import os
import json
import threading
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

# ================= CONFIGURATION =================
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
USE_TESTNET = True # เปลี่ยนเป็น False เมื่อใช้เงินจริง

app = Flask(__name__)

# ================= BINANCE CLIENT =================
client = Client(API_KEY, API_SECRET, testnet=USE_TESTNET)

# ================= DUPLICATE PROTECTION =================
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

# ================= UTILS =================
def get_position_amt(symbol, side):
    positions = client.futures_position_information(symbol=symbol)
    pos_side = "LONG" if side == "BUY" else "SHORT"
    for p in positions:
        if p["positionSide"] == pos_side:
            return abs(float(p["positionAmt"]))
    return 0

# ================= WEBHOOK ROUTE =================
@app.route("/webhook", methods=["POST"])
def webhook():
    # 1. ดักจับข้อมูลดิบเพื่อ Debug
    raw_body = request.get_data(as_text=True)
    print(f"--- [NEW ALERT] ---")
    print(f"Raw Data: {raw_body}")

    try:
        # 2. พยายามแปลงเป็น JSON
        if request.is_json:
            data = request.json
        else:
            data = json.loads(raw_body)

        # 3. ตรวจสอบ Duplicate
        order_id = str(data.get("id"))
        if is_duplicate(order_id):
            print(f"⚠ Ignored: Duplicate ID {order_id}")
            return jsonify({"status": "duplicate"}), 200

        # 4. ดึงพารามิเตอร์
        action = data.get("action")   # OPEN / CLOSE
        side = data.get("side")       # BUY / SELL
        symbol = data.get("symbol")
        qty = float(data.get("amount", 0))
        leverage = int(data.get("leverage", 20))

        pos_side = "LONG" if side == "BUY" else "SHORT"

        # 5. ประมวลผล OPEN
        if action == "OPEN":
            print(f"🚀 OPENING {pos_side} on {symbol}...")
            client.futures_change_leverage(symbol=symbol, leverage=leverage)
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY if side == "BUY" else SIDE_SELL,
                positionSide=pos_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            print(f"✅ Success: {order_id}")
            return jsonify({"status": "opened", "id": order_id}), 200

        # 6. ประมวลผล CLOSE
        if action == "CLOSE":
            print(f"🛑 CLOSING {pos_side} on {symbol}...")
            current_qty = get_position_amt(symbol, side)
            if current_qty > 0:
                order = client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_SELL if side == "BUY" else SIDE_BUY,
                    positionSide=pos_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=current_qty
                )
                print(f"✅ Closed: {order_id}")
                return jsonify({"status": "closed"}), 200
            print(f"⚠ No position found for {symbol}")
            return jsonify({"status": "no_position"}), 200

    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
