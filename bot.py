import os, time
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# ตั้งค่า Client
client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))
# เปลี่ยนเป็น https://fapi.binance.com สำหรับพอร์ตจริง
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

def sync_time():
    try:
        server_time = client.get_server_time()["serverTime"]
        client.timestamp_offset = server_time - int(time.time() * 1000)
    except: pass

sync_time()

def close_all_by_side(symbol, pos_side):
    """
    ฟังก์ชันปิดสถานะตามฝั่ง (Hedge Mode)
    ถ้าปิด LONG ต้องส่ง SELL | ถ้าปิด SHORT ต้องส่ง BUY
    """
    try:
        positions = client.futures_position_information(symbol=symbol)
        for p in positions:
            if p["positionSide"] == pos_side:
                amt = abs(float(p["positionAmt"]))
                if amt > 0:
                    # หัวใจสำคัญ: Side ต้องตรงข้ามกับ PositionSide
                    side_to_send = SIDE_SELL if pos_side == "LONG" else SIDE_BUY
                    client.futures_create_order(
                        symbol=symbol,
                        side=side_to_send,
                        type=ORDER_TYPE_MARKET,
                        quantity=amt,
                        positionSide=pos_side,
                        reduceOnly=True # ยืนยันว่าเป็นการปิดเท่านั้น
                    )
                    print(f"🧹 ล้าง {pos_side} สำเร็จ: {amt}")
        client.futures_cancel_all_open_orders(symbol=symbol)
    except Exception as e:
        print(f"❌ Error Closing {pos_side}: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data: return jsonify({"status": "no data"}), 400

    action = data.get("action", "").upper()
    symbol = data.get("symbol")
    
    try:
        # --- 1. คำสั่ง CLOSE ---
        if action == "CLOSE":
            close_all_by_side(symbol, "LONG")
            close_all_by_side(symbol, "SHORT")
            return jsonify({"status": "closed_all"}), 200
        
        elif action == "CLOSE_LONG":
            close_all_by_side(symbol, "LONG")
            return jsonify({"status": "closed_long"}), 200
            
        elif action == "CLOSE_SHORT":
            close_all_by_side(symbol, "SHORT")
            return jsonify({"status": "closed_short"}), 200

        # --- 2. คำสั่ง BUY / SELL (รองรับสลับฝั่ง + สะสมไม้) ---
        elif action in ["BUY", "SELL"]:
            qty = float(data.get("amount", 0))
            lev = int(data.get("leverage", 1))
            
            # กำหนดเป้าหมาย
            target_pos_side = "LONG" if action == "BUY" else "SHORT"
            opp_pos_side = "SHORT" if action == "BUY" else "LONG"
            order_side = SIDE_BUY if action == "BUY" else SIDE_SELL

            # ปรับ Leverage ก่อน
            client.futures_change_leverage(symbol=symbol, leverage=lev)

            # สั่งล้างฝั่งตรงข้ามก่อนเสมอ (สลับหน้าเทรด)
            close_all_by_side(symbol, opp_pos_side)

            # เปิดไม้ใหม่ หรือ เพิ่มไม้ (Re-entry)
            client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                positionSide=target_pos_side
            )
            print(f"✅ {action} Executed: {qty} on {target_pos_side}")
            return jsonify({"status": "success"}), 200

    except Exception as e:
        if "Timestamp" in str(e): sync_time()
        print(f"❌ Webhook Error: {e}")
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
