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
client.FUTURES_URL = "https://fapi.binance.com/fapi"

# ================= TIME SYNC =================
def sync_time():
    try:
        server_time = client.get_server_time()["serverTime"]
        local_time = int(time.time() * 1000)
        client.timestamp_offset = server_time - local_time
        print("🕒 Time synced")
    except Exception as e:
        print("Time sync error:", e)

sync_time()

# sync ทุก 30 นาที
def auto_sync():
    while True:
        time.sleep(1800)
        sync_time()

threading.Thread(target=auto_sync, daemon=True).start()

# ================= APP =================
app = Flask(__name__)

# ================= DUPLICATE PROTECTION =================
last_signal_id = None

# ================= UTILS =================
def get_position(symbol):
    positions = client.futures_position_information(symbol=symbol)
    for p in positions:
        if abs(float(p["positionAmt"])) > 0:
            return p
    return None

# ================= WEBHOOK =================
@app.route("/webhook", methods=["POST"])
def webhook():
    global last_signal_id

    data = request.json
    print("📩 Received:", data)

    try:
        # ===== DUPLICATE CHECK =====
        signal_id = data.get("id")
        if signal_id == last_signal_id:
            print("⚠️ Duplicate signal ignored")
            return jsonify({"status": "duplicate ignored"})
        last_signal_id = signal_id

        action = data.get("action")
        symbol = data.get("symbol")
        side = data.get("side")

        if not action or not symbol:
            return jsonify({"error": "invalid payload"}), 400

        # ================= CLOSE =================
        if action == "CLOSE":
            pos = get_position(symbol)
            if not pos:
                return jsonify({"status": "no position"})

            qty = abs(float(pos["positionAmt"]))
            close_side = SIDE_SELL if float(pos["positionAmt"]) > 0 else SIDE_BUY

            order = client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )

            return jsonify({"status": "closed", "orderId": order["orderId"]})

        # ================= OPEN =================
        if action == "OPEN":
            qty = float(data.get("amount"))
            leverage = int(data.get("leverage", 10))

            # set leverage (จะ call เฉพาะตอนเปิด)
            client.futures_change_leverage(
                symbol=symbol,
                leverage=leverage
            )

            order_side = SIDE_BUY if side == "BUY" else SIDE_SELL

            order = client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )

            return jsonify({"status": "opened", "orderId": order["orderId"]})

        return jsonify({"error": "unknown action"}), 400

    except Exception as e:
        print("❌ ERROR TYPE:", type(e))
        print("❌ ERROR DETAIL:", e)
        return jsonify({"error": str(e)}), 400


# ================= RUN =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
