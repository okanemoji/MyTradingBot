import os
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

# 1. โหลดค่า Environment และประกาศตัวแปร app ก่อนเพื่อน
load_dotenv()
app = Flask(__name__)

# 2. ตั้งค่า Binance Client
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
client = Client(API_KEY, API_SECRET)
# เลือกใช้ URL ตามจริง (Testnet หรือ Mainnet)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi" 

# 3. ส่วนของฟังก์ชันคำสั่ง (Logic)
def execute_close_all(symbol):
    try:
        # ยกเลิก Order ค้าง
        client.futures_cancel_all_open_orders(symbol=symbol)
        # สั่ง Close Position (กวาดล้าง 1 API Call)
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL, # ใน One-Way mode ใส่ฝั่งไหนก็ได้ถ้ามี closePosition=True
            type=ORDER_TYPE_MARKET,
            closePosition=True
        )
        return True
    except Exception as e:
        print(f"❌ Close All Error: {e}")
        return False

# 4. ส่วนของ Webhook (ต้องอยู่หลังการประกาศ app)
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data:
        return jsonify({"status": "no data"}), 400

    action = data.get("action").upper()
    symbol = data.get("symbol")
    qty = data.get("amount")
    lev = data.get("leverage")

    try:
        if action == "BUY" or action == "SELL":
            client.futures_change_leverage(symbol=symbol, leverage=lev)
            side = SIDE_BUY if action == "BUY" else SIDE_SELL
            client.futures_create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            print(f"🚀 {action} {symbol} Done")
            
        elif action == "CLOSE":
            execute_close_all(symbol)
            print(f"🧹 {symbol} Closed All")

        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

# 5. ส่วนรัน Server
if __name__ == "__main__":
    # Render จะใช้พอร์ต 5000 หรือจาก Environment
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
