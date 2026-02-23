from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv
import os
import time
import threading

# ================= CONFIG =================
SYMBOL = "XAUUSDT"
LEVERAGE = 50

# ================= ENV =================
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

app = Flask(__name__)
client = None

# ================= SAFE INIT =================
def init_binance():
    global client
    while True:
        try:
            c = Client(API_KEY, API_SECRET, {"timeout": 20})

            # sync time
            server_time = c.get_server_time()["serverTime"]
            local_time = int(time.time() * 1000)
            c.timestamp_offset = server_time - local_time

            # set leverage once
            c.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

            client = c
            print("✅ Binance connected (One-way mode)")
            break

        except Exception as e:
            print("⚠ Init failed:", e)
            print("⏳ Retry in 60 sec...")
            time.sleep(60)

threading.Thread(target=init_binance, daemon=True).start()

# ================= DUPLICATE =================
processed_ids = set()
lock = threading.Lock()

def is_duplicate(order_id):
    with lock:
        if order_id in processed_ids:
            return True
        processed_ids.add(order_id)
        if len(processed_ids) > 500:
            processed_ids.clear()
        return False

# ================= POSITION =================
def get_position_amt():
    positions = client.futures_position_information(symbol=SYMBOL)
    for p in positions:
        amt = float(p["positionAmt"])
        if amt != 0:
            return amt
    return 0

# ================= WEBHOOK =================
@app.route("/webhook", methods=["POST"])
def webhook():
    global client

    if client is None:
        return jsonify({"error": "binance not ready"}), 503

    try:
        data = request.get_json(force=True)

        if not data:
            return jsonify({"error": "invalid json"}), 400

        print("📩 Received:", data)

        order_id = data.get("id")
        action = data.get("action")

        if not order_id:
            return jsonify({"error": "missing id"}), 400

        if is_duplicate(order_id):
            return jsonify({"status": "duplicate ignored"})

        # ===== OPEN =====
        if action == "OPEN":

            side = data.get("side")
            qty = data.get("amount")

            if side not in ["BUY", "SELL"]:
                return jsonify({"error": "invalid side"}), 400

            if qty is None:
                return jsonify({"error": "missing amount"}), 400

            qty = float(qty)

            order_side = SIDE_BUY if side == "BUY" else SIDE_SELL

            client.futures_create_order(
                symbol=SYMBOL,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )

            return jsonify({"status": "opened"})

        # ===== CLOSE =====
        if action == "CLOSE":

            amt = get_position_amt()

            if amt == 0:
                return jsonify({"status": "no position"})

            close_side = SIDE_SELL if amt > 0 else SIDE_BUY

            client.futures_create_order(
                symbol=SYMBOL,
                side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=abs(amt),
                reduceOnly=True
            )

            return jsonify({"status": "closed"})

        return jsonify({"error": "invalid action"}), 400

    except Exception as e:
        print("❌ ERROR:", e)
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
