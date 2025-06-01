import os
import json
import time
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import gspread # Make sure this is installed: pip install gspread
from oauth2client.service_account import ServiceAccountCredentials # pip install oauth2client

app = Flask(__name__)

# --- Binance API Configuration ---
# Get API keys from environment variables
api_key = os.environ.get('BINANCE_API_KEY')
api_secret = os.environ.get('BINANCE_API_SECRET')

client = None
if api_key and api_secret:
    try:
        client = Client(api_key, api_secret)
        print("Binance client initialized successfully.")
    except Exception as e:
        print(f"Error initializing Binance client: {e}")
        client = None
else:
    print("Binance API keys not found in environment variables. Trading functions will be disabled.")

# --- Google Sheet Configuration ---
# Make sure your Google Sheet credentials JSON is set as an environment variable
# 'GOOGLE_SHEET_CREDENTIALS' in Render, or 'GOOGLE_APPLICATION_CREDENTIALS' in local setup
google_sheet_initialized = False
sheet = None

# For Render deployment, use GOOGLE_SHEET_CREDENTIALS env var
if os.environ.get('GOOGLE_SHEET_CREDENTIALS'):
    try:
        creds_json_str = os.environ.get('GOOGLE_SHEET_CREDENTIALS')
        creds_json = json.loads(creds_json_str)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
        gc = gspread.authorize(creds)
        sheet = gc.open("Binance Trading Bot Log").sheet1 # Replace "Binance Trading Bot Log" with your actual Google Sheet name
        google_sheet_initialized = True
        print("Google Sheet initialized successfully.")
    except Exception as e:
        print(f"Error initializing Google Sheet: {e}")
        google_sheet_initialized = False
else:
    print("Google Sheet credentials not found in environment variables. Logging to Google Sheet will be disabled.")

# --- Trading Logic (simplified for example) ---
def place_order(signal_type, symbol, price, order_size_usd, sl_price):
    if not client:
        print(f"Binance client not initialized. Cannot place order for {symbol}.")
        return False

    try:
        # Get current ticker price to calculate quantity based on USD value
        ticker = client.get_ticker(symbol=symbol)
        current_price = float(ticker['lastPrice'])

        # Calculate quantity (assuming market order or very close to current price)
        # Adjust based on your symbol's precision
        # Example: quantity = order_size_usd / current_price
        # You need to implement proper quantity calculation and precision handling
        # This is a placeholder
        quantity = order_size_usd / current_price 
        
        # Get symbol info to determine price and quantity precision
        info = client.get_symbol_info(symbol)
        min_notional = float([f['minNotional'] for f in info['filters'] if f['filterType'] == 'MIN_NOTIONAL'][0])
        step_size = float([f['stepSize'] for f in info['filters'] if f['filterType'] == 'LOT_SIZE'][0])
        tick_size = float([f['tickSize'] for f in info['filters'] if f['filterType'] == 'PRICE_FILTER'][0])

        # Apply quantity precision
        quantity = client.quantize_quantity(quantity, step_size)
        
        # Check minNotional
        if quantity * current_price < min_notional:
            print(f"Calculated quantity {quantity} * {current_price} is below minNotional {min_notional} for {symbol}. Adjusting quantity...")
            # You might need to adjust quantity up to meet minNotional or handle this case
            # For simplicity, returning False if cannot meet minNotional with current logic
            return False

        print(f"Attempting to place {signal_type} order for {quantity} {symbol} at price {current_price} USD_value: {order_size_usd}")

        order = None
        if signal_type == 'BUY':
            order = client.create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
        elif signal_type == 'SELL':
            order = client.create_order(
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
            # This ensures that ping requests don't trigger trading logic or errors.
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
                    # Make sure the sheet name in gc.open("YOUR SHEET NAME") is correct
                    # and the sheet has columns for this data
                    sheet.append_row([timestamp, signal_type, symbol, price, order_size_usd, sl_price, json.dumps(data)])
                    print(f"Signal logged to Google Sheet: {signal_type} {symbol}")
                except Exception as e:
                    print(f"Error logging to Google Sheet: {e}")
                    # If Google Sheet logging fails, it should not stop trading
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
    # Use environment variable for port, default to 5000 for local testing
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
    print("Flask app is starting...") # This line will likely not be seen on Render directly if Gunicorn is used