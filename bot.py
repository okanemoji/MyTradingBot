from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os

app = Flask(__name__)
# เก็บสถานะฝั่งล่าสุดไว้ในตัวแปร (Memory) เพื่อลดการ Call API ไปถาม Binance
last_side = {} 

@app.route("/webhook", methods=["POST"])
def webhook():
    global last_side
    data = request.json
    action = data.get("action").upper() # BUY, SELL, CLOSE
    symbol = data.get("symbol")
    qty = data.get("amount")
    lev = data.get("leverage")

    try:
        # --- กรณีสั่ง CLOSE (100% กวาดล้าง) ---
        if action == "CLOSE":
            client.futures_cancel_all_open_orders(symbol=symbol)
            # ปิด 100% โดยการลองส่งทั้งสองฝั่งพร้อม closePosition (Binance จะปิดฝั่งที่มีอยู่ให้เอง)
            # วิธีนี้ใช้ 1-2 API Call แต่ชัวร์กว่าและไม่ต้องดึง Quantity มาคำนวณ
            for s in [SIDE_SELL, SIDE_BUY]:
                try:
                    client.futures_create_order(
                        symbol=symbol, side=s, type=ORDER_TYPE_MARKET, closePosition=True
                    )
                except: pass 
            last_side[symbol] = None
            print(f"🧹 {symbol} Closed 100%")
            return jsonify({"status": "success"}), 200

        # --- กรณีสั่ง BUY หรือ SELL ---
        elif action in ["BUY", "SELL"]:
            # ปรับ Leverage
            client.futures_change_leverage(symbol=symbol, leverage=lev)

            # ตรวจสอบ: ถ้าเป็นฝั่งตรงข้ามกับที่มีอยู่ (เช่น มี BUY จะลง SELL) ให้ล้างพอร์ตก่อน
            if symbol in last_side and last_side[symbol] is not None and last_side[symbol] != action:
                print(f"🔄 Opposite Signal! Clearing {last_side[symbol]} before opening {action}")
                for s in [SIDE_SELL, SIDE_BUY]:
                    try:
                        client.futures_create_order(
                            symbol=symbol, side=s, type=ORDER_TYPE_MARKET, closePosition=True
                        )
                    except: pass

            # เปิดไม้ (ถ้าเป็นฝั่งเดียวกัน มันจะ Re-entry สะสม Lot ให้เอง)
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY if action == "BUY" else SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            
            last_side[symbol] = action # บันทึกฝั่งล่าสุดไว้
            print(f"🚀 {action} Executed (Qty: {qty})")
            return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400
