import os
import json
import time
import gspread
from binance.client import Client
from binance.enums import *
import decimal

# --- Flask for Webhook ---
from flask import Flask, request, jsonify
app = Flask(__name__)

# --- Environment Variables / Replit Secrets ---
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET')
GOOGLE_SHEET_NAME = os.environ.get('GOOGLE_SHEET_NAME', 'TradingBot_Signals') # Default name if not set

# --- Initialization Flags and Global Variables ---
binance_client_initialized = False
google_sheet_initialized = False
client = None
gc = None
sheet = None

# --- Initialize Binance Client ---
def initialize_binance_client():
    global client, binance_client_initialized
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        print("Error: BINANCE_API_KEY or BINANCE_API_SECRET not found. Please set them in your environment variables.")
        return False
    try:
        # Use Testnet for testing, remove 't' for production
        client = Client(BINANCE_API_KEY, BINANCE_API_SECRET, tld='us') # Change tld to 'com' for mainnet, 'us' for US, etc.
        # For Testnet, you might need to set tld='testnet' or use a specific base_url
        # Example for Binance Futures Testnet:
        # client = Client(BINANCE_API_KEY, BINANCE_API_SECRET, {"base_url": "https://testnet.binancefuture.com/fapi"})
        # Please confirm the correct base_url for Binance Futures Testnet
        # Based on previous logs, the default client might work if keys/permissions are correct.
        print("Binance client initialized successfully.")
        binance_client_initialized = True
        return True
    except Exception as e:
        print(f"Error initializing Binance client: {e}")
        return False

# --- Initialize Google Sheet ---
def initialize_google_sheet():
    global gc, sheet, google_sheet_initialized
    if google_sheet_initialized:
        return True

    google_creds_json = os.environ.get('GOOGLE_CREDS_JSON')

    if not google_creds_json:
        print("Error: GOOGLE_CREDS_JSON environment variable not found. Google Sheet will not be accessible.")
        return False

    try:
        creds_dict = json.loads(google_creds_json)
        gc = gspread.service_account_from_dict(creds_dict)
        sheet = gc.open(GOOGLE_SHEET_NAME).sheet1 # Assumes you want the first sheet
        print("Google Sheet initialized successfully.")
        google_sheet_initialized = True
        return True
    except Exception as e:
        print(f"Error initializing Google Sheet: {e}")
        print("Please ensure GOOGLE_CREDS_JSON is a valid JSON string of your service account credentials.")
        return False

