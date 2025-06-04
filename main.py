import os
import json
import time
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# --- Binance API Configuration ---
# Get API keys from environment variables
api_key = os.environ.get('BINANCE_API_KEY')
api_secret = os.environ.get('BINANCE_API_SECRET')

client = None
if api_key and api_secret:
    try:
        # *** แก้ไข: ใช้ testnet=True แทน base_url ***
        client = Client(api_key, api_secret, testnet=True)
        print("Binance Futures Testnet client initialized successfully using testnet=True.")
    except Exception as e:
        print(f"Error initializing Binance client: {e}")
        client = None
else:
    print("Binance API keys not found in environment variables. Trading functions will be disabled.")

# --- Google Sheet Configuration ---
google_sheet_initialized = False
sheet = None

if os.environ.get('GOOGLE_SHEET_CREDENTIALS'):
    try:
        creds_json_str = os.environ.get('GOOGLE_SHEET_CREDENTIALS')
        creds_json = json.loads(creds_json_str)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
        gc = gspread.authorize(creds)
        sheet = gc.open("TradingBot_Signals").sheet1 # Replace "Binance Trading Bot Log" with your actual Google Sheet name
        google_sheet_initialized = True
        print("Google Sheet initialized successfully.")
    except Exception as e:
        print(f"Error initializing Google Sheet: {e}")
        google_sheet_initialized = False
else:
    print("Google Sheet credentials not found in environment variables. Logging to Google Sheet will be disabled.")

# --- Trading Logic ---
def place_order(signal_type, symbol, price, order_size_usd, sl_price):
    if not client:
        print(f"Binance client not initialized. Cannot place order for {symbol}.")
        return False

    try:
        # ใช้ futures_mark_price สำหรับราคา Futures
        ticker = client.futures_mark_price(symbol=symbol)
        current_price = float(ticker['markPrice'])
        
        # ใช้ futures_exchange_info เพื่อดึงข้อมูล Futures Symbol
        exchange_info = client.futures_exchange_info()
        symbol_info = next((item for item in exchange_info['symbols'] if item['symbol'] == symbol), None)
        
        if not symbol_info:
            print(f"Error: Symbol {symbol} not found in Futures exchange info.")
            return False

        # ดึง filters ที่ถูกต้องสำหรับ Futures
        min_notional_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'MIN_NOTIONAL'), None)
        # ตรวจสอบ filterType สำหรับ step_size ใน Futures (อาจเป็น LOT_SIZE หรือ MARKET_LOT_SIZE)
        # ลองใช้ MARKET_LOT_SIZE ก่อน หากยัง error อาจจะต้องลอง LOT_SIZE หรือดูจาก docs ของ Binance Futures
        lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'MARKET_LOT_SIZE'), None) 

        if not min_notional_filter or not lot_size_filter:
            print(f"Error: Could not find MIN_NOTIONAL or MARKET_LOT_SIZE filter for {symbol}.")
            return False

        min_notional = float(min_notional_filter['minNotional'])
        step_size = float(lot_size_filter['stepSize'])
        
        # Calculate quantity
        quantity = order_size_usd / current_price 
        
        # Apply quantity precision
        quantity = client.quantize_quantity(quantity, step_size)
        
        # Check minNotional
        if quantity * current_price < min_notional:
            print(f"Calculated quantity {quantity} * {current_price} is below minNotional {min_notional} for {symbol}. Cannot place order.")
            return False

        print(f"Attempting to place {signal_type} order for {quantity} {symbol} at price {current_price} USD_value: {order_size_usd}")

        order = None
        # ใช้ client.futures_create_order สำหรับ Futures
        if signal_type == 'BUY':
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
        elif signal_type == 'SELL':
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
        
        if order:
            print(f"Order placed successfully: {order}")
            return True
        else:
            print("Order object is None.")
            return False

    except Exception as e:
        print(f"Error placing order for {symbol}: {e}")
        return False

# --- Webhook Endpoint ---
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == 'POST':
        try:
            data = request.get_json()
            if data is None:
                print("Received empty or non-JSON webhook data.")
                return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

            print(f"Received webhook data: {data}")

            # --- IMPORTANT: Check for ping signal FIRST ---
            if data.get('type') == 'ping':
                timestamp_ping = data.get('timestamp', time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()))
                print(f"Received Keep-alive ping from TradingView at {timestamp_ping}.")
                return jsonify({"status": "pong", "message": "Keep-alive ping received"}), 200
            # --- END Ping Check ---

            # If it's not a ping, process as a trading signal
            signal_type = data.get('Signal Type')
            symbol = data.get('Symbol')
            price = float(data.get('Price')) if data.get('Price') else 0
            order_size_usd = float(data.get('Order Size USD')) if data.get('Order Size USD') else 0
            sl_price = float(data.get('SL Price')) if data.get('SL Price') else 0
            timestamp = data.get('Timestamp', time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()))

            # Validate essential trading signal data
            if not all([signal_type, symbol, order_size_usd > 0]):
                print(f"Error: Missing essential data for trading signal. Received: {data}")
                return jsonify({"status": "error", "message": "Missing essential data for trading signal."}), 400

            # Log to Google Sheet
            if google_sheet_initialized:
                try:
                    sheet.append_row([timestamp, signal_type, symbol, price, order_size_usd, sl_price, json.dumps(data)])
                    print(f"Signal logged to Google Sheet: {signal_type} {symbol}")
                except Exception as e:
                    print(f"Error logging to Google Sheet: {e}")
            else:
                print("Google Sheet not initialized, skipping log.")

            # Place order on Binance
            order_success = place_order(signal_type, symbol, price, order_size_usd, sl_price)
            if order_success:
                return jsonify({"status": "success", "message": "Signal processed and order placed"}), 200
            else:
                return jsonify({"status": "error", "message": "Failed to place order"}), 500

        except json.JSONDecodeError:
            print("Received non-JSON data or malformed JSON.")
            return jsonify({"status": "error", "message": "Invalid JSON format"}), 400
        except Exception as e:
            print(f"Unhandled error processing webhook: {e}")
            return jsonify({"status": "error", "message": f"Internal server error: {e}"}), 500
    return jsonify({"status": "error", "message": "Method Not Allowed"}), 405

# --- Health Check Endpoint (Optional but Recommended for Render) ---
@app.route('/', methods=['GET'])
def health_check():
    return "Flask app is running!", 200

# --- Main entry point for Flask ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
    print("Flask app is starting...")