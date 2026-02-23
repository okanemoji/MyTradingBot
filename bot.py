from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv
import os
import time
import threading

# ===== LOAD ENV VARIABLES =====
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)

app = Flask(__name__)

# ===== GLOBAL VARIABLES =====
recent_alerts = {}       # ป้องกัน alert ซ้ำ
ALERT_COOLDOWN = 2       # วินาที delay ป้องกัน alert ซ้ำ
lock = threading.Lock()

# ===== FUNCTION: HANDLE ALERT =====
def handle_alert(alert_json, test_mode=True):
    """
    alert_json ตัวอย่าง:
    {
      "id": "1677051234567_BUY_OPEN",
      "action": "OPEN",
      "side": "BUY",
      "symbol": "XAUUSDT",
      "amount": 0.1,
      "leverage": 10
    }
    """
    alert_id = alert_json.get("id")
    action = alert_json.get("action")
    side = alert_json.get("side")
    symbol = alert_json.get("symbol")
    qty = float(alert_json.get("amount", 0))
    leverage = int(alert_json.get("leverage", 1))

    # ป้องกัน alert ซ้ำ
    with lock:
        now = time.time()
        if alert_id in recent_alerts:
            if now - recent_alerts[alert_id] < ALERT_COOLDOWN:
                print(f"[INFO] Duplicate alert skipped: {alert_id}")
                return
        recent_alerts[alert_id] = now

    # เรียก Binance API ใน thread แยก
    def execute_order():
        print(f"[INFO] Received alert: {alert_json}")

        if test_mode:
            print(f"[TEST MODE] Would execute order: {side} {action} {qty} {symbol} @ leverage {leverage}")
            return

        try:
            # ตั้ง leverage
            client.futures_change_leverage(symbol=symbol, leverage=leverage)

            if action == "OPEN":
                if side == "BUY":
                    client.futures_create_order(
                        symbol=symbol,
                        side=SIDE_BUY,
                        type=FUTURE_ORDER_TYPE_MARKET,
                        quantity=qty
                    )
                elif side == "SELL":
                    client.futures_create_order(
                        symbol=symbol,
                        side=SIDE_SELL,
                        type=FUTURE_ORDER_TYPE_MARKET,
                        quantity=qty
                    )
            elif action == "CLOSE":
                # ปิด position ด้วย opposite order
                if side == "BUY":
                    client.futures_create_order(
                        symbol=symbol,
                        side=SIDE_SELL,
                        type=FUTURE_ORDER_TYPE_MARKET,
                        quantity=qty
                    )
                elif side == "SELL":
                    client.futures_create_order(
                        symbol=symbol,
                        side=SIDE_BUY,
                        type=FUTURE_ORDER_TYPE_MARKET,
                        quantity=qty
                    )
            print(f"[SUCCESS] Alert executed: {alert_id}")
        except Exception as e:
            print(f"[ERROR] Failed to execute alert {alert_id}: {e}")

    threading.Thread(target=execute_order).start()

# ===== FLASK ROUTES =====
@app.route("/alert", methods=["POST"])
def alert():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON provided"}), 400

    # test_mode=True -> print JSON แทนส่งจริง
    handle_alert(data, test_mode=True)
    return jsonify({"status": "ok"}), 200

# เพิ่ม /webhook ให้รองรับ TradingView ที่ยิงมาแบบ webhook
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON provided"}), 400
    handle_alert(data, test_mode=True)
    return jsonify({"status": "ok"}), 200

# ===== RUN APP =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
