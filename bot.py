from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv
import os
import time
import threading

# ================= ENV =================
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# ================= CLIENT =================
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

# ===== SYNC TIME =====
server_time = client.get_server_time()["serverTime"]
local_time = int(time.time() * 1000)
client.timestamp_offset = server_time - local_time

app = Flask(__name__)

# ================= DUPLICATE PROTECTION =================
processed_ids = set()
lock = threading.Lock()

def is_duplicate(order_id):
    with lock:
        if order_id in processed_ids:
            return True
        processed_ids.add(order_id)

        # จำกัด memory ไม่ให้โตเกิน
        if len(processed_ids) > 500:
            processed_ids.clear()

        return False

# ================= UTILS =================
def get_position(symbol, position_side):
    positions = client.futures_position_information(symbol=symbol)
    for p in positions:
        if p["positionSide"] == position_side and abs(float(p["positionAmt"])) > 0:
            return p
    return None

# ================= WEBHOOK =================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("📩 Received:", data)

    try:
        order_id = data.get("id")
        if not order_id:
            return jsonify({"error": "missing id"}), 400

        if is_duplicate(order_id):
            print("⚠ Duplicate blocked:", order_id)
            return jsonify({"status": "duplicate ignored"})

        action = data.get("action")
        symbol = data["symbol"]

        # ===== CLOSE =====
        if action == "CLOSE":
            side = data["side"]
            position_side = "LONG" if side == "BUY" else "SHORT"
            close_side = SIDE_SELL if side == "BUY" else SIDE_BUY

            pos = get_position(symbol, position_side)
            if not pos:
                return jsonify({"status": "no position"})

            qty = abs(float(pos["positionAmt"]))

            order = client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                positionSide=position_side,
                newClientOrderId=order_id
            )

            return jsonify({"status": "closed"})

        # ===== OPEN =====
        if action == "OPEN":
            side = data["side"]
            amount = float(data["amount"])
            leverage = int(data["leverage"])

            position_side = "LONG" if side == "BUY" else "SHORT"
            order_side = SIDE_BUY if side == "BUY" else SIDE_SELL

            client.futures_change_leverage(symbol=symbol, leverage=leverage)

            order = client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=amount,
                positionSide=position_side,
                newClientOrderId=order_id
            )

            return jsonify({"status": "opened"})

        return jsonify({"error": "invalid action"})

    except Exception as e:
        print("❌ ERROR:", e)
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
