from flask import Flask, request
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv
import os
import json

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

app = Flask(__name__)

def get_client():
    client = Client(API_KEY, API_SECRET)
    client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
    return client

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = json.loads(request.data)
    except:
        return "bad json", 400

    action = data.get("action")
    side   = data.get("side")
    symbol = data.get("symbol")
    amount = float(data.get("amount", 0))
    leverage = int(data.get("leverage", 1))

    client = get_client()

    # ===== OPEN POSITION =====
    if action == "OPEN":
        position_side = "LONG" if side == "BUY" else "SHORT"
        order_side = SIDE_BUY if side == "BUY" else SIDE_SELL

        try:
            client.futures_change_leverage(symbol=symbol, leverage=leverage)
            client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=amount,
                positionSide=position_side
            )
        except Exception as e:
            print("OPEN ERROR:", e)

    # ===== CLOSE POSITION (force) =====
    if action in ["CLOSEBUY", "CLOSESELL"]:
        position_side = "LONG" if action=="CLOSEBUY" else "SHORT"
        order_side = SIDE_SELL if action=="CLOSEBUY" else SIDE_BUY

        try:
            # Get positions
            positions = client.futures_position_information(symbol=symbol)
            for p in positions:
                if p["positionSide"]==position_side and float(p["positionAmt"])!=0:
                    qty = abs(float(p["positionAmt"]))
                    client.futures_create_order(
                        symbol=symbol,
                        side=order_side,
                        type=ORDER_TYPE_MARKET,
                        quantity=qty,
                        positionSide=position_side,
                        reduceOnly=True
                    )
        except Exception as e:
            print("CLOSE ERROR:", e)

    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
