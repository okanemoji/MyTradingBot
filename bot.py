from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv
import os, time, random, threading, json

# ================= ENV =================
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# ================= CLIENT =================
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
server_time = client.get_server_time()["serverTime"]
local_time = int(time.time() * 1000)
client.timestamp_offset = server_time - local_time

# ================= FLASK =================
app = Flask(__name__)

# ================= HUMAN-LIKE DELAY =================
MIN_REACTION = 1
MAX_REACTION = 5
MIN_COOLDOWN = 2
MAX_COOLDOWN = 3.5
last_order_time = 0
lock = threading.Lock()

def human_delay():
    reaction = random.uniform(MIN_REACTION, MAX_REACTION)
    print(f"🧠 Human reaction delay: {reaction:.2f}s")
    time.sleep(reaction)

def cooldown_delay():
    global last_order_time
    with lock:
        now = time.time()
        elapsed = now - last_order_time
        random_cooldown = random.uniform(MIN_COOLDOWN, MAX_COOLDOWN)
        if elapsed < random_cooldown:
            wait_time = random_cooldown - elapsed
            print(f"⏳ Cooldown delay: {wait_time:.2f}s")
            time.sleep(wait_time)
        last_order_time = time.time()

# ================= UTILS =================
def open_position(symbol, side, qty, leverage, sl_points, tp_points, price):
    position_side = "LONG" if side.upper() == "BUY" else "SHORT"
    order_side = SIDE_BUY if side.upper() == "BUY" else SIDE_SELL

    if qty <= 0:
        print("⚠ Quantity too small")
        return None

    client.futures_change_leverage(symbol=symbol, leverage=leverage)
    human_delay()
    cooldown_delay()

    # เปิด order market
    order = client.futures_create_order(
        symbol=symbol,
        side=order_side,
        type=ORDER_TYPE_MARKET,
        quantity=qty,
        positionSide=position_side
    )
    print(f"✅ Opened {side} {qty} {symbol} at {price}")

    # คำนวณ SL/TP เป็นราคาจริง
    if side.upper() == "BUY":
        sl_price = price - sl_points
        tp_price = price + tp_points
        stop_side = SIDE_SELL
    else:
        sl_price = price + sl_points
        tp_price = price - tp_points
        stop_side = SIDE_BUY

    try:
        client.futures_create_oco_order(
            symbol=symbol,
            side=stop_side,
            quantity=qty,
            price=tp_price,
            stopPrice=sl_price,
            stopLimitPrice=sl_price,
            stopLimitTimeInForce="GTC"
        )
        print(f"🛡 SL/TP set: SL={sl_price}, TP={tp_price}")
    except Exception as e:
        print("❌ Failed to set SL/TP:", e)

    return order

# ================= WEBHOOK =================
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw = request.data.decode("utf-8")  # รับ string จาก TV
        print("Raw data:", raw)
        data = json.loads(raw)               # แปลงเป็น JSON

        # ตรวจ field
        required_fields = ["action","side","symbol","amount","leverage","sl_points","tp_points","price"]
        for f in required_fields:
            if f not in data:
                return jsonify({"error": f"missing {f}"}), 400

        if data["action"] != "OPEN":
            return jsonify({"error": "Only OPEN action supported"}), 400

        open_position(
            symbol=data["symbol"],
            side=data["side"],
            qty=float(data["amount"]),
            leverage=int(data["leverage"]),
            sl_points=float(data["sl_points"]),
            tp_points=float(data["tp_points"]),
            price=float(data["price"])
        )

        return jsonify({"status": "opened"})

    except Exception as e:
        print("❌ ERROR:", e)
        return jsonify({"error": str(e)}), 400

# ================= TEST ROUTE =================
@app.route("/test")
def test():
    return "Bot working"

# ================= RUN =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
