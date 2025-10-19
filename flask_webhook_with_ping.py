from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os
import requests
import threading
from math import floor

# ===================== CONFIG =====================
app = Flask(__name__)

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

client = Client(API_KEY, API_SECRET)

ENABLE_TELEGRAM = True
TELEGRAM_TIMEOUT = 10  # seconds

# ===================== TELEGRAM =====================
def _send_telegram_request(msg: str):
    """ส่งข้อความไป Telegram (ภายใน thread แยก)"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram not configured (token/chat_id missing)")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        resp = requests.post(url, data=payload, timeout=TELEGRAM_TIMEOUT)
        if resp.status_code != 200:
            print(f"⚠️ Telegram returned {resp.status_code}: {resp.text}")
    except Exception as ex:
        print(f"⚠️ Telegram send error: {ex}")

def send_telegram_message_async(msg: str):
    """ส่งข้อความแบบ async"""
    if not ENABLE_TELEGRAM:
        return
    t = threading.Thread(target=_send_telegram_request, args=(msg,), daemon=True)
    t.start()

# ===================== HELPERS =====================
def get_position_mode_is_hedge() -> bool:
    try:
        res = client.futures_get_position_mode()
        raw = res.get("dualSidePosition")
        return raw if isinstance(raw, bool) else str(raw).lower() == "true"
    except Exception as e:
        print(f"⚠️ Cannot read position mode, assume ONE-WAY. err={e}")
        return False

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

def read_live_qtys(symbol: str, is_hedge: bool) -> tuple[float, float]:
    info = client.futures_position_information(symbol=symbol)
    long_q = 0.0
    short_q = 0.0
    if is_hedge:
        for p in info:
            side = p.get("positionSide")
            amt = float(p.get("positionAmt", "0"))
            if side == "LONG":
                long_q = max(amt, 0.0)
            elif side == "SHORT":
                short_q = max(abs(amt), 0.0)
    else:
        amt = float(info[0].get("positionAmt", "0"))
        if amt > 0:
            long_q = amt
        elif amt < 0:
            short_q = abs(amt)
    return long_q, short_q

# ===================== WEBHOOK =====================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    print(f"📩 Received Webhook: {data}")
    if not data or "signal" not in data:
        return jsonify({"status": "error", "message": "No/invalid signal"}), 400

    signal   = str(data["signal"]).lower()
    symbol   = str(data.get("symbol", "BTCUSDT")).upper()
    amount   = float(data.get("amount", 10))
    leverage = int(data.get("leverage", 125))
    side_in  = str(data.get("side", "")).upper()

    # ping check
    if signal == "ping" or data.get("type") == "ping":
        print("🟢 Received ping alert → skip Binance API.")
        return jsonify({"status": "ok", "message": "ping received"}), 200

    is_hedge = get_position_mode_is_hedge()
    print(f"ℹ️ HedgeMode={is_hedge} | symbol={symbol}")

    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        print(f"⚠️ Leverage error: {e}")

    try:
        price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        step  = get_symbol_step_size(symbol)
    except Exception as e:
        print(f"❌ Market info error: {e}")
        send_telegram_message_async(f"❌ Market info error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

    try:
        if signal in ("buy", "sell"):
            qty = usdt_to_contracts(symbol, amount, leverage, price, step)
            if qty <= 0:
                return jsonify({"status": "error", "message": "qty<=0"}), 400

            if is_hedge:
                pos_side = "LONG" if signal == "buy" else "SHORT"
                order = client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_BUY if signal == "buy" else SIDE_SELL,
                    type=ORDER_TYPE_MARKET,
                    quantity=qty,
                    positionSide=pos_side
                )
            else:
                order = client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_BUY if signal == "buy" else SIDE_SELL,
                    type=ORDER_TYPE_MARKET,
                    quantity=qty
                )

            msg = f"✅ Open {signal.upper()} {symbol} qty={qty}"
            print(msg)
            send_telegram_message_async(msg)
            return jsonify({"status": "success", "orderId": order.get('orderId'), "qty": qty}), 200

        elif signal == "close":
            desired = usdt_to_contracts(symbol, amount, leverage, price, step)
            long_qty, short_qty = read_live_qtys(symbol, is_hedge)

            if side_in == "BUY":
                live = long_qty
                close_side = SIDE_SELL
                pos_side = "LONG"
            elif side_in == "SELL":
                live = short_qty
                close_side = SIDE_BUY
                pos_side = "SHORT"
            else:
                return jsonify({"status": "error", "message": "close needs side=BUY|SELL"}), 400

            raw_close = min(live, desired)
            qty_to_close = floor_to_step(raw_close, step)
            print(f"[DEBUG] close {symbol} | live={live}, desired={desired}, final={qty_to_close}")

            if qty_to_close <= 0:
                return jsonify({"status": "noop", "message": "Nothing to close"}), 200

            if is_hedge:
                order = client.futures_create_order(
                    symbol=symbol,
                    side=close_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=qty_to_close,
                    positionSide=pos_side
                )
            else:
                order = client.futures_create_order(
                    symbol=symbol,
                    side=close_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=qty_to_close,
                    reduceOnly=True
                )

            msg = f"✅ Close {symbol} qty={qty_to_close}"
            print(msg)
            send_telegram_message_async(msg)
            return jsonify({"status": "success", "orderId": order.get('orderId'), "qty": qty_to_close}), 200

        else:
            return jsonify({"status": "error", "message": "Unknown signal"}), 400

    except Exception as e:
        msg = f"❌ Error: {e}"
        print(msg)
        send_telegram_message_async(msg)
        return jsonify({"status": "error", "message": str(e)}), 500

# ===================== STATUS =====================
@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    print("✅ Binance client initialized (Testnet=False)")
    app.run(host="0.0.0.0", port=5000)
