from flask import Flask, request, jsonify
from binance.client import Client
from binance.exceptions import BinanceAPIException
import os
import json
import threading
import queue
import time

# ===== CONFIG =====
SYMBOL = "ETHUSDT"
LEVERAGE = 100
LOT_SIZE = 2
ORDER_DELAY = 0.5   # delay หลังส่ง order
ALERT_COOLDOWN = 1  # ป้องกัน duplicate alert

# ===== BINANCE =====
API_KEY = os.environ.get("BINANCE_API_KEY")
API_SECRET = os.environ.get("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

# ===== APP =====
app = Flask(__name__)

# ===== STATE =====
recent_alerts = {}
order_queue = queue.Queue()
lock = threading.Lock()

# เก็บ position ล่าสุดใน memory เพื่อลด API request
current_position = 0

# ===== PLACE ORDER (FLIP LOGIC WITHOUT GET_POSITION) =====
def place_order(side):
    global current_position

    try:
        qty = LOT_SIZE
        order_side = side.upper()

        # flip position ถ้าตรงข้าม
        if order_side == "BUY" and current_position < 0:
            qty = abs(current_position) + LOT_SIZE
        elif order_side == "SELL" and current_position > 0:
            qty = abs(current_position) + LOT_SIZE

        # ส่ง order
        client.futures_create_order(
            symbol=SYMBOL,
            side=order_side,
            type="MARKET",
            quantity=qty
        )

        print(time.strftime("%H:%M:%S"), "ORDER:", order_side, "QTY:", qty)

        # อัปเดต position memory
        if order_side == "BUY":
            current_position += qty
        else:
            current_position -= qty

    except BinanceAPIException as e:
        print("BINANCE ERROR:", e.message)
        if "1003" in str(e):
            print("RATE LIMIT - SLEEP")
            time.sleep(60)

# ===== WORKER =====
def worker():
    while True:
        data = order_queue.get()
        if data is None:
            break
        side = data["side"]
        place_order(side)
        time.sleep(ORDER_DELAY)
        order_queue.task_done()

thread = threading.Thread(target=worker)
thread.daemon = True
thread.start()

# ===== ALERT QUEUE =====
def enqueue_alert(data):
    alert_id = data.get("id")
    now = time.time()
    with lock:
        if alert_id in recent_alerts:
            if now - recent_alerts[alert_id] < ALERT_COOLDOWN:
                print("DUPLICATE ALERT")
                return
        recent_alerts[alert_id] = now
    order_queue.put(data)

# ===== PING KEEP ALIVE =====
@app.route("/ping", methods=["POST","GET"])
def ping():
    print(time.strftime("%H:%M:%S"), "PING received")
    return jsonify({"status":"ok"})

# ===== ROUTES =====
@app.route("/webhook", methods=["POST"])
def webhook():
    raw = request.data.decode()
    try:
        data = json.loads(raw)
    except:
        print("INVALID JSON")
        return jsonify({"status":"ignored"})

    if "side" not in data or data["side"].upper() not in ["BUY","SELL"]:
        return jsonify({"status":"ignored"})

    enqueue_alert(data)
    return jsonify({"status":"queued"})

@app.route("/")
def home():
    return "bot running"

# ===== RUN =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0", port=port)
