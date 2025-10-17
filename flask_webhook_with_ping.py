# ======================================================
# 🚀 FLASK WEBHOOK BOT (Manual Signal + Ping Safe)
# ======================================================
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os
from math import floor

app = Flask(__name__)

# ---------- CONFIG ----------
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
USE_TESTNET = os.getenv("USE_TESTNET", "false").lower() == "true"

client = Client(API_KEY, API_SECRET, testnet=USE_TESTNET)
print(f"✅ Binance client initialized (Testnet={USE_TESTNET})")

# ---------- HELPERS ----------
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
    long_q = short_q = 0.0
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

# ---------- ROUTES ----------
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    print(f"📩 Received Webhook: {data}")

    # ✅ Ping alert → skip Binance API
    if data and data.get("type") == "ping":
        print("🟢 Received ping alert → skip Binance API.")
        return jsonify({"status": "ok", "message": "Ping skipped"}), 200

    # ❌ No signal → return error
    if not data or "signal" not in data:
        return jsonify({"status": "error", "message": "No or invalid signal"}), 400

    signal   = str(data["signal"]).lower()
    symbol   = str(data.get("symbol", "BTCUSDT")).replace("BINANCE:", "").upper()
    amount   = float(data.get("amount", 10))
    leverage = int(data.get("leverage", 125))
    side_in  = str(data.get("side", "")).upper()

    # read mode & market info
    is_hedge = get_position_mode_is_hedge()
    print(f"ℹ️ HedgeMode={is_hedge} | symbol={symbol}")

    # set leverage (best effort)
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        print(f"⚠️ Leverage error (ignored): {e}")

    try:
        price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        step  = get_symbol_step_size(symbol)
    except Exception as e:
        print(f"❌ Market/step fetch error: {e}")
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
            print(f"✅ Open {signal.upper()} {symbol} qty={qty}")
            return jsonify({"status": "success", "orderId": order.get("orderId"), "qty": qty}), 200

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

            qty_to_close = floor_to_step(min(live, desired), step)
            print(f"[DEBUG] close side={side_in}, live_long={long_qty}, live_short={short_qty}, "
                  f"desired={desired}, step={step}, final_qty={qty_to_close}, pos_side={pos_side}")

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

            print(f"✅ Close {symbol}: qty={qty_to_close}, mode={'HEDGE' if is_hedge else 'ONE-WAY'}")
            return jsonify({"status": "success", "orderId": order.get('orderId'), "qty": qty_to_close}), 200

        else:
            return jsonify({"status": "error", "message": "Unknown signal"}), 400

    except Exception as e:
        print(f"❌ Error executing order: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})

# ---------- RUN SERVER ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
