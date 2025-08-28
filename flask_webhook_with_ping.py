from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os, time

app = Flask(__name__)

# โหลด API key จาก environment variable
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)

# ตรวจสอบ Hedge Mode
def is_hedge_mode():
    try:
        res = client.futures_get_position_mode()
        return res["dualSidePosition"]
    except Exception as e:
        print(f"❌ Error checking hedge mode: {e}")
        return False

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    print(f"Received Webhook: {data}")

    if not data or "signal" not in data:
        return jsonify({"error": "Invalid webhook"}), 400

    signal = data["signal"]
    symbol = data.get("symbol", "BTCUSDT")
    amount = float(data.get("amount", 0))
    leverage = int(data.get("leverage", 125))
    side = data.get("side", "").upper()

    hedge = is_hedge_mode()
    print(f"ℹ️ HedgeMode={hedge} | symbol={symbol}")

    try:
        # ตั้ง leverage
        try:
            client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except Exception as e:
            print(f"⚠️ Could not change leverage: {e}")

        # แปลง USDT → contracts
        last_price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
        qty = round((amount * leverage) / last_price, 3)  # ปัด 3 ทศนิยมพอ

        if signal == "buy":
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                positionSide="LONG" if hedge else None
            )
            print(f"✅ Buy order: {order}")

        elif signal == "sell":
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                positionSide="SHORT" if hedge else None
            )
            print(f"✅ Sell order: {order}")

        elif signal == "close":
            # ดึงสถานะปัจจุบัน
            positions = client.futures_position_information(symbol=symbol)
            long_amt = short_amt = 0.0
            for p in positions:
                if p["positionSide"] == "LONG":
                    long_amt = float(p["positionAmt"])
                elif p["positionSide"] == "SHORT":
                    short_amt = float(p["positionAmt"])

            desired = (amount * leverage) / last_price
            final_qty = min(abs(long_amt if side == "BUY" else short_amt), desired)

            pos_side = "LONG" if side == "BUY" else "SHORT"
            close_side = SIDE_SELL if side == "BUY" else SIDE_BUY

            print(f"[DEBUG] Request close side={side}, live_long={long_amt}, live_short={short_amt}, desired={desired}, final_qty={final_qty}, pos_side={pos_side}")

            if hedge:
                # Hedge mode: ไม่ต้องใส่ reduceOnly
                order = client.futures_create_order(
                    symbol=symbol,
                    side=close_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=final_qty,
                    positionSide=pos_side
                )
            else:
                # One-way mode: ต้องใช้ reduceOnly
                order = client.futures_create_order(
                    symbol=symbol,
                    side=close_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=final_qty,
                    reduceOnly=True
                )

            print(f"✅ Close order: {order}")

        else:
            return jsonify({"error": "Unknown signal"}), 400

    except Exception as e:
        print(f"❌ Error executing order: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "success"}), 200


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "time": int(time.time())})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
