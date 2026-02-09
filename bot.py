from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv
import os
import time

# ================= ENV =================
load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

app = Flask(__name__)

# ================= SAFE CLIENT =================
# ❗ สร้าง client เฉพาะตอนมี webhook (กันโดน -1003)
def get_client():
    client = Client(API_KEY, API_SECRET)
    client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
    return client

# ================= TIME SYNC =================
def get_timestamp(client):
    server_time = client.get_server_time()["serverTime"]
    local_time = int(time.time() * 1000)
    return local_time + (server_time - local_time)

# ================= HEALTH CHECK =================
# กัน Render ยิง HEAD /
@app.route("/", methods=["GET", "HEAD"])
def home():
    return "OK"

# ================= UTILS =================
def get_position(client, symbol, position_side):
    positions = client.futures_position_information(symbol=symbol)
    for p in positions:
        if (
            p["positionSide"] == position_side
            and abs(float(p["positionAmt"])) > 0
        ):
            return p
    return None

# ================= WEBHOOK =================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("📩 Received:", data)

    try:
        client = get_client()
        ts = get_timestamp(client)

        action = data.get("action")
        symbol = data["symbol"]

        # ===== CLOSE POSITION (ปิด 100%) =====
        if action == "CLOSE":
            side = data["side"]  # BUY หรือ SELL

            position_side = "LONG" if side == "BUY" else "SHORT"
            close_side = SIDE_SELL if side == "BUY" else SIDE_BUY

            pos = get_position(client, symbol, position_side)
            if not pos:
                return jsonify({"status": "no position to close"})

            qty = abs(float(pos["positionAmt"]))

            order = client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                positionSide=position_side,
                reduceOnly=True,
                timestamp=ts
            )

            return jsonify({"status": "closed", "order": order})

        # ===== OPEN POSITION =====
        if action == "OPEN":
            side = data["side"]          # BUY / SELL
            amount = float(data["amount"])
            leverage = int(data["leverage"])

            position_side = "LONG" if side == "BUY" else "SHORT"
            order_side = SIDE_BUY if side == "BUY" else SIDE_SELL

            client.futures_change_leverage(
                symbol=symbol,
                leverage=leverage
            )

            order = client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=amount,
                positionSide=position_side,
                timestamp=ts
            )

            return jsonify({"status": "opened", "order": order})

        return jsonify({"error": "invalid action"})

    except Exception as e:
        print("❌ ERROR:", e)
        return jsonify({"error": str(e)}), 400


# ================= RUN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # สำคัญสำหรับ Render
    app.run(host="0.0.0.0", port=port)
