from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException
import os
import time
import threading
import math

# ===== ENV =====
API_KEY = os.environ.get("BINANCE_API_KEY")
API_SECRET = os.environ.get("BINANCE_API_SECRET")

if not API_KEY or not API_SECRET:
    raise Exception("Missing Binance API keys")

client = Client(API_KEY, API_SECRET, testnet=True)

app = Flask(__name__)

recent_alerts = {}
ALERT_COOLDOWN = 2
lock = threading.Lock()


def round_step_size(quantity, step_size):
    return math.floor(quantity / step_size) * step_size


def close_full_position(symbol, side):
    positions = client.futures_position_information(symbol=symbol)

    for pos in positions:
        if pos["symbol"] != symbol:
            continue

        position_amt = float(pos["positionAmt"])
        position_side = pos["positionSide"]

        if side == "BUY" and position_side == "LONG" and position_amt > 0:
            close_side = SIDE_SELL
        elif side == "SELL" and position_side == "SHORT" and position_amt < 0:
            close_side = SIDE_BUY
        else:
            continue

        qty = abs(position_amt)

        if qty == 0:
            continue

        resp = client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type=FUTURE_ORDER_TYPE_MARKET,
            quantity=qty,
            positionSide=position_side
        )

        print(f"[SUCCESS] Closed FULL {position_side} qty={qty}")


def open_position(symbol, side, qty, leverage):
    client.futures_change_leverage(symbol=symbol, leverage=leverage)

    info = client.futures_exchange_info()
    step_size = None

    for s in info['symbols']:
        if s['symbol'] == symbol:
            step_size = float(s['filters'][2]['stepSize'])
            break

    if step_size is None:
        print("Cannot find step size")
        return

    qty = round_step_size(qty, step_size)

    position_side = "LONG" if side == "BUY" else "SHORT"

    resp = client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY if side == "BUY" else SIDE_SELL,
        type=FUTURE_ORDER_TYPE_MARKET,
        quantity=qty,
        positionSide=position_side
    )

    print(f"[SUCCESS] Opened {position_side} qty={qty}")


def handle_alert(alert_json):
    alert_id = alert_json.get("id")
    action = alert_json.get("action")
    side = alert_json.get("side")
    symbol = alert_json.get("symbol")
    qty = float(alert_json.get("amount", 0))
    leverage = int(alert_json.get("leverage", 1))

    with lock:
        now = time.time()
        if alert_id in recent_alerts and now - recent_alerts[alert_id] < ALERT_COOLDOWN:
            print("Duplicate alert skipped")
            return
        recent_alerts[alert_id] = now

    try:
        if action == "CLOSE":
            close_full_position(symbol, side)

        elif action == "OPEN":
            open_position(symbol, side, qty, leverage)

    except BinanceAPIException as e:
        print(f"Binance error: {e.message}")
    except Exception as e:
        print(f"Unexpected error: {e}")


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON"}), 400

    threading.Thread(target=handle_alert, args=(data,)).start()
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