# --- Place Order Function ---
def place_order(signal_type, symbol, price, order_size_usd, sl_price):
    global client
    if not client:
        print("Binance client not initialized. Cannot place order.")
        return False

    # Define leverage and position side (from your requirements)
    LEVERAGE = 125 # Your specified leverage
    # Make sure your account supports this leverage
    # You might need to set leverage and margin mode first if not already done
    try:
        # Set leverage (only needs to be done once per symbol or if you change it)
        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
        print(f"Leverage set to {LEVERAGE} for {symbol}.")

        # Set margin mode to isolated (if you prefer, default is cross)
        # client.futures_change_margin_type(symbol=symbol, marginType='ISOLATED')
        # print(f"Margin type set to ISOLATED for {symbol}.")

    except Exception as e:
        print(f"Error setting leverage/margin type for {symbol}: {e}")
        # Depending on the error, you might want to return False or continue
        # For -4007 (ex: isolated_margin_not_enabled), it might proceed anyway if cross is okay
        # For -4014 (ex: leverage too high), it's critical.
        # Let's return False for now to be safe.
        return False


    # Get current price for calculating quantity and for market orders if price is 0
    try:
        ticker_info = client.futures_symbol_ticker(symbol=symbol)
        current_price = float(ticker_info['price'])
        print(f"Current price for {symbol}: {current_price}")
    except Exception as e:
        print(f"Error fetching current price for {symbol}: {e}")
        return False

    # Use current_price if signal price is invalid (e.g., 0 due to parsing error)
    # or if you intend to always use market price for order placement
    if price <= 0: # If price was invalid from webhook, use current market price
        trade_price = current_price
        print(f"Signal price invalid or zero, using current market price {trade_price} for trade.")
    else:
        trade_price = price

    # Calculate quantity
    # qty = (order_size_usd / trade_price)
    # Adjust for Binance Futures minimum quantity (lot size).
    # You need to fetch symbol info to get stepSize for quantity.
    try:
        exchange_info = client.futures_exchange_info()
        symbol_info = next(s for s in exchange_info['symbols'] if s['symbol'] == symbol)
        
        # Get lot size / step size for the symbol
        step_size = 0.0
        for f in symbol_info['filters']:
            if f['filterType'] == 'MARKET_LOT_SIZE' or f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                break
        
        if step_size == 0.0:
            print(f"Error: Could not find stepSize for {symbol}.")
            return False

        # Calculate raw quantity
        raw_qty = decimal.Decimal(str(order_size_usd)) / decimal.Decimal(str(trade_price))
        
        # Adjust quantity to fit step_size
        # The quantization logic from Binance's API docs usually involves
        # dividing by step_size, truncating, then multiplying by step_size
        qty = (raw_qty // decimal.Decimal(str(step_size))) * decimal.Decimal(str(step_size))
        
        # Convert to float for printing if needed, but keep as Decimal for precision
        qty_float = float(qty)

        # Ensure qty is positive and not zero
        if qty <= 0:
            print(f"Error: Calculated quantity {qty_float} is zero or negative. Order not placed.")
            return False

        print(f"Calculated quantity to trade: {qty_float}")

    except Exception as e:
        print(f"Error calculating quantity for {symbol}: {e}")
        return False
    
    # Define order side and position side
    side = None
    position_side = None
    if signal_type.lower() == 'buy':
        side = SIDE_BUY
        position_side = 'LONG' # For futures
    elif signal_type.lower() == 'sell':
        side = SIDE_SELL
        position_side = 'SHORT' # For futures
    else:
        print(f"Invalid signal type: {signal_type}")
        return False

    # Place primary order (Market order is simplest for quick execution)
    try:
        # Use a market order for simplicity and to ensure execution
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            positionSide=position_side,
            type=ORDER_TYPE_MARKET, # Use MARKET order type
            quantity=qty_float
        )
        print(f"Successfully placed primary {signal_type} order for {qty_float} {symbol}: {order}")

        # Place Stop Loss Order (OCO not directly available for Futures API for position SL, use STOP_MARKET)
        if sl_price is not None and sl_price > 0: # Ensure SL price is valid
            try:
                # Determine stop price type (e.g., if buy, SL is below entry, if sell, SL is above entry)
                sl_order_side = SIDE_SELL if signal_type.lower() == 'buy' else SIDE_BUY
                
                # Place a STOP_MARKET order as the stop loss
                sl_order = client.futures_create_order(
                    symbol=symbol,
                    side=sl_order_side,
                    positionSide=position_side,
                    type=ORDER_TYPE_STOP_MARKET,
                    quantity=qty_float,
                    stopPrice=sl_price, # The price at which the stop order is triggered
                    closePosition=True # Close the entire position if triggered
                )
                print(f"Successfully placed Stop Loss order at {sl_price}: {sl_order}")
            except Exception as e:
                print(f"Error placing Stop Loss order at {sl_price}: {e}")
                # Log error, but primary order might still be active
        else:
            print("No valid Stop Loss price provided or SL price is zero, skipping SL order.")

        return True
    except Exception as e:
        print(f"Error placing primary order: {e}")
        return False

