import os
import time
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

# --- SETUP ---
load_dotenv()
app = Flask(__name__)

# ดึง API Key จาก Environment Variables ใน Render
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)
# ถ้าใช้พอร์ตจริงให้เปลี่ยนเป็น https://fapi.binance.com
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi" 

# ฟังก์ชัน Sync เวลาป้องกัน Error -1021
def sync_time():
    try:
        server_time = client.get_server_time()["serverTime"]
        client.timestamp_offset = server_time - int(time.time() * 1000)
    except:
        pass

sync_time()

# --- FUNCTIONS ---

def close_all_by_side(symbol, pos_side):
    """ ปิดสถานะฝั่งที่ระบุให้เกลี้ยง 100% (LONG หรือ SHORT) """
    try:
        positions = client.futures_position_information(symbol=symbol)
        for p in positions:
            if p["positionSide"] == pos_side:
                amt = abs(float(p["positionAmt"]))
                if amt > 0:
                    # ถ้าจะปิด LONG ต้องส่ง SELL | ถ้าจะปิด SHORT ต้องส่ง BUY
                    side = SIDE_SELL if pos_side == "LONG" else SIDE_BUY
                    client.futures_create_order(
                        symbol=symbol,
                        side=side,
                        type=ORDER_TYPE_MARKET,
                        quantity=amt,
                        positionSide=pos_side
                    )
                    print(f"🧹 Closed {pos_side} size {amt}")
        client.futures_cancel_all_open_orders(symbol=symbol)
    except Exception as e:
        print(f"❌ Error Closing {pos_side}: {e}")

# --- WEBHOOK ENDPOINT ---

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data:
        return jsonify({"status": "error"}), 400

    action = data.get("action", "").upper() # BUY, SELL, CLOSE
    symbol = data.get("symbol")
    
    print(f"📩 Received: {action} on {symbol}")

    try:
        # กรณีสั่ง CLOSE (กวาดล้างทั้ง 2 ฝั่ง)
        if action == "CLOSE":
            close_all_by_side(symbol, "LONG")
            close_all_by_side(symbol, "SHORT")
            return jsonify({"status": "closed_all"}), 200

        # กรณีสั่งเปิดไม้ หรือสะสมไม้ (Re-entry)
        if action in ["BUY", "SELL"]:
            qty = float(data.get("amount", 0))
            lev = int(data.get("leverage", 1))
            
            # กำหนดฝั่งตาม Hedge Mode
            pos_side = "LONG" if action == "BUY" else "SHORT"
            opp_side = "SHORT" if action == "BUY" else "LONG"

            # 1. ล้างฝั่งตรงข้ามก่อนเสมอ (เพื่อรองรับการสลับหน้าเทรด)
            close_all_by_side(symbol, opp_side)
            
            # 2. ปรับ Leverage
            client.futures_change_leverage(symbol=symbol, leverage=lev)
            
            # 3. เปิดออเดอร์ (ถ้าฝั่งเดิมจะเป็นการเพิ่ม Lot สะสม)
            side = SIDE_BUY if action == "BUY" else SIDE_SELL
            client.futures_create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                positionSide=pos_side
            )
            print(f"✅ {action} Executed (Re-entry supported)")
            return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        if "Timestamp" in str(e):
            sync_time()
        return jsonify({"error": str(e)}), 400

@app.route("/")
def health():
    return "Bot is running", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
