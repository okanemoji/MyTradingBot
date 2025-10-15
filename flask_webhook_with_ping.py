import os
import json
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from datetime import datetime

# ---------------------------
# 🔐 ตั้งค่า API Key / Secret จาก environment variable
# ---------------------------
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

app = Flask(__name__)

# ---------------------------
# 🧩 ฟังก์ชันสร้าง client เฉพาะเมื่อจำเป็น
# ---------------------------
def get_binance_client():
    """สร้าง Binance Client ตอนที่ต้องใช้งานจริงเท่านั้น"""
    return Client(API_KEY, API_SECRET)


# ---------------------------
# 🧠 ฟังก์ชันส่งคำสั่งเทรดไป Binance
# ---------------------------
def execute_trade(symbol, side, position_side, qty, order_type=ORDER_TYPE_MARKET):
    try:
        client = get_binance_client()
        print(f"🚀 Sending order: {side} {qty} {symbol} ({position_side})")
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type=order_type,
            quantity=qty,
            positionSide=position_side
        )
        print(f"✅ Binance order executed: {order['orderId']}")
        return {"status": "success", "order_id": order["orderId"]}
    except Exception as e:
        print(f"❌ Binance order failed: {e}")
        return {"status": "error", "message": str(e)}


# ---------------------------
# 🌐 Webhook Route
# ---------------------------
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        print(f"Received Webhook: {data}")

        # ✅ 1. ตรวจสอบว่าคือ alert ping
        if data.get("type") == "ping":
            print("🟢 Received ping alert → skip Binance API.")
            return jsonify({"status": "ok", "message": "ping received"}), 200

        # ✅ 2. ตรวจสอบว่ามี signal type มาจริงไหม
        signal_type = data.get("signal")
        if not signal_type:
            return jsonify({"status": "error", "message": "missing signal"}), 400

        symbol = data.get("symbol", "BTCUSDT")
        qty = float(data.get("qty", 0.001))

        # ✅ 3. แยกประเภทสัญญาณ
        if signal_type == "buy":
            return jsonify(execute_trade(symbol, SIDE_BUY, "LONG", qty))

        elif signal_type == "sell":
            return jsonify(execute_trade(symbol, SIDE_SELL, "SHORT", qty))

        elif signal_type == "close":
            client = get_binance_client()
            print("🔻 Closing all positions...")
            try:
                client.futures_cancel_all_open_orders(symbol=symbol)
                client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_SELL,
                    type=ORDER_TYPE_MARKET,
                    quantity=qty,
                    reduceOnly=True
                )
                print("✅ All positions closed.")
                return jsonify({"status": "success", "message": "positions closed"}), 200
            except Exception as e:
                print(f"❌ Close position failed: {e}")
                return jsonify({"status": "error", "message": str(e)}), 400

        else:
            return jsonify({"status": "ignored", "message": "unknown signal"}), 200

    except Exception as e:
        print(f"❌ Webhook Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------
# 🧭 Route สำหรับตรวจสอบสถานะบอท
# ---------------------------
@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "running",
        "timestamp": datetime.utcnow().isoformat()
    })


# ---------------------------
# 🚀 Flask entrypoint
# ---------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
