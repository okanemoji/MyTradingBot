import os, time
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# เชื่อมต่อ API (ดึงจาก Env)
client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))
# สำหรับ Testnet ใช้ URL นี้ / ถ้าพอร์ตจริงเปลี่ยนเป็น https://fapi.binance.com
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

def sync_time():
    """ซิงค์เวลาป้องกัน Error 1021"""
    try:
        server_time = client.get_server_time()["serverTime"]
        client.timestamp_offset = server_time - int(time.time() * 1000)
    except: pass

sync_time()

def force_close_side(symbol, pos_side):
    """
    เช็คหน้าตักจริงและสั่งปิดสถานะฝั่งที่ระบุ (Hedge Mode)
    เรียก API เฉพาะเมื่อได้รับสัญญาณปิด เพื่อประหยัดโควตา
    """
    try:
        positions = client.futures_position_information(symbol=symbol)
        for p in positions:
            # ตรวจสอบเหรียญและฝั่ง (LONG/SHORT)
            if p['symbol'] == symbol and p['positionSide'] == pos_side:
                amt = abs(float(p['positionAmt']))
                if amt > 0:
                    # Hedge Mode กฎคือ: ปิด LONG ใช้ SELL / ปิด SHORT ใช้ BUY
                    side_to_send = SIDE_SELL if pos_side == "LONG" else SIDE_BUY
                    
                    print(f"⚠️ บังคับปิด {pos_side}: จำนวน {amt} units")
                    client.futures_create_order(
                        symbol=symbol,
                        side=side_to_send,
                        type=ORDER_TYPE_MARKET,
                        quantity=amt,
                        positionSide=pos_side,
                        reduceOnly=True
                    )
        # เคลียร์ออเดอร์ค้าง (SL/TP)
        client.futures_cancel_all_open_orders(symbol=symbol)
    except Exception as e:
        print(f"❌ Error ในการปิด {pos_side}: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data: return jsonify({"status": "no data"}), 400

    action = data.get("action", "").upper()
    symbol = data.get("symbol")
    
    print(f"📩 สัญญาณเข้า: {action} | เหรียญ: {symbol}")

    try:
        # --- 1. คำสั่ง CLOSE (ล้างพอร์ต) ---
        if action == "CLOSE":
            force_close_side(symbol, "LONG")
            force_close_side(symbol, "SHORT")
            return jsonify({"status": "force_closed_all"}), 200
        
        elif action == "CLOSE_LONG":
            force_close_side(symbol, "LONG")
            return jsonify({"status": "closed_long"}), 200

        elif action == "CLOSE_SHORT":
            force_close_side(symbol, "SHORT")
            return jsonify({"status": "closed_short"}), 200

        # --- 2. คำสั่ง BUY / SELL (สลับฝั่ง + สะสมไม้) ---
        elif action in ["BUY", "SELL"]:
            qty = float(data.get("amount", 0))
            lev = int(data.get("leverage", 50))
            
            target_side = "LONG" if action == "BUY" else "SHORT"
            opp_side = "SHORT" if action == "BUY" else "LONG"
            order_side = SIDE_BUY if action == "BUY" else SIDE_SELL

            # ปรับ Leverage (API 1 Weight)
            client.futures_change_leverage(symbol=symbol, leverage=lev)

            # เช็คและล้างฝั่งตรงข้ามก่อนเปิดไม้ใหม่ (ป้องกัน Lot ลด)
            force_close_side(symbol, opp_side)

            # ส่งคำสั่งเปิด/เพิ่มไม้ (API 1 Weight)
            client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                positionSide=target_side
            )
            print(f"✅ ยิง {action} สำเร็จ | ฝั่ง: {target_side} | จำนวน: {qty}")
            return jsonify({"status": "success"}), 200

    except Exception as e:
        if "Timestamp" in str(e): sync_time()
        print(f"❌ Webhook Error: {e}")
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
