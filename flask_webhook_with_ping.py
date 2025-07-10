from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os

app = Flask(__name__)

# === Binance Credentials (Production) ===
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)

# === Global Settings ===
symbol = "BTCUSDT"

@app.route("/", methods=["GET"])
def home():
    return "Binance Webhook Bot Running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print(f"Received Webhook: {data}")

    # ✅ Handle ping
    if data.get("type") == "ping":
        print("🔵 Keep-alive ping received.")
        return jsonify({"status": "ok", "message": "Ping received"}), 200

    signal = data.get("signal")
    leverage = int(data.get("leverage", 125))
    amount = float(data.get("amount", 100))

    try:
        # Set leverage
        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        # Get mark price
        mark_price_data = client.futures_mark_price(symbol=symbol)
        mark_price = float(mark_price_data["markPrice"])

        # Calculate quantity
        raw_qty = (amount * leverage) / mark_price

        # Round to step size 0.001
        quantity = max(round(raw_qty / 0.001) * 0.001, 0.001)

        print(f"Calculated quantity: {quantity}")

        if signal == "buy":
            close_position("SELL")
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            print(f"✅ Buy order placed: {order}")

        elif signal == "sell":
            close_position("BUY")
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            print(f"✅ Sell order placed: {order}")

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
        positions = client.futures_position_information(symbol=symbol)
        for p in positions:
            amt = float(p["positionAmt"])
            if amt != 0:
                side = SIDE_BUY if amt < 0 else SIDE_SELL
                if side == side_to_close:
                    qty = abs(amt)
                    qty = max(round(qty / 0.001) * 0.001, 0.001)  # Ensure step size
                    client.futures_create_order(
                        symbol=symbol,
                        side=side,
                        type=ORDER_TYPE_MARKET,
                        quantity=qty,
                        reduceOnly=True
                    )
                    print(f"🔁 Closed {side} position qty {qty}")
    except Exception as e:
        print(f"⚠️ Error closing positions: {str(e)}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
