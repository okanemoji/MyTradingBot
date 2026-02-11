from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os
from dotenv import load_dotenv

load_dotenv()
client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))
# ใช้ Testnet สำหรับทดสอบ หรือ fapi.binance.com สำหรับเทรดจริง
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json
        action = data.get("action").upper() # BUY, SELL, CLOSE
        symbol = data.get("symbol")
        qty = data.get("amount")
        lev = data.get("leverage")

        print(f"📩 Alert Received: {action} {symbol}")

        # 1. ตั้ง Leverage (เรียก API เฉพาะตอนเปิดไม้ใหม่)
        if action in ["BUY", "SELL"]:
            client.futures_change_leverage(symbol=symbol, leverage=lev)

        # 2. ส่งคำสั่ง Market Order ทันที
        if action == "BUY":
            client.futures_create_order(
                symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_MARKET, quantity=qty)
        
        elif action == "SELL":
            client.futures_create_order(
                symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=qty)
        
        elif action == "CLOSE":
            # ใช้ฟีเจอร์ One-Way เพื่อปิดสถานะทั้งหมดที่มีอยู่ของเหรียญนั้น
            # การระบุ side ใน One-Way Close ต้องระบุฝั่งตรงข้ามที่มีอยู่ หรือใช้ Market Order ปกติ
            # แต่เพื่อความง่ายและประหยัด APIที่สุด เราส่งคำสั่งขาย/ซื้อคืนตามจำนวนที่ส่งมาจาก Pine
            side_to_close = SIDE_SELL if data.get("prev_side") == "BUY" else SIDE_BUY
            client.futures_create_order(
                symbol=symbol, side=side_to_close, type=ORDER_TYPE_MARKET, quantity=qty)

        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
