import os, time
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

def sync_time():
    try:
        server_time = client.get_server_time()["serverTime"]
        client.timestamp_offset = server_time - int(time.time() * 1000)
    except: pass

sync_time()

def close_all_by_side(symbol, pos_side):
    """ปิดสถานะฝั่ง LONG หรือ SHORT ให้เกลี้ยงหน้าตักจริง"""
    try:
        positions = client.futures_position_information(symbol=symbol)
        for p in positions:
            if p["positionSide"] == pos_side:
                amt = abs(float(p["positionAmt"]))
                if amt > 0:
                    side = SIDE_SELL if pos_side == "LONG" else SIDE_BUY
                    client.futures_create_order(
                        symbol=symbol, side=side, type=ORDER_TYPE_MARKET,
                        quantity=amt, positionSide=pos_side, reduceOnly=True
                    )
        client.futures_cancel_all_open_orders(symbol=symbol)
    except Exception as e: print(f"❌ Close {pos_side} Error: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    action = data.get("action", "").upper()
    symbol = data.get("symbol")
    
    try:
        if action == "CLOSE":
            close_all_by_side(symbol, "LONG")
            close_all_by_side(symbol, "SHORT")
            return jsonify({"status": "closed_all"}), 200

        if action in ["BUY", "SELL"]:
            qty = float(data.get("amount", 0))
            lev = int(data.get("leverage", 1))
            pos_side = "LONG" if action == "BUY" else "SHORT"
            opp_side = "SHORT" if action == "BUY" else "LONG"

            # 1. ปรับ Leverage
            client.futures_change_leverage(symbol=symbol, leverage=lev)
            
            # 2. ถ้าสลับฝั่ง ให้ล้างฝั่งตรงข้ามก่อนเปิดไม้ใหม่
            close_all_by_side(symbol, opp_side)
            
            # 3. เปิดไม้/สะสมไม้ (Hedge Mode)
            client.futures_create_order(
                symbol=symbol, side=SIDE_BUY if action == "BUY" else SIDE_SELL,
                type=ORDER_TYPE_MARKET, quantity=qty, positionSide=pos_side
            )
            return jsonify({"status": "success"}), 200

    except Exception as e:
        if "Timestamp" in str(e): sync_time()
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
