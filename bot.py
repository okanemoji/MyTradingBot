from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv
import os
import time
import threading

# ===== ENV =====
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

# ===== SYNC TIME (ครั้งเดียวพอ) =====
server_time = client.get_server_time()["serverTime"]
local_time = int(time.time() * 1000)
client.timestamp_offset = server_time - local_time

app = Flask(__name__)

# ===== DUPLICATE PROTECTION =====
processed_ids = set()
lock = threading.Lock()

def is_duplicate(order_id):
    with lock:
        if order_id in processed_ids:
            return True
        processed_ids.add(order_id)

        if len(processed_ids) > 1000:
            processed_ids.clear()

        return False

# ===== TRACK LEVERAGE PER SYMBOL =====
symbol_leverage = {}

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    try:
        order_id = data.get("id")
        if not order_id:
            return jsonify({"error": "missing id"}), 400

        if is_duplicate(order_id):
            return jsonify({"status": "duplicate ignored"})

        action = data["action"]
        symbol = data["symbol"]
        side = data["side"]

        position_side = "LONG" if side == "BUY" else "SHORT"
        order_side = SIDE_BUY if side == "BUY" else SIDE_SELL

        # ===== OPEN =====
        if action == "OPEN":
            amount = float(data["amount"])
            leverage = int(data["leverage"])

            # เปลี่ยน leverage เฉพาะตอนยังไม่เคยตั้ง
            if symbol not in symbol_leverage:
                client.futures_change_leverage(symbol=symbol, leverage=leverage)
                symbol_leverage[symbol] = leverage

            client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=amount,
                positionSide=position_side,
                newClientOrderId=order_id,
                recvWindow=5000
            )

            return jsonify({"status": "opened"})

        # ===== CLOSE (ไม่ query position) =====
        if action == "CLOSE":

            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL if side == "BUY" else SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=float(data.get("amount", 0)),  # optional
                positionSide=position_side,
                reduceOnly=True,
                newClientOrderId=order_id,
                recvWindow=5000
            )

            return jsonify({"status": "closed"})

        return jsonify({"error": "invalid action"})

   except Exception as e:
    print("ERROR >>>", e)
    print("DATA >>>", data)
    return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
