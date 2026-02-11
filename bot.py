from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv
import os, time, random, threading

# ================= ENV =================
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

# sync time
server_time = client.get_server_time()["serverTime"]
local_time = int(time.time() * 1000)
client.timestamp_offset = server_time - local_time

# ================= FLASK =================
app = Flask(__name__)
lock = threading.Lock()
last_order_time = 0

# ================= HUMAN-LIKE DELAY =================
def human_delay():
    time.sleep(random.uniform(1,5))

def cooldown_delay():
    global last_order_time
    with lock:
        elapsed = time.time() - last_order_time
        wait_time = max(0, random.uniform(2,3.5) - elapsed)
        if wait_time > 0:
            time.sleep(wait_time)
        last_order_time = time.time()

# ================= OPEN POSITION =================
def open_position(symbol, side, qty, leverage, sl_points, tp_points, price):
    position_side = "LONG" if side.upper() == "BUY" else "SHORT"
    order_side = SIDE_BUY if side.upper() == "BUY" else SIDE_SELL

    # set leverage
    client.futures_change_leverage(symbol=symbol, leverage=leverage)
    human_delay()
    cooldown_delay()

    # market order
    order = client.futures_create_order(
        symbol=symbol,
        side=order_side,
        type=ORDER_TYPE_MARKET,
        quantity=qty,
        positionSide=position_side
    )
    print(f"✅ Opened {side} {qty} {symbol} at {price}")

    # SL/TP OCO
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
        raw = request.data.decode("utf-8").strip()
        print("Raw alert:", raw)

        # parse key=value ต่อบรรทัด
        data = {}
        for line in raw.split("\n"):
            if "=" in line:
                k,v = line.split("=",1)
                data[k.strip()] = v.strip()

        # ตรวจ field
        required = ["action","side","symbol","amount","leverage","sl_points","tp_points","price"]
        for f in required:
            if f not in data:
                return jsonify({"error":f"missing {f}"}),400

        if data["action"].upper() != "OPEN":
            return jsonify({"error":"Only OPEN action supported"}),400

        # แปลง type
        symbol = data["symbol"]
        side = data["side"]
        qty = float(data["amount"])
        leverage = int(data["leverage"])
        sl_points = float(data["sl_points"])
        tp_points = float(data["tp_points"])
        price = float(data["price"])

        open_position(symbol, side, qty, leverage, sl_points, tp_points, price)
        return jsonify({"status":"ok"})

    except Exception as e:
        print("❌ ERROR:", e)
        return jsonify({"error": str(e)}), 400

@app.route("/test")
def test():
    return "Bot working"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
