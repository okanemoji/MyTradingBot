from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv
import os
import time
import threading
import json

# ================= ENV =================
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

app = Flask(__name__)

# ================= GLOBAL =================
client = None
last_order_time = 0
ORDER_COOLDOWN = 2
processed_ids = set()
lock = threading.Lock()

# ================= INIT BINANCE =================
def init_binance():
    global client
    while True:
        try:
            client = Client(API_KEY, API_SECRET, {"timeout": 20}, testnet=True)

            server_time = client.get_server_time()["serverTime"]
            local_time = int(time.time() * 1000)
            client.timestamp_offset = server_time - local_time

            print("✅ Binance Testnet Connected")
            break

        except Exception as e:
            print("⚠ Binance init failed:", e)
            time.sleep(5)

threading.Thread(target=init_binance, daemon=True).start()

# ================= DUPLICATE CHECK =================
def is_duplicate(order_id):
    with lock:
        if order_id in processed_ids:
            return True
        processed_ids.add(order_id)
        if len(processed_ids) > 1000:
            processed_ids.clear()
        return False

# ================= SAFE ORDER =================
def safe_order(**kwargs):
    global last_order_time

    now = time.time()
    if now - last_order_time < ORDER_COOLDOWN:
        print("⏳ Cooldown block")
        return {"status": "cooldown"}

    try:
        order = client.futures_create_order(**kwargs)
        last_order_time = time.time()
        return order

    except BinanceAPIException as e:
        print("⚠ Binance API Error:", e)
        return {"error": str(e)}

    except Exception as e:
        print("⚠ Unknown Order Error:", e)
        return {"error": str(e)}

# ================= GET POSITION =================
def get_position(symbol, position_side):
    try:
        positions = client.futures_position_information(symbol=symbol)
        for p in positions:
            if p["positionSide"] == position_side and abs(float(p["positionAmt"])) > 0:
                return p
    except Exception as e:
        print("⚠ Get position error:", e)
    return None

# ================= WEBHOOK =================
@app.route("/webhook", methods=["POST"])
def webhook():

    global client

    print("RAW:", request.data)

    if client is None:
        return jsonify({"status": "binance not ready"})

    try:
        data = request.get_json(force=True)
    except Exception as e:
        print("❌ JSON ERROR:", e)
        return jsonify({"status": "bad json but ignored"})

    order_id = data.get("id")
    action = data.get("action")
    symbol = data.get("symbol")

    if not order_id or not action or not symbol:
        return jsonify({"status": "missing field ignored"})

    if is_duplicate(order_id):
        return jsonify({"status": "duplicate ignored"})

    # ================= OPEN =================
    if action == "OPEN":

        side = data.get("side")
        amount = float(data.get("amount", 0))
        leverage = int(data.get("leverage", 1))

        position_side = "LONG" if side == "BUY" else "SHORT"
        order_side = SIDE_BUY if side == "BUY" else SIDE_SELL

        try:
            # เปลี่ยน leverage เฉพาะถ้าจำเป็น
            try:
                client.futures_change_leverage(symbol=symbol, leverage=leverage)
            except BinanceAPIException:
                pass

            result = safe_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=amount,
                positionSide=position_side,
                newClientOrderId=order_id
            )

            print("OPEN RESULT:", result)

        except Exception as e:
            print("❌ OPEN FAIL:", e)

        return jsonify({"status": "open processed"})

    # ================= CLOSE =================
    if action == "CLOSE":

        side = data.get("side")
        position_side = "LONG" if side == "BUY" else "SHORT"
        close_side = SIDE_SELL if side == "BUY" else SIDE_BUY

        pos = get_position(symbol, position_side)

        if not pos:
            return jsonify({"status": "no position"})

        qty = abs(float(pos["positionAmt"]))

        try:
            result = safe_order(
                symbol=symbol,
                side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                positionSide=position_side,
                newClientOrderId=order_id
            )

            print("CLOSE RESULT:", result)

        except Exception as e:
            print("❌ CLOSE FAIL:", e)

        return jsonify({"status": "close processed"})

    return jsonify({"status": "unknown action ignored"})


# ================= RUN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
