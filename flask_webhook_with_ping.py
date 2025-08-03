
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os

app = Flask(__name__)

# Binance Testnet Credentials
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)
client.API_URL = 'https://testnet.binancefuture.com/fapi'

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
    side = data.get("side", "BUY").upper()

    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        mark_price_data = client.futures_mark_price(symbol=symbol)
        mark_price = float(mark_price_data["markPrice"])
        quantity = round((amount * leverage) / mark_price, 3)

        if signal == "buy":
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=quantity,
                positionSide="LONG"
            )
            print(f"✅ Buy Order Placed: {order}")

        elif signal == "sell":
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=quantity,
                positionSide="SHORT"
            )
            print(f"✅ Sell Order Placed: {order}")

        elif signal == "close":
            position_side = "LONG" if side == "BUY" else "SHORT"
            direction = SIDE_SELL if side == "BUY" else SIDE_BUY
            order = client.futures_create_order(
                symbol=symbol,
                side=direction,
                type=ORDER_TYPE_MARKET,
                quantity=quantity,
                reduceOnly=True,
                positionSide=position_side
            )
            print(f"✅ Partial Close Executed ({side}): {quantity} contracts")

        else:
            print("❌ Unknown signal received.")
            return jsonify({"status": "error", "message": "Invalid signal"}), 400

        return jsonify({"status": "success", "message": f"Executed {signal}"}), 200

    except Exception as e:
        print(f"❌ Error executing order: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
