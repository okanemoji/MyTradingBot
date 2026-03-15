from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException
import os
import time
import threading
import queue
import json

# ===== ENV =====
API_KEY = os.environ.get("BINANCE_API_KEY")
API_SECRET = os.environ.get("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)

app = Flask(__name__)

# ===== CONFIG =====
ORDER_DELAY = 1.2
ALERT_COOLDOWN = 10

recent_alerts = {}
order_queue = queue.Queue()
lock = threading.Lock()

# ===== UTIL =====
def safe_order(symbol, side, qty, position_side, reduce=False):

    try:

        client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY if side=="BUY" else SIDE_SELL,
            type=FUTURE_ORDER_TYPE_MARKET,
            quantity=qty,
            positionSide=position_side,
            reduceOnly=reduce
        )

        print("ORDER SENT",symbol,side,qty)

    except BinanceAPIException as e:

        print("BINANCE ERROR",e.message)

        if "1003" in str(e):

            print("RATE LIMIT HIT - sleeping 60s")
            time.sleep(60)

    except Exception as e:
        print("UNKNOWN ERROR",e)


# ===== ALERT HANDLER =====
def handle_alert(data):

    symbol = data["symbol"]
    action = data["action"]
    side = data["side"]
    qty = float(data["amount"])

    position_side = "LONG" if side=="BUY" else "SHORT"

    if action == "OPEN":

        safe_order(symbol,side,qty,position_side,False)

    elif action == "CLOSE":

        safe_order(symbol,side,qty,position_side,True)


# ===== QUEUE =====
def enqueue_alert(data):

    alert_id = data.get("id")
    now = time.time()

    with lock:

        if alert_id in recent_alerts:

            if now - recent_alerts[alert_id] < ALERT_COOLDOWN:
                print("DUPLICATE ALERT SKIPPED")
                return

        recent_alerts[alert_id] = now

    order_queue.put(data)


# ===== WORKER =====
def order_worker():

    while True:

        item = order_queue.get()

        try:

            handle_alert(item)

        except Exception as e:

            print("WORKER ERROR",e)

        time.sleep(ORDER_DELAY)

        order_queue.task_done()


worker = threading.Thread(target=order_worker)
worker.daemon = True
worker.start()


# ===== ROUTES =====
@app.route("/",methods=["GET"])
def home():
    return jsonify({"status":"bot running"})


@app.route("/webhook",methods=["POST"])
def webhook():

    raw = request.data.decode("utf-8")

    try:
        data = json.loads(raw)
    except:
        print("INVALID JSON")
        return jsonify({"status":"ignored"})

    enqueue_alert(data)

    return jsonify({"status":"queued"})


# ===== RUN =====
if __name__ == "__main__":

    port = int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port)
