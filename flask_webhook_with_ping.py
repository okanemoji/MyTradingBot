from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os, requests
from math import floor

# -------------------- CONFIG --------------------
API_KEY    = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

app = Flask(__name__)
client = Client(API_KEY, API_SECRET)

# -------------------- TELEGRAM --------------------
def send_telegram_message(text):
    """ส่งข้อความไป Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram not configured.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"⚠️ Telegram send error: {e}")

# -------------------- HELPERS --------------------
def get_position_mode_is_hedge() -> bool:
    """ตรวจสอบว่าเป็น Hedge Mode หรือไม่"""
    try:
        res = client.futures_get_position_mode()
        raw = res.get("dualSidePosition")
        return raw if isinstance(raw, bool) else str(raw).lower() == "true"
    except Exception as e:
        print(f"⚠️ Cannot read position mode: {e}")
        return False

def get_symbol_step_size(symbol: str) -> float:
    info = client.futures_exchange_info()
    sym = next(s for s in info["symbols"] if s["symbol"] == symbol)
    for f in sym["filters"]:
        if f["filterType"] == "LOT_SIZE":
            return float(f["stepSize"])
    return 0.001

def floor_to_step(qty: float, step: float) -> float:
    return float(f"{floor(qty / step) * step:.10f}")

def usdt_to_contracts(symbol, amount_usd, leverage, price, step):
    raw = (float(amount_usd) * int(leverage)) / float(price)
    return floor_to_step(raw, step)

# -------------------- ROUTES --------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    print(f"📩 Received Webhook: {data}")

    if not data:
        return jsonify({"error": "No data"}), 400

    signal = str(data.get("signal", "")).lower()
    symbol = str(data.get("symbol", "BTCUSDT")).replace("BINANCE:", "")
    amount = float(data.get("amount", 10))
    leverage = int(data.get("leverage", 125))
    side = str(data.get("side", "")).upper()

    is_hedge = get_position_mode_is_hedge()
    print(f"ℹ️ HedgeMode={is_hedge} | symbol={symbol}")

    # --- Set Leverage (ignore error if already set) ---
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        print(f"⚠️ Leverage error: {e}")

    # --- Fetch price & step size ---
    try:
        price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        step = get_symbol_step_size(symbol)
    except Exception as e:
        print(f"❌ Market info error: {e}")
        send_telegram_message(f"❌ Market info error: {e}")
        return jsonify({"error": str(e)}), 500

    try:
        # -------------------- OPEN POSITION --------------------
        if signal in ["buy", "sell"]:
            qty = usdt_to_contracts(symbol, amount, leverage, price, step)
            if qty <= 0:
                return jsonify({"error": "qty <= 0"}), 400

            if is_hedge:
                pos_side = "LONG" if signal == "buy" else "SHORT"
                order = client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_BUY if signal == "buy" else SIDE_SELL,
                    type="MARKET",
                    quantity=qty,
                    positionSide=pos_side
                )
            else:
                order = client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_BUY if signal == "buy" else SIDE_SELL,
                    type="MARKET",
                    quantity=qty
                )

            msg = f"✅ Open {signal.upper()} {symbol} qty={qty}"
            print(msg)
            send_telegram_message(msg)
            return jsonify({"status": "success", "orderId": order.get("orderId"), "qty": qty}), 200

        # -------------------- CLOSE POSITION --------------------
        elif signal == "close":
            print(f"📩 Close signal received | side={side}")

            # ระบุฝั่งที่จะปิด
            if side == "BUY":
                position_side = "LONG"
                close_side = SIDE_SELL
            elif side == "SELL":
                position_side = "SHORT"
                close_side = SIDE_BUY
            else:
                return jsonify({"error": "Invalid side for close"}), 400

            positions = client.futures_position_information(symbol=symbol)
            qty_to_close = 0.0
            for p in positions:
                if p.get("positionSide", "").upper() == position_side and abs(float(p["positionAmt"])) > 0:
                    qty_to_close = abs(float(p["positionAmt"]))
                    break

            if qty_to_close > 0:
                if is_hedge:
                    # 🟢 Hedge Mode → ใช้ positionSide เท่านั้น
                    order = client.futures_create_order(
                        symbol=symbol,
                        side=close_side,
                        type="MARKET",
                        quantity=qty_to_close,
                        positionSide=position_side
                    )
                else:
                    # 🟠 One-Way Mode → ใช้ reduceOnly
                    order = client.futures_create_order(
                        symbol=symbol,
                        side=close_side,
                        type="MARKET",
                        quantity=qty_to_close,
                        reduceOnly=True
                    )

                msg = f"✅ Close {position_side} {symbol} qty={qty_to_close}"
                print(msg)
                send_telegram_message(msg)
                return jsonify({"status": "closed", "qty": qty_to_close}), 200
            else:
                msg = f"⚠️ No {position_side} position to close for {symbol}"
                print(msg)
                send_telegram_message(msg)
                return jsonify({"status": "noop"}), 200

        else:
            return jsonify({"error": "Unknown signal"}), 400

    except Exception as e:
        print(f"❌ Error: {e}")
        send_telegram_message(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})

# -------------------- MAIN --------------------
if __name__ == "__main__":
    print("✅ Binance client initialized (Testnet=False)")
    app.run(host="0.0.0.0", port=5000)
