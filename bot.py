from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv
import os
import time
import threading

# ================= ENV =================
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY_TESTNET")
API_SECRET = os.getenv("BINANCE_API_SECRET_TESTNET")

app = Flask(__name__)

# ================= SAFE CLIENT INIT =================
client = None

def init_binance():
    global client
    while True:
        try:
            # ===== Testnet endpoint =====
            client = Client(API_KEY, API_SECRET, {"timeout": 20}, testnet=True)

            # ===== SYNC TIME =====
            server_time = client.get_server_time()["serverTime"]
            local_time = int(time.time() * 1000)
            client.timestamp_offset = server_time - local_time

            print("✅ Binance Testnet connected & time synced")
            break

        except Exception as e:
            print("⚠ Binance init failed:", e)
            print("⏳ Retry in 60 sec...")
            time.sleep(60)

# รัน background thread
threading.Thread(target=init_binance, daemon=True).start()

# ================= DUPLICATE PROTECTION =================
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

# ================= UTILS =================
def get_position(symbol, position_side):
    if client is None:
        return None
    positions = client.futures_position_information(symbol=symbol)
    for p in positions:
        if p["positionSide"] == position_side and abs(float(p["positionAmt"])) > 0:
            return p
    return None

# ================= WEBHOOK =================
@app.route("/webhook", methods=["POST"])
def webhook():
    global client

    if client is None:
        return jsonify({"error": "binance not ready"}), 503

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
        symbol = data.get("symbol")

        # ===== HEARTBEAT =====
        if action == "HEARTBEAT":
            timestamp = data.get("timestamp", time.time() * 1000)
            print(f"💓 Heartbeat received at {timestamp}")

            # เรียก API เบาๆ แค่ keep-alive
            try:
                client.get_server_time()
            except Exception as e:
                print("⚠ Heartbeat API error:", e)

            return jsonify({"status": "heartbeat ok"})

        # ===== CLOSE =====
        if action == "CLOSE":
            side = data["side"]
            position_side = "LONG" if side == "BUY" else "SHORT"
            close_side = SIDE_SELL if side == "BUY" else SIDE_BUY

            pos = get_position(symbol, position_side)
            if not pos:
                return jsonify({"status": "no position"})

            qty = abs(float(pos["positionAmt"]))

            client.futures_create_order(
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

            client.futures_create_order(
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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
