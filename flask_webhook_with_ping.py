from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os

app = Flask(__name__)

# === Binance Credentials ===
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)

# client.API_URL = 'https://testnet.binancefuture.com/fapi'  # Uncomment if using testnet

symbol = "BTCUSDT"

@app.route("/", methods=["GET"])
def home():
    return "Binance Webhook Bot Running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print(f"Received Webhook: {data}")

    if data.get("type") == "ping":
        print("🔵 Keep-alive ping received.")
        return jsonify({"status": "ok", "message": "Ping received"}), 200

    signal = data.get("signal")
    leverage = data.get("leverage", 125)
    amount = data.get("amount", 100)

    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        mark_price_data = client.futures_mark_price(symbol=symbol)
        mark_price = float(mark_price_data["markPrice"])
        quantity = round((amount * leverage) / mark_price, 6)

        if signal == "buy":
            close_all_positions()
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                positionSide="LONG",
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            print(f"✅ Buy Order Placed: {order}")

        elif signal == "sell":
            close_all_positions()
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                positionSide="SHORT",
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            print(f"✅ Sell Order Placed: {order}")

        elif signal == "close":
            close_all_positions()
            print("✅ All positions closed.")

        else:
            print("❌ Unknown signal received.")
            return jsonify({"status": "error", "message": "Invalid signal"}), 400

        return jsonify({"status": "success", "message": f"Executed {signal}"}), 200

    except Exception as e:
        print(f"❌ Error executing order: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

def close_all_positions():
    try:
        positions = client.futures_position_information(symbol=symbol)
        for pos in positions:
            amt = float(pos['positionAmt'])
            if amt == 0:
                continue

            side = SIDE_BUY if amt < 0 else SIDE_SELL
            pos_side = pos['positionSide']  # LONG or SHORT
            qty = abs(amt)

            client.futures_create_order(
                symbol=symbol,
                side=side,
                positionSide=pos_side,
                type=ORDER_TYPE_MARKET,
                quantity=round(qty, 6),
                reduceOnly=True
            )
            print(f"🔁 Closed {pos_side} ({side}) position qty {qty}")
    except Exception as e:
        print(f"⚠️ Error closing positions: {str(e)}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
