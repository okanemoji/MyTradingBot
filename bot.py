from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException
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
recent_alerts = {}
ALERT_COOLDOWN = 2  # วินาที ป้องกัน alert ซ้ำ
lock = threading.Lock()

# ===== FUNCTION: HANDLE ALERT =====
def handle_alert(alert_json):
    alert_id = alert_json.get("id")
    action = alert_json.get("action")  # OPEN / CLOSE
    side = alert_json.get("side")      # BUY / SELL
    symbol = alert_json.get("symbol")
    qty = float(alert_json.get("amount", 0))
    leverage = int(alert_json.get("leverage", 1))

    with lock:
        now = time.time()
        if alert_id in recent_alerts and now - recent_alerts[alert_id] < ALERT_COOLDOWN:
            print(f"[INFO] Duplicate alert skipped: {alert_id}")
            return
        recent_alerts[alert_id] = now

    def execute_order():
        print(f"[DEBUG] Received alert: {alert_json}")

        try:
            # ตั้ง leverage
            resp_leverage = client.futures_change_leverage(symbol=symbol, leverage=leverage)
            print(f"[DEBUG] Set leverage response: {resp_leverage}")

            # Hedge Mode ต้องระบุ positionSide
            if action == "OPEN":
                position_side = "LONG" if side == "BUY" else "SHORT"
                resp_order = client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_BUY if side == "BUY" else SIDE_SELL,
                    type=FUTURE_ORDER_TYPE_MARKET,
                    quantity=qty,
                    positionSide=position_side
                )
            elif action == "CLOSE":
                position_side = "LONG" if side == "BUY" else "SHORT"
                resp_order = client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_SELL if side == "BUY" else SIDE_BUY,
                    type=FUTURE_ORDER_TYPE_MARKET,
                    quantity=qty,
                    positionSide=position_side
                )
            print(f"[SUCCESS] Order executed: {resp_order}")
        except BinanceAPIException as e:
            print(f"[ERROR] BinanceAPIException: {e.status_code} {e.message}")
        except Exception as e:
            print(f"[ERROR] Unexpected exception: {e}")

    threading.Thread(target=execute_order).start()

# ===== FLASK ROUTES =====
@app.route("/alert", methods=["POST"])
def alert():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON provided"}), 400

    handle_alert(data)
    return jsonify({"status": "ok"}), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON provided"}), 400

    handle_alert(data)
    return jsonify({"status": "ok"}), 200

# ===== RUN APP =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
