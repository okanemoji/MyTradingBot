from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os
from math import floor

app = Flask(__name__)

API_KEY = os.getenv("BINANCE_API_KEY", "your_api_key")
API_SECRET = os.getenv("BINANCE_API_SECRET", "your_api_secret")
client = Client(API_KEY, API_SECRET)

def get_is_hedge_mode():
    """Return True if Hedge mode (dualSidePosition), else False."""
    mode = client.futures_get_position_mode()  # e.g. {'dualSidePosition': True} OR {'dualSidePosition': 'false'}
    raw = mode.get("dualSidePosition")
    return str(raw).lower() == "true" if not isinstance(raw, bool) else raw

def get_step_size(symbol):
    ex_info = client.futures_exchange_info()
    sym_info = next(s for s in ex_info["symbols"] if s["symbol"] == symbol)
    for f in sym_info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            return float(f["stepSize"])
    return 0.0

def round_step(qty, step):
    if step <= 0:
        return float(qty)
    return float(f"{(floor(qty / step) * step):.10f}")

def calc_qty_from_usd(symbol, amount_usd, leverage):
    mark_price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
    qty = (float(amount_usd) * int(leverage)) / mark_price
    step = get_step_size(symbol)
    return round_step(qty, step)

def read_live_position_qty(symbol, is_hedge):
    """Return (long_qty, short_qty) as positive numbers (0 if none)."""
    pos_info = client.futures_position_information(symbol=symbol)
    long_amt = 0.0
    short_amt = 0.0
    if is_hedge:
        for p in pos_info:
            side = p.get("positionSide")
            amt = float(p.get("positionAmt", "0"))
            if side == "LONG":
                long_amt = max(amt, 0.0)
            elif side == "SHORT":
                short_amt = max(abs(amt), 0.0)
    else:
        amt = float(pos_info[0].get("positionAmt", "0"))
        if amt > 0:
            long_amt = amt
        elif amt < 0:
            short_amt = abs(amt)
    return long_amt, short_amt

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    print(f"Received Webhook: {data}")
    if not data or "signal" not in data:
        return jsonify({"status": "error", "message": "No signal"}), 400

    signal = str(data["signal"]).lower()
    symbol = str(data.get("symbol", "BTCUSDT")).upper()
    leverage = int(data.get("leverage", 125))
    amount_usd = float(data.get("amount", 100))

    try:
        # === READ MODE ===
        is_hedge = get_is_hedge_mode()
        print(f"ℹ️ HedgeMode={is_hedge} | symbol={symbol}")

        # === CLOSE ===
        if signal == "close":
            side = str(data.get("side", "BUY")).upper()  # BUY = close long, SELL = close short
            desired = calc_qty_from_usd(symbol, amount_usd, leverage)
            long_qty, short_qty = read_live_position_qty(symbol, is_hedge)

            if side == "BUY":   # Close LONG
                live = long_qty
                close_side = SIDE_SELL
                pos_side = "LONG"
            else:               # Close SHORT
                live = short_qty
                close_side = SIDE_BUY
                pos_side = "SHORT"

            qty_to_close = min(live, desired)
            qty_to_close = round_step(qty_to_close, get_step_size(symbol))

            # DEBUG log
            print(f"[DEBUG] Request close side={side}, live_long={long_qty}, live_short={short_qty}, "
                  f"desired={desired}, final_qty={qty_to_close}, pos_side={pos_side}")

            if qty_to_close <= 0:
                return jsonify({"status": "noop", "message": "Nothing to close"}), 200

            if is_hedge:
                order = client.futures_create_order(
                    symbol=symbol,
                    side=close_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=qty_to_close,
                    positionSide=pos_side,
                    reduceOnly=True
                )
            else:
                order = client.futures_create_order(
                    symbol=symbol,
                    side=close_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=qty_to_close
                )

            print(f"✅ Close {symbol}: side={side}, qty={qty_to_close}, mode={'HEDGE' if is_hedge else 'ONE-WAY'}")
            return jsonify({"status": "success", "orderId": order.get('orderId'), "qty": qty_to_close}), 200

        else:
            return jsonify({"status": "error", "message": "Unknown signal"}), 400

    except Exception as e:
        print(f"❌ Error executing order: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
