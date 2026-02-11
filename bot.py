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

def auto_sync():
    while True:
        time.sleep(1800)  # sync ทุก 30 นาที
        sync_time()

threading.Thread(target=auto_sync, daemon=True).start()

# ================= SET LEVERAGE ON START =================
DEFAULT_SYMBOL = "XPTUSDT"
DEFAULT_LEVERAGE = 50

try:
    client.futures_change_leverage(
        symbol=DEFAULT_SYMBOL,
        leverage=DEFAULT_LEVERAGE
    )
    print("✅ Leverage set on startup")
except Exception as e:
    print("Leverage setup error:", e)

# ================= APP =================
app = Flask(__name__)

# ================= DUPLICATE PROTECTION =================
last_signal_id = None

# ================= WEBHOOK =================
@app.route("/webhook", methods=["POST"])
def webhook():
    global last_signal_id

    try:
        # ✅ แก้ 415 / header issue
        data = request.get_json(force=True, silent=True)

        if not data:
            import json
            raw = request.data.decode("utf-8")
            data = json.loads(raw)

        print("📩 Received:", data)

        signal_id = data.get("id")

        # ===== Duplicate protection =====
        if signal_id and signal_id == last_signal_id:
            print("⚠️ Duplicate ignored")
            return jsonify({"status": "duplicate ignored"})

        if signal_id:
            last_signal_id = signal_id

        action = data.get("action")
        symbol = data.get("symbol")
        side = data.get("side")
        qty = float(data.get("amount", 0))

        if not action or not symbol:
            return jsonify({"error": "invalid payload"}), 400

        # ================= CLOSE =================
        if action == "CLOSE":

            close_side = SIDE_SELL if side == "BUY" else SIDE_BUY

            # 🔥 ถ้า qty = 0 ให้ปิดทั้ง position แทน
            if qty <= 0:
                position = client.futures_position_information(symbol=symbol)
                for p in position:
                    if float(p["positionAmt"]) != 0:
                        qty = abs(float(p["positionAmt"]))
                        break

            order = client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                reduceOnly=True
            )

            return jsonify({"status": "closed", "orderId": order["orderId"]})

        # ================= OPEN =================
        if action == "OPEN":

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

