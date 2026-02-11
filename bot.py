import os
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

# โหลด Environment
load_dotenv()

app = Flask(__name__)

# ตั้งค่า Binance Client
# สำคัญ: ต้องไปตั้งค่า API Key ในหน้า Dashboard ของ Render (Environment Variables)
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
client = Client(API_KEY, API_SECRET)

# เปลี่ยนเป็น https://fapi.binance.com สำหรับพอร์ตจริง
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi" 

# ตัวแปรจำสถานะล่าสุดเพื่อทำ Re-entry
last_side = {}

def execute_close_all(symbol):
    """ฟังก์ชันกวาดล้างพอร์ต 100% แบบประหยัด API"""
    try:
        # 1. ยกเลิก Order ค้าง
        client.futures_cancel_all_open_orders(symbol=symbol)
        # 2. ยิงปิดทั้ง 2 ฝั่ง (One-Way Mode)
        for s in [SIDE_SELL, SIDE_BUY]:
            try:
                client.futures_create_order(
                    symbol=symbol,
                    side=s,
                    type=ORDER_TYPE_MARKET,
                    closePosition=True
                )
            except:
                pass
        return True
    except Exception as e:
        print(f"❌ Close Error: {e}")
        return False

@app.route("/webhook", methods=["POST"])
def webhook():
    global last_side
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No JSON payload"}), 400

    action = data.get("action", "").upper()
    symbol = data.get("symbol")
    qty = data.get("amount")
    lev = data.get("leverage")

    print(f"📩 Received Alert: {action} on {symbol}")

    try:
        # กรณีสั่ง CLOSE
        if action == "CLOSE":
            execute_close_all(symbol)
            last_side[symbol] = None
            return jsonify({"status": "success", "message": "Closed all"}), 200

        # กรณีสั่ง BUY หรือ SELL (รองรับ Re-entry และ Reverse)
        elif action in ["BUY", "SELL"]:
            # ปรับ Leverage
            if lev:
                client.futures_change_leverage(symbol=symbol, leverage=int(lev))

            # ถ้าสัญญาณสลับฝั่ง (Reverse) -> ล้างพอร์ตก่อน
            if symbol in last_side and last_side[symbol] is not None and last_side[symbol] != action:
                print(f"🔄 Swapping from {last_side[symbol]} to {action}. Clearing old position...")
                execute_close_all(symbol)

            # เปิดออเดอร์ (ถ้าฝั่งเดิมจะกลายเป็นการ Re-entry สะสม Lot)
            side = SIDE_BUY if action == "BUY" else SIDE_SELL
            client.futures_create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            
            last_side[symbol] = action
            print(f"✅ Executed {action} Qty: {qty}")
            return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"❌ Webhook Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/")
def health_check():
    return "Bot is Running!", 200

if __name__ == "__main__":
    # Render ต้องการให้รันบน Port ที่กำหนด
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
