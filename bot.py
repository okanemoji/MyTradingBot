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
ORDER_DELAY = 0.5  # delay ระหว่าง order
ALERT_COOLDOWN = 3  # วินาที

# ===== BINANCE =====
API_KEY = os.environ.get("BINANCE_API_KEY")
API_SECRET = os.environ.get("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

# เปลี่ยน leverage
client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

# ===== APP =====
app = Flask(__name__)

# ===== STATE =====
recent_alerts = {}
order_queue = queue.Queue()
lock = threading.Lock()

# ===== GET POSITION (One-way mode) =====
def get_position():
    positions = client.futures_position_information(symbol=SYMBOL)
    long_amt = 0
    short_amt = 0
    for p in positions:
        amt = float(p["positionAmt"])
        if p["positionSide"] == "LONG":
            long_amt = amt
        elif p["positionSide"] == "SHORT":
            short_amt = abs(amt)
    return long_amt, short_amt

# ===== PLACE ORDER (FLIP + OPEN) =====
def place_order(side):
    try:
        long_amt, short_amt = get_position()
        qty = LOT_SIZE
        side_upper = side.upper()

        # ถ้ามีตำแหน่งตรงข้ามอยู่ ให้ flip
        if side_upper == "BUY":
            if short_amt > 0:
                qty = short_amt + LOT_SIZE
        elif side_upper == "SELL":
            if long_amt > 0:
                qty = long_amt + LOT_SIZE
        else:
            print("INVALID SIDE:", side)
            return

        # สร้าง order market
        client.futures_create_order(
            symbol=SYMBOL,
            side=side_upper,
            type="MARKET",
            quantity=qty
        )
        print(time.strftime("%H:%M:%S"), "ORDER:", side_upper, "QTY:", qty)
        # รอให้ Binance update position
        time.sleep(0.3)

    except BinanceAPIException as e:
        print("BINANCE ERROR:", e.message)
        if "1003" in str(e):
            print("RATE LIMIT - SLEEP")
            time.sleep(60)

# ===== WORKER THREAD =====
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

# ===== WEBHOOK =====
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

# ===== HOME =====
@app.route("/")
def home():
    return "bot running"

# ===== RUN =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0", port=port)
