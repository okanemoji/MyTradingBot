from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os
import json

app = Flask(__name__)

# === Binance Testnet Credentials ===
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)
client.API_URL = 'https://testnet.binancefuture.com/fapi'

# === Global Settings ===
symbol = "BTCUSDT"

@app.route("/", methods=["GET"])
def home():
    return "Binance Webhook Bot Running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        # ✅ รองรับทั้ง JSON (application/json) และ Text (text/plain)
        if request.is_json:
            data = request.get_json()
        else:
            raw_data = request.data.decode("utf-8")
            data = json.loads(raw_data)

        print(f"Received Webhook: {data}")

        # ✅ Ping handler
        if data.get("type") == "ping":
            print("🔵 Keep-alive ping received.")
            return jsonify({"status": "ok", "message": "Ping received"}), 200

        # ✅ Main signal handling
        signal = data.get("signal")
        leverage = data.get("leverage", 125)
        amount = data.get("amount", 100)

        print(f"🟢 Signal: {signal} | Leverage: {leverage} | Amount: {amount}")

        # Set leverage
        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        # Get current price
        mark_price_data = client.futures_mark_price(symbol=symbol)
        mark_price = float(mark_price_data["markPrice"])
        quantity = round((amount * leverage) / mark_price, 3)

        if signal == "buy":
            close_position("SELL")
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            print(f"✅ Buy Order: {order}")
        elif signal == "sell":
            close_position("BUY")
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            print(f"✅ Sell Order: {order}")
        elif signal == "close":
            close_position("BUY")
            close_position("SELL")
            print("✅ Closed all positions")
        else:
            print("❌ Unknown signal received.")
            return jsonify({"status": "error", "message": "Invalid signal"}), 400

        return jsonify({"status": "success", "message": f"Executed {signal}"}), 200

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

def close_position(side_to_close):
    try:
        pos = client.futures_position_information(symbol=symbol)
        for p in pos:
            amt = float(p['positionAmt'])
            if amt != 0:
                side = SIDE_BUY if amt < 0 else SIDE_SELL
                if side == side_to_close:
                    qty = abs(amt)
                    client.futures_create_order(
                        symbol=symbol,
                        side=side,
                        type=ORDER_TYPE_MARKET,
                        quantity=round(qty, 3),
                        reduceOnly=True
                    )
                    print(f"🔁 Closed {side} position with qty {qty}")
    except Exception as e:
        print(f"⚠️ Error closing position: {str(e)}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
