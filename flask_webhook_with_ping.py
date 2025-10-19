from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os
import requests
from math import floor

# ---------------- CONFIG ----------------
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TESTNET = False   # ตั้งเป็น True ถ้าจะใช้ Binance Futures Testnet

TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"

# ----------------------------------------
app = Flask(__name__)
client = Client(API_KEY, API_SECRET, testnet=TESTNET)
print(f"✅ Binance client initialized (Testnet={TESTNET})")

# ---------- Telegram ----------
def send_telegram_message(text: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        print(f"⚠️ Telegram send error: {e}")

# ---------- Helpers ----------
def get_symbol_step_size(symbol: str) -> float:
    info = client.futures_exchange_info()
    sym = next(s for s in info["symbols"] if s["symbol"] == symbol)
    for f in sym["filters"]:
        if f["filterType"] == "LOT_SIZE":
            return float(f["stepSize"])
    return 0.0

def floor_to_step(qty: float, step: float) -> float:
    if step <= 0:
        return float(qty)
    return float(f"{(floor(qty / step) * step):.10f}")

def usdt_to_contracts(symbol: str, amount_usd: float, leverage: int, price: float, step: float) -> float:
    raw = (float(amount_usd) * int(leverage)) / float(price)
    return floor_to_step(raw, step)

def get_position_mode_is_hedge() -> bool:
    try:
        res = client.futures_get_position_mode()
        raw = res.get("dualSidePosition")
        return raw if isinstance(raw, bool) else str(raw).lower() == "true"
    except Exception as e:
        print(f"⚠️ Cannot read position mode, assume ONE-WAY. err={e}")
        return False

# ---------- Routes ----------
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    print(f"📩 Received Webhook: {data}")

    if not data or "signal" not in data:
        return jsonify({"error": "No or invalid signal"}), 400

    signal = data["signal"].lower()
    symbol = data.get("symbol", "BTCUSDT").upper()
    amount = float(data.get("amount", 10))
    leverage = int(data.get("leverage", 125))
    is_hedge = get_position_mode_is_hedge()
    print(f"ℹ️ HedgeMode={is_hedge} | symbol={symbol}")

    # ตั้งค่า Leverage
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        print(f"⚠️ Leverage error: {e}")

    try:
        price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        step = get_symbol_step_size(symbol)
    except Exception as e:
        print(f"❌ Market fetch error: {e}")
        send_telegram_message(f"❌ Market fetch error: {e}")
        return jsonify({"error": str(e)}), 500

    # ---------- OPEN ORDER ----------
    if signal in ["buy", "sell"]:
        try:
            qty = usdt_to_contracts(symbol, amount, leverage, price, step)
            if qty <= 0:
                return jsonify({"error": "qty<=0"}), 400

            pos_side = "LONG" if signal == "buy" else "SHORT"

            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY if signal == "buy" else SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                positionSide=pos_side if is_hedge else None
            )

            msg = f"✅ Open {signal.upper()} {symbol} qty={qty}"
            print(msg)
            send_telegram_message(msg)
            return jsonify({"status": "success", "orderId": order.get("orderId"), "qty": qty}), 200

        except Exception as e:
            print(f"❌ Error executing order: {e}")
            send_telegram_message(f"❌ Error: {e}")
            return jsonify({"error": str(e)}), 500

    # ---------- CLOSE ORDER ----------
    elif signal == "close":
        side = data.get("side", "").upper()
        print(f"📩 Close signal received | side={side}")

        if side == "BUY":
            position_side = "LONG"
            close_side = "SELL"
        elif side == "SELL":
            position_side = "SHORT"
            close_side = "BUY"
        else:
            return jsonify({"error": "Missing or invalid 'side' for close signal"}), 400

        try:
            positions = client.futures_position_information(symbol=symbol)
            qty_to_close = 0
            for p in positions:
                if p["positionSide"].upper() == position_side and abs(float(p["positionAmt"])) > 0:
                    qty_to_close = abs(float(p["positionAmt"]))
                    break

            if qty_to_close > 0:
                order = client.futures_create_order(
                    symbol=symbol,
                    side=close_side,
                    type="MARKET",
                    quantity=qty_to_close,
                    positionSide=position_side if is_hedge else None,
                    reduceOnly=(not is_hedge)
                )
                msg = f"✅ Close {position_side} {symbol} qty={qty_to_close}"
                print(msg)
                send_telegram_message(msg)
            else:
                msg = f"⚠️ No {position_side} position to close for {symbol}"
                print(msg)
                send_telegram_message(msg)

        except Exception as e:
            print(f"❌ Close error: {e}")
            send_telegram_message(f"❌ Close error: {e}")
            return jsonify({"error": str(e)}), 500

        return jsonify({"status": "closed"}), 200

    else:
        return jsonify({"error": "Unknown signal"}), 400

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
