import os
import json
import threading
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

# ================= การตั้งค่า (Configuration) =================
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
# เปลี่ยนเป็น False เมื่อต้องการรันบนบัญชีจริง (Real Account)
USE_TESTNET = True 

app = Flask(__name__)

# ================= เชื่อมต่อ BINANCE =================
client = Client(API_KEY, API_SECRET, testnet=USE_TESTNET)

# ================= ระบบป้องกันคำสั่งซ้ำ (Duplicate Protection) =================
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

# ================= ฟังก์ชันเช็คสถานะออเดอร์ค้าง (Utils) =================
def get_position_amt(symbol, side):
    try:
        positions = client.futures_position_information(symbol=symbol)
        pos_side = "LONG" if side == "BUY" else "SHORT"
        for p in positions:
            if p["positionSide"] == pos_side:
                return abs(float(p["positionAmt"]))
    except Exception as e:
        print(f"Error fetching position: {e}")
    return 0

# ================= WEBHOOK (จุดรับสัญญาณ) =================
@app.route("/webhook", methods=["POST"])
def webhook():
    # 1. ดักจับข้อมูลดิบเพื่อตรวจสอบ (Debug)
    raw_body = request.get_data(as_text=True)
    print(f"--- [ได้รับสัญญาณใหม่] ---")
    print(f"ข้อมูลที่ส่งมา: {raw_body}")

    try:
        # 2. แปลงข้อมูลเป็น JSON
        if request.is_json:
            data = request.json
        else:
            data = json.loads(raw_body)

        # 3. ตรวจสอบ ID ซ้ำ
        order_id = str(data.get("id", "no_id"))
        if is_duplicate(order_id):
            print(f"⚠ ข้ามสัญญาณ: ID {order_id} ถูกประมวลผลไปแล้ว")
            return jsonify({"status": "duplicate"}), 200

        # 4. ดึงค่าพารามิเตอร์ต่างๆ
        action = data.get("action")   # OPEN หรือ CLOSE
        side = data.get("side")       # BUY หรือ SELL
        symbol = data.get("symbol")
        qty = float(data.get("amount", 0))
        leverage = int(data.get("leverage", 20))
        pos_side = "LONG" if side == "BUY" else "SHORT"

        # 5. ประมวลผลคำสั่ง OPEN
        if action == "OPEN":
            print(f"🚀 กำลังเปิด {pos_side} สำหรับ {symbol}...")
            client.futures_change_leverage(symbol=symbol, leverage=leverage)
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY if side == "BUY" else SIDE_SELL,
                positionSide=pos_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            print(f"✅ สำเร็จ: {order_id}")
            return jsonify({"status": "opened", "order_id": order_id}), 200

        # 6. ประมวลผลคำสั่ง CLOSE
        elif action == "CLOSE":
            print(f"🛑 กำลังปิด {pos_side} สำหรับ {symbol}...")
            current_qty = get_position_amt(symbol, side)
            if current_qty > 0:
                order = client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_SELL if side == "BUY" else SIDE_BUY,
                    positionSide=pos_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=current_qty
                )
                print(f"✅ ปิดออเดอร์สำเร็จ: {order_id}")
                return jsonify({"status": "closed", "order_id": order_id}), 200
            else:
                print(f"⚠ ไม่มี Position ค้างอยู่ให้ปิด")
                return jsonify({"status": "no_position_to_close"}), 200

        # 7. กรณี Action ไม่ถูกต้อง
        else:
            print(f"❌ Action ไม่ถูกต้อง: {action}")
            return jsonify({"status": "invalid_action"}), 400

    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาด: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
