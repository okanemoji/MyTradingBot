from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os
from math import floor

app = Flask(__name__)

# ---------- CONFIG ----------
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# ---------- Helper ----------
def get_binance_client() -> Client:
    """สร้าง client เฉพาะเวลาต้องใช้ ลดการ ping Binance โดยไม่จำเป็น"""
    return Client(API_KEY, API_SECRET)

def get_position_mode_is_hedge(client: Client) -> bool:
    """เช็กว่าใช้ Hedge mode หรือไม่"""
    try:
        res = client.futures_get_position_mode()
        raw = res.get("dualSidePosition")
        return raw if isinstance(raw, bool) else str(raw).lower() == "true"
    except Exception as e:
        print(f"⚠️ Cannot read position mode, assume ONE-WAY. err={e}")
        return False

def get_symbol_step_size(client: Client, symbol: str) -> float:
    """LOT_SIZE step สำหรับปัดขนาด order"""
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

def read_live_qtys(client: Client, symbol: str, is_hedge: bool) -> tuple[float, float]:
    """คืนค่า (long_qty, short_qty)"""
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


# ---------- Routes ----------
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    print(f"\n📩 Received Webhook: {data}")

    # ป้องกัน JSON ว่าง
    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    signal = str(data.get("signal", "")).lower().strip()
    symbol = str(data.get("symbol", "BTCUSDT")).upper()
    amount = float(data.get("amount", 10))
    leverage = int(data.get("leverage", 125))
    side_in = str(data.get("side", "")).upper()

    # 🔒 ป้องกัน ping จาก TradingView ไม่ให้ยิง Binance
    if signal == "ping":
        print("🔁 Ping received — skipping Binance API call.")
        return jsonify({"status": "ok", "message": "Ping acknowledged"}), 200

    client = get_binance_client()

    # ตรวจ hedge mode
    is_hedge = get_position_mode_is_hedge(client)
    print(f"ℹ️ HedgeMode={is_hedge} | symbol={symbol}")

    # ตั้ง leverage
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        print(f"⚠️ Leverage error: {e}")

    # ดึงราคาและ step
    try:
        price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        step = get_symbol_step_size(client, symbol)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Market data error: {e}"}), 500

    try:
        # ---------- สัญญาณ BUY / SELL ----------
        if signal in ("buy", "sell"):
            qty = usdt_to_contracts(symbol, amount, leverage, price, step)
            if qty <= 0:
                return jsonify({"status": "error", "message": "qty<=0"}), 400

            pos_side = "LONG" if signal == "buy" else "SHORT"
            side_api = SIDE_BUY if signal == "buy" else SIDE_SELL

            params = {
                "symbol": symbol,
                "side": side_api,
                "type": ORDER_TYPE_MARKET,
                "quantity": qty
            }

            if is_hedge:
                params["positionSide"] = pos_side

            order = client.futures_create_order(**params)
            print(f"✅ Open {signal.upper()} {symbol} qty={qty}")
            return jsonify({"status": "success", "orderId": order.get("orderId"), "qty": qty}), 200

        # ---------- สัญญาณ CLOSE ----------
        elif signal == "close":
            desired = usdt_to_contracts(symbol, amount, leverage, price, step)
            long_qty, short_qty = read_live_qtys(client, symbol, is_hedge)

            if side_in == "BUY":   # close LONG
                live = long_qty
                close_side = SIDE_SELL
                pos_side = "LONG"
            elif side_in == "SELL":  # close SHORT
                live = short_qty
                close_side = SIDE_BUY
                pos_side = "SHORT"
            else:
                return jsonify({"status": "error", "message": "close needs side=BUY|SELL"}), 400

            raw_close = min(live, desired)
            qty_to_close = floor_to_step(raw_close, step)
            print(f"[DEBUG] close {symbol} {side_in}: live={live}, desired={desired}, step={step}, final={qty_to_close}")

            if qty_to_close <= 0:
                return jsonify({"status": "noop", "message": "Nothing to close"}), 200

            params = {
                "symbol": symbol,
                "side": close_side,
                "type": ORDER_TYPE_MARKET,
                "quantity": qty_to_close
            }

            if is_hedge:
                params["positionSide"] = pos_side
            else:
                params["reduceOnly"] = True

            order = client.futures_create_order(**params)
            print(f"✅ Close {symbol} qty={qty_to_close}")
            return jsonify({"status": "success", "orderId": order.get('orderId'), "qty": qty_to_close}), 200

        else:
            return jsonify({"status": "error", "message": "Unknown signal"}), 400

    except Exception as e:
        print(f"❌ Error executing order: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "Bot online"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