# --- Webhook Listener ---
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == 'POST':
        try:
            data = request.get_json()
            if not data:
                return jsonify({"status": "error", "message": "Request must be JSON"}), 400

            # --- Handle Keep-Alive Signal ---
            alert_type = data.get('Type') # Check for the 'Type' key from Pine Script
            if alert_type == 'KeepAlive':
                print(f"Received Keep-Alive signal: {data.get('Timestamp')}. Keeping service alive for {data.get('Symbol')}.")
                return jsonify({"status": "success", "message": "Keep-Alive signal received."}), 200

            # --- Process Actual Trading Signal ---
            print(f"Received webhook: {json.dumps(data, indent=2)}")

            timestamp_str = data.get('Timestamp')
            signal_type = data.get('Signal Type')
            symbol = data.get('Symbol')
            price_str = data.get('Price')
            order_size_usd_str = data.get('Order Size USD')
            sl_price_str = data.get('SL Price')

            # Validate essential data for trading signal
            if not all([signal_type, symbol, price_str, order_size_usd_str]):
                print("Error: Missing essential data for trading signal.")
                return jsonify({"status": "error", "message": "Missing essential data for trading signal"}), 400

            # Remove commas and convert to float
            price = 0.0
            if price_str:
                try:
                    price = float(price_str.replace(',', ''))
                except ValueError:
                    print(f"Warning: Could not convert Price '{price_str}' to float. Setting to 0.")
                    price = 0.0

            order_size_usd = 0.0
            if order_size_usd_str:
                try:
                    order_size_usd = float(order_size_usd_str)
                except ValueError:
                    print(f"Warning: Could not convert Order Size USD '{order_size_usd_str}' to float. Setting to 0.")
                    order_size_usd = 0.0

            sl_price = None
            if sl_price_str:
                try:
                    sl_price = float(sl_price_str.replace(',', ''))
                except ValueError:
                    print(f"Warning: Could not convert SL Price '{sl_price_str}' to float. Setting to None.")
                    sl_price = None

            print(f"Parsed values: Signal Type={signal_type}, Symbol={symbol}, Price={price}, Order Size USD={order_size_usd}, SL Price={sl_price}")

            # Initialize Binance client and Google Sheet if not already
            if not binance_client_initialized:
                initialize_binance_client()
            if not google_sheet_initialized:
                initialize_google_sheet()

            # Log to Google Sheet
            if google_sheet_initialized and sheet:
                try:
                    row_data = [timestamp_str, signal_type, symbol, price, order_size_usd, sl_price, json.dumps(data)]
                    sheet.append_row(row_data)
                    print("Signal logged to Google Sheet.")
                except Exception as e:
                    print(f"Error logging to Google Sheet: {e}")
            else:
                print("Google Sheet not initialized, skipping log.")

            # Process and place order (only if it's a valid trading signal)
            # Ensure price is valid (not 0 from parsing error) before placing order
            if signal_type and symbol and order_size_usd > 0 and price > 0:
                print(f"Processing signal: Type={signal_type}, Symbol={symbol}, Price={price}, OrderSizeUSD={order_size_usd}, SLPrice={sl_price}")
                order_success = place_order(signal_type, symbol, price, order_size_usd, sl_price)
                if order_success:
                    return jsonify({"status": "success", "message": "Signal processed and order placed"}), 200
                else:
                    return jsonify({"status": "error", "message": "Failed to place order"}), 500
            else:
                # Log if not a valid trading signal for order placement
                print(f"Invalid signal for order placement: Type={signal_type}, Symbol={symbol}, Price={price}, OrderSizeUSD={order_size_usd}. Skipping order.")
                return jsonify({"status": "error", "message": "Invalid signal data for order placement. Skipping order."}), 400

        except Exception as e:
            print(f"Error processing webhook: {e}")
            return jsonify({"status": "error", "message": f"Internal server error: {e}"}), 500
    return jsonify({"status": "error", "message": "Method Not Allowed"}), 405

# --- Health Check Endpoint (Optional but recommended) ---
@app.route('/')
def home():
    return "TradingBot Webhook Listener is running!", 200

# --- Run the Flask App ---
if __name__ == '__main__':
    # Use Gunicorn in production environments like Render for better performance and reliability
    # Render's Procfile will handle this.
    # For local testing, you can run app.run()
    # app.run(host='0.0.0.0', port=os.environ.get('PORT', 5000)) # Replit uses PORT env var
    print("Flask app is starting...")
    # Render will automatically start the app using the Procfile, so no need for app.run() here in the final deployment.
    # Keep it for local testing if you want, but ensure your Procfile overrides it.