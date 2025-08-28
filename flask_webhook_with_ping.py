from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os
from math import floor

app = Flask(__name__)

# ======= CONFIG =======
API_KEY = os.getenv("BINANCE_API_KEY", "your_api_key")
API_SECRET = os.getenv("BINANCE_API_SECRET", "your_api_secret")
client = Client(API_KEY, API_SECRET)

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "alive"})

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print(f"Received Webhook: {data}")

    if not data or "signal" not in data:
        return jsonify({"status": "error", "message": "No signal"}), 400

    signal = data["signal"].lower()

    try:
        # ====== PING KEEP-ALIVE ======
        if signal == "ping":
            print("🔵 Keep-alive ping received.")
            return jsonify({"status": "ok", "message": "pong"}), 200

        # ====== BUY / SELL ======
        if signal in ["buy", "sell"]:
            sym = data.get("symbol", "BTCUSDT").upper()
            leverage = int(data.get("leverage", 125))
            amount_usd = float(data.get("amount", 100))

            # ตั้ง Leverage
            try:
                client.futures_change_leverage(symbol=sym, leverage=leverage)
            except Exception as e:
                print(f"⚠️ Set leverage error: {e}")

            # Mark price
            mark_price = float(client.futures_mark_price(symbol=sym)["markPrice"])
            qty = (amount_usd * leverage) / mark_price

            # ปัดตาม stepSize
            ex_info = client.futures_exchange_info()
            sym_info = next(s for s in ex_info["symbols"] if s["symbol"] == sym)
            step_size = 0.0
            for f in sym_info["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step_size = float(f["stepSize"])
                    break
            if step_size > 0:
                qty = float(f"{(floor(qty / step_size) * step_size):.10f}")

            order_side = SIDE_BUY if signal == "buy" else SIDE_SELL
            position_side = "LONG" if signal == "buy" else "SHORT"

            order = client.futures_create_order(
                symbol=sym,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                positionSide=position_side
            )

            print(f"✅ Open {signal.upper()} {sym} qty={qty}")
            return jsonify({"status": "success", "order": order}), 200

        # ====== CLOSE ======
        elif signal == "close":
            sym = data.get("symbol", "BTCUSDT").upper()
            side = data.get("side", "BUY").upper()  # BUY = ปิด long, SELL = ปิด short
            amount_usd = float(data.get("amount", 100))
            leverage = int(data.get("leverage", 125))

            # === 1) เช็ค Hedge Mode หรือ One-way ===
            mode = client.futures_get_position_mode()
            is_hedge = bool(mode.get("dualSidePosition"))

            # === 2) Mark price + ปัดขนาด ===
            mark_price = float(client.futures_mark_price(symbol=sym)["markPrice"])
            desired_qty = (amount_usd * leverage) / mark_price

            ex_info = client.futures_exchange_info()
            sym_info = next(s for s in ex_info["symbols"] if s["symbol"] == sym)
            step_size = 0.0
            for f in sym_info["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step_size = float(f["stepSize"])
                    break
            if step_size > 0:
                desired_qty = float(f"{(floor(desired_qty / step_size) * step_size):.10f}")

            # === 3) ดู position จริง ===
            pos_info = client.futures_position_information(symbol=sym)
            long_amt = 0.0
            short_amt = 0.0
            if is_hedge:
                for p in pos_info:
                    if p.get("positionSide") == "LONG":
                        long_amt = float(p.get("positionAmt", "0"))
                    elif p.get("positionSide") == "SHORT":
                        short_amt = float(p.get("positionAmt", "0"))
            else:
                amt = float(pos_info[0].get("positionAmt", "0"))
                long_amt = max(amt, 0.0)
                short_amt = max(-amt, 0.0)

            # === 4) เลือกฝั่งปิด + ขนาดจริง ===
            if is_hedge:
                if side == "BUY":   # ปิด LONG
                    live_qty = long_amt
                    close_side = SIDE_SELL
                    pos_side = "LONG"
                else:               # ปิด SHORT
                    live_qty = short_amt
                    close_side = SIDE_BUY
                    pos_side = "SHORT"
            else:
                if long_amt > 0:
                    live_qty = long_amt
                    close_side = SIDE_SELL
                    pos_side = None
                elif short_amt > 0:
                    live_qty = short_amt
                    close_side = SIDE_BUY
                    pos_side = None
                else:
                    return jsonify({"status": "noop", "message": "No open position"}), 200

            qty_to_close = min(abs(live_qty), desired_qty)

            if qty_to_close <= 0:
                return jsonify({"status": "noop", "message": "Nothing to close"}), 200

            # === 5) ส่งคำสั่งปิด ===
            if is_hedge:
                order = client.futures_create_order(
                    symbol=sym,
                    side=close_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=qty_to_close,
                    positionSide=pos_side,
                    reduceOnly=True
                )
            else:
                order = client.futures_create_order(
                    symbol=sym,
                    side=close_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=qty_to_close
                )

            print(f"✅ Partial close {sym}: side={side} qty={qty_to_close}")
            return jsonify({"status": "success", "order": order}), 200

        else:
            return jsonify({"status": "error", "message": "Unknown signal"}), 400

    except Exception as e:
        print(f"❌ Error executing order: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
