import os, time
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# เชื่อมต่อ API (Testnet)
client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

def sync_time():
    try:
        server_time = client.get_server_time()["serverTime"]
        client.timestamp_offset = server_time - int(time.time() * 1000)
    except: pass

sync_time()

def force_close_side(symbol, pos_side):
    """ ปิดสถานะฝั่งที่ระบุให้เกลี้ยง (LONG หรือ SHORT) """
    try:
        positions = client.futures_position_information(symbol=symbol)
        for p in positions:
            if p['symbol'] == symbol and p['positionSide'] == pos_side:
                amt = abs(float(p['positionAmt']))
                if amt > 0:
                    side_to_send = SIDE_SELL if pos_side == "LONG" else SIDE_BUY
                    client.futures_create_order(
                        symbol=symbol, side=side_to_send, type=ORDER_TYPE_MARKET,
                        quantity=amt, positionSide=pos_side, reduceOnly=True
                    )
                    print(f"🧹 Force Closed {pos_side}: {amt}")
        client.futures_cancel_all_open_orders(symbol=symbol)
    except Exception as e:
        print(f"❌ Close Error ({pos_side}): {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data: return jsonify({"status": "no data"}), 400

    action = data.get("action", "").upper()
    close_opp = data.get("close_opp", "").upper() # คำสั่งปิดที่แนบมา
    symbol = data.get("symbol")
    
    print(f"📩 Signal: {action} | Close Opp: {close_opp} | Symbol: {symbol}")

    try:
        # 1. จัดการคำสั่ง "ปิด" ที่แนบมากับสัญญาณหลัก หรือสั่งปิดแยก
        if close_opp == "CLOSE_SHORT" or action == "CLOSE_SHORT":
            force_close_side(symbol, "SHORT")
        if close_opp == "CLOSE_LONG" or action == "CLOSE_LONG":
            force_close_side(symbol, "LONG")
        if action == "CLOSE":
            force_close_side(symbol, "LONG")
            force_close_side(symbol, "SHORT")

        # 2. จัดการคำสั่ง "เปิด/เพิ่มไม้" (BUY/SELL)
        if action in ["BUY", "SELL"]:
            qty = float(data.get("amount", 0))
            lev = int(data.get("leverage", 50))
            pos_side = "LONG" if action == "BUY" else "SHORT"
            order_side = SIDE_BUY if action == "BUY" else SIDE_SELL

            client.futures_change_leverage(symbol=symbol, leverage=lev)
            client.futures_create_order(
                symbol=symbol, side=order_side, type=ORDER_TYPE_MARKET,
                quantity=qty, positionSide=pos_side
            )
            print(f"✅ {action} Executed: {qty} on {pos_side}")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        if "Timestamp" in str(e): sync_time()
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
