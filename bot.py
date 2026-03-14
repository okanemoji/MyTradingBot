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

client = Client(API_KEY, API_SECRET)

app = Flask(__name__)

recent_alerts = {}
ALERT_COOLDOWN = 2
lock = threading.Lock()


# =========================
# UTIL
# =========================

def round_step_size(quantity, step_size):
    return math.floor(quantity / step_size) * step_size


# =========================
# PING ROUTE
# =========================

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "bot running"}), 200


@app.route("/ping", methods=["GET", "POST"])
def ping():
    return jsonify({"status": "ok"}), 200


# =========================
# POSITION CHECK
# =========================

def get_position(symbol):

    positions = client.futures_position_information(symbol=symbol)

    long_amt = 0
    short_amt = 0

    for pos in positions:

        if pos["positionSide"] == "LONG":
            long_amt = float(pos["positionAmt"])

        if pos["positionSide"] == "SHORT":
            short_amt = abs(float(pos["positionAmt"]))

    return long_amt, short_amt


# =========================
# CLOSE POSITION
# =========================

def close_position(symbol, side):

    positions = client.futures_position_information(symbol=symbol)

    for pos in positions:

        position_amt = float(pos["positionAmt"])
        position_side = pos["positionSide"]

        if position_amt == 0:
            continue

        if side == "BUY" and position_side == "LONG":

            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=FUTURE_ORDER_TYPE_MARKET,
                quantity=abs(position_amt),
                positionSide="LONG",
                reduceOnly=True
            )

            print(f"Closed LONG {abs(position_amt)}")

        elif side == "SELL" and position_side == "SHORT":

            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=FUTURE_ORDER_TYPE_MARKET,
                quantity=abs(position_amt),
                positionSide="SHORT",
                reduceOnly=True
            )

            print(f"Closed SHORT {abs(position_amt)}")


# =========================
# OPEN POSITION
# =========================

def open_position(symbol, side, qty, leverage):

    client.futures_change_leverage(symbol=symbol, leverage=leverage)

    info = client.futures_exchange_info()
    step_size = None

    for s in info['symbols']:
        if s['symbol'] == symbol:
            step_size = float(s['filters'][2]['stepSize'])
            break

    if step_size is None:
        print("Step size not found")
        return

    qty = round_step_size(qty, step_size)

    if qty <= 0:
        print("Quantity too small")
        return

    long_amt, short_amt = get_position(symbol)

    # กันเปิดซ้ำ
    if side == "BUY" and long_amt > 0:
        print("Already LONG")
        return

    if side == "SELL" and short_amt > 0:
        print("Already SHORT")
        return

    position_side = "LONG" if side == "BUY" else "SHORT"

    client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY if side == "BUY" else SIDE_SELL,
        type=FUTURE_ORDER_TYPE_MARKET,
        quantity=qty,
        positionSide=position_side
    )

    print(f"Opened {position_side} {qty}")


# =========================
# HANDLE ALERT
# =========================

def handle_alert(data):

    alert_id = data.get("id")
    action = data.get("action")
    side = data.get("side")
    symbol = data.get("symbol")

    qty = float(data.get("amount", 0))
    leverage = int(data.get("leverage", 1))

    if not alert_id or not action or not side or not symbol:
        print("Invalid alert")
        return

    with lock:

        now = time.time()

        if alert_id in recent_alerts and now - recent_alerts[alert_id] < ALERT_COOLDOWN:
            print("Duplicate alert")
            return

        recent_alerts[alert_id] = now

    try:

        if action == "CLOSE":
            close_position(symbol, side)

        elif action == "OPEN":
            open_position(symbol, side, qty, leverage)

    except BinanceAPIException as e:
        print(f"Binance error: {e.message}")

    except Exception as e:
        print(f"Unexpected error: {e}")


# =========================
# WEBHOOK
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    handle_alert(data)

    return jsonify({"status": "ok"}), 200


# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
