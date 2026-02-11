from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv
import os
import time
import random
import threading


# ================= ENV =================
load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# ================= CLIENT =================
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
print("Server Time:", client.get_server_time())


# ===== HARD SYNC TIME (ตัวนี้แหละที่หาย -1021) =====
server_time = client.get_server_time()["serverTime"]
local_time = int(time.time() * 1000)
client.timestamp_offset = server_time - local_time

app = Flask(__name__)

# ================= HUMAN LIKE DELAY =================
MIN_REACTION = 1      # วินาที
MAX_REACTION = 5

MIN_COOLDOWN = 2      # อย่างน้อย 2 วิ
MAX_COOLDOWN = 3.5    # สุ่มเพิ่มนิดหน่อย

last_order_time = 0
lock = threading.Lock()

def human_delay():
    # 1️⃣ Reaction time หลังได้สัญญาณ
    reaction = random.uniform(MIN_REACTION, MAX_REACTION)
    print(f"🧠 Human reaction delay: {reaction:.2f}s")
    time.sleep(reaction)

def cooldown_delay():
    # 2️⃣ Cooldown กันยิงถี่
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
def get_position(symbol, position_side):
    positions = client.futures_position_information(symbol=symbol)
    for p in positions:
        if p["positionSide"] == position_side and abs(float(p["positionAmt"])) > 0:
            return p
    return None

# ================= WEBHOOK =================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("📩 Received:", data)

    try:
        action = data.get("action")
        symbol = data["symbol"]

        # ===== CLOSE POSITION (100%) =====
        if action == "CLOSE":
            side = data["side"]
            position_side = "LONG" if side == "BUY" else "SHORT"
            close_side = SIDE_SELL if side == "BUY" else SIDE_BUY

            pos = get_position(symbol, position_side)
            if not pos:
                return jsonify({"status": "no position to close"})

            qty = abs(float(pos["positionAmt"]))

            human_delay()
            cooldown_delay()
            order = client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                positionSide=position_side
            )

            return jsonify({"status": "closed", "order": order})

        # ===== OPEN POSITION =====
        if action == "OPEN":
            side = data["side"]
            amount = float(data["amount"])
            leverage = int(data["leverage"])

            position_side = "LONG" if side == "BUY" else "SHORT"
            order_side = SIDE_BUY if side == "BUY" else SIDE_SELL

            client.futures_change_leverage(
                symbol=symbol,
                leverage=leverage
            )

            human_delay()
            cooldown_delay()
            order = client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=amount,
                positionSide=position_side
            )

            return jsonify({"status": "opened", "order": order})

        return jsonify({"error": "invalid action"})

    except Exception as e:
        print("❌ ERROR:", e)
        return jsonify({"error": str(e)}), 400


# ================= RUN =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
