import os
import time
import threading
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

# ================= CONFIGURATION =================
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
# เปลี่ยนเป็น False เมื่อต้องการรันบน Real Account
USE_TESTNET = True 

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
        # ป้องกัน Memory เต็ม (เก็บไว้ 1,000 id ล่าสุด)
        if len(processed_ids) > 1000:
            processed_ids.clear()
        return False

# ================= UTILS =================
def get_position_amt(symbol, side):
    """ เช็คจำนวน QTY ที่ถืออยู่จริงในฝั่งนั้นๆ """
    positions = client.futures_position_information(symbol=symbol)
    position_side = "LONG" if side == "BUY" else "SHORT"
    for p in positions:
        if p["positionSide"] == position_side:
            return abs(float(p["positionAmt"]))
    return 0

# ================= WEBHOOK ROUTE =================
@app.route("/webhook", methods=["POST"])
def webhook():
    # ดูว่าข้อมูลที่เข้ามาหน้าตาเป็นยังไง
    raw_data = request.get_data(as_text=True)
    print(f"RAW DATA: {raw_data}") 
    
    try:
        data = request.json
        if data is None:
            return jsonify({"error": "Request must be JSON"}), 400
            
        # ... ส่วนที่เหลือของโค้ด ...

        action = data.get("action")   # OPEN หรือ CLOSE
        side = data.get("side")       # BUY หรือ SELL
        symbol = data.get("symbol")
        
        # กำหนด Parameter สำหรับ Hedge Mode
        pos_side = "LONG" if side == "BUY" else "SHORT"
        
        # 2. ประมวลผลคำสั่ง CLOSE
        if action == "CLOSE":
            qty = get_position_amt(symbol, side)
            if qty > 0:
                order_side = SIDE_SELL if side == "BUY" else SIDE_BUY
                order = client.futures_create_order(
                    symbol=symbol,
                    side=order_side,
                    positionSide=pos_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=qty
                )
                return jsonify({"status": "closed", "order": order}), 200
            return jsonify({"status": "no_position_to_close"}), 200

        # 3. ประมวลผลคำสั่ง OPEN
        if action == "OPEN":
            qty = float(data.get("amount", 0))
            lev = int(data.get("leverage", 20))
            
            # ปรับ Leverage ก่อนเปิด
            client.futures_change_leverage(symbol=symbol, leverage=lev)
            
            order_side = SIDE_BUY if side == "BUY" else SIDE_SELL
            order = client.futures_create_order(
                symbol=symbol,
                side=order_side,
                positionSide=pos_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            return jsonify({"status": "opened", "order": order}), 200

    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == "__main__":
    # Render จะส่ง Port มาให้ทาง Environment Variable
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
