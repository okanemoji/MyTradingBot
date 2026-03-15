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
LEVERAGE = 50
LOT_SIZE = 0.01
ORDER_DELAY = 1.0
ALERT_COOLDOWN = 5

# ===== BINANCE =====
API_KEY = os.environ.get("BINANCE_API_KEY")
API_SECRET = os.environ.get("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

client.futures_change_leverage(
    symbol=SYMBOL,
    leverage=LEVERAGE
)

# ===== APP =====
app = Flask(__name__)

# ===== STATE =====
recent_alerts = {}
order_queue = queue.Queue()
lock = threading.Lock()

# ===== GET POSITION =====
def get_position():
    positions = client.futures_position_information(symbol=SYMBOL)
    for p in positions:
        amt = float(p["positionAmt"])
        if amt != 0:
            return amt
    return 0

# ===== PLACE ORDER (FLIP LOGIC) =====
def place_order(side):
    try:
        pos = get_position()
        qty = LOT_SIZE

        # ถ้า opposite side ให้ flip quantity
        if side.upper() == "BUY":
            order_side = "BUY"
            if pos < 0:
                qty = abs(pos) + LOT_SIZE
        elif side.upper() == "SELL":
            order_side = "SELL"
            if pos > 0:
                qty = abs(pos) + LOT_SIZE
        else:
            print("INVALID SIDE:", side)
            return

        client.futures_create_order(
            symbol=SYMBOL,
            side=order_side,
            type="MARKET",
            quantity=qty
        )

        print(time.strftime("%H:%M:%S"), "ORDER:", order_side, "QTY:", qty)

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
