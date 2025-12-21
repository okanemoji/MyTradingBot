# app.py
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os, requests, logging
from math import floor

# -------------------- CONFIG --------------------
API_KEY    = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

PROXY_URL = os.getenv("PROXY_URL")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

DEFAULT_AMOUNT_USD = float(os.getenv("DEFAULT_AMOUNT_USD", "10"))
DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "125"))

# -------------------- LOGGING --------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("bot")

# -------------------- APP & CLIENT --------------------
app = Flask(__name__)

def make_binance_client():
    if not API_KEY or not API_SECRET:
        logger.warning("BINANCE_API_KEY or BINANCE_API_SECRET not set")
    client = Client(API_KEY, API_SECRET)
    if PROXY_URL:
        try:
            client.session.proxies.update({"http": PROXY_URL, "https": PROXY_URL})
            logger.info("Applied proxy to binance client")
        except Exception as e:
            logger.warning(f"Cannot apply proxy to binance client: {e}")
    return client

client = make_binance_client()

requests_sess = requests.Session()
if PROXY_URL:
    requests_sess.proxies.update({"http": PROXY_URL, "https": PROXY_URL})

# -------------------- TELEGRAM --------------------
def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        requests_sess.post(url, json=payload, timeout=6)
    except Exception as e:
        logger.warning(f"Telegram send error: {e}")

# -------------------- HELPERS --------------------
def get_position_mode_is_hedge() -> bool:
    try:
        res = client.futures_get_position_mode()
        raw = res.get("dualSidePosition")
        return raw if isinstance(raw, bool) else str(raw).lower() == "true"
    except Exception as e:
        logger.warning(f"Cannot read position mode: {e}")
        return False

def get_symbol_step_size(symbol: str) -> float:
    try:
        info = client.futures_exchange_info()
        sym = next(s for s in info["symbols"] if s["symbol"] == symbol)
        for f in sym["filters"]:
            if f["filterType"] == "LOT_SIZE":
                return float(f["stepSize"])
    except Exception as e:
        logger.warning(f"get_symbol_step_size error: {e}")
    return 0.001

def floor_to_step(qty: float, step: float) -> float:
    return float(f"{floor(qty / step) * step:.10f}")

def usdt_to_contracts(symbol, amount_usd, leverage, price, step):
    raw = (float(amount_usd) * int(leverage)) / float(price)
    return floor_to_step(raw, step)

# -------------------- ROUTES --------------------
@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "env": {"proxy": bool(PROXY_URL)}})

@app.route("/check_ip", methods=["GET"])
def check_ip():
    try:
        ip = requests_sess.get("https://ifconfig.me", timeout=5).text
    except Exception as e:
        logger.warning(f"check_ip error: {e}")
        ip = "unavailable"
    return f"Server IP: {ip}"

@app.route("/health", methods=["GET"])
def health():
    try:
        c = make_binance_client()
        t = c.get_server_time()
        return jsonify({"status": "ok", "serverTime": t})
    except Exception as e:
        logger.error(f"health error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/test_order", methods=["POST"])
def test_order():
    data = request.get_json(force=True, silent=True) or {}
    side = data.get("side", "BUY").upper()
    symbol = data.get("symbol", "BTCUSDT")
    amount = float(data.get("amount", DEFAULT_AMOUNT_USD))
    leverage = int(data.get("leverage", DEFAULT_LEVERAGE))
    try:
        price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        step = get_symbol_step_size(symbol)
        qty = usdt_to_contracts(symbol, amount, leverage, price, step)
        if qty <= 0:
            return jsonify({"status": "error", "error": "qty <= 0"}), 400
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY if side == "BUY" else SIDE_SELL,
            type="MARKET",
            quantity=qty
        )
        logger.info(f"Test order created: {order}")
        send_telegram_message(f"Test order: {order.get('orderId')}")
        return jsonify({"status": "ok", "orderId": order.get("orderId"), "qty": qty})
    except Exception as e:
        logger.error(f"test_order error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    logger.info(f"Received Webhook: {data}")

    if not data:
        return jsonify({"error": "No data"}), 400

    signal = str(data.get("signal", "")).lower()
    symbol = str(data.get("symbol", "BTCUSDT")).replace("BINANCE:", "")
    amount = float(data.get("amount", DEFAULT_AMOUNT_USD))
    leverage = int(data.get("leverage", DEFAULT_LEVERAGE))
    side = str(data.get("side", "")).upper()

    is_hedge = get_position_mode_is_hedge()
    logger.info(f"HedgeMode={is_hedge} | symbol={symbol}")

    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        logger.warning(f"Leverage error: {e}")

    try:
        price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        step = get_symbol_step_size(symbol)
    except Exception as e:
        logger.error(f"Market info error: {e}")
        send_telegram_message(f"❌ Market info error: {e}")
        return jsonify({"error": str(e)}), 500

    try:
        # OPEN POSITION
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
            logger.info(msg)
            send_telegram_message(msg)
            return jsonify({"status": "success", "orderId": order.get("orderId"), "qty": qty}), 200

        # CLOSE POSITION (PARTIAL CLOSE ENABLED)
        elif signal == "close":
            logger.info(f"Close signal received | side={side}")

            if side == "BUY":
                position_side = "LONG"
                close_side = SIDE_SELL
            elif side == "SELL":
                position_side = "SHORT"
                close_side = SIDE_BUY
            else:
                return jsonify({"error": "Invalid side for close"}), 400

            positions = client.futures_position_information(symbol=symbol)

            position_qty = 0.0
            for p in positions:
                if p.get("positionSide", "").upper() == position_side:
                    position_qty = abs(float(p.get("positionAmt", 0)))
                    break

            if position_qty <= 0:
                msg = f"⚠️ No {position_side} position to close for {symbol}"
                logger.info(msg)
                send_telegram_message(msg)
                return jsonify({"status": "noop"}), 200

            # <<< MODIFIED FOR PARTIAL CLOSE >>>
            desired_qty = usdt_to_contracts(symbol, amount, leverage, price, step)
            qty_to_close = min(position_qty, desired_qty)

            if qty_to_close <= 0:
                return jsonify({"error": "qty_to_close <= 0"}), 400

            if is_hedge:
                order = client.futures_create_order(
                    symbol=symbol,
                    side=close_side,
                    type="MARKET",
                    quantity=qty_to_close,
                    positionSide=position_side
                )
            else:
                order = client.futures_create_order(
                    symbol=symbol,
                    side=close_side,
                    type="MARKET",
                    quantity=qty_to_close,
                    reduceOnly=True
                )

            msg = f"✅ Partial Close {position_side} {symbol} qty={qty_to_close}"
            logger.info(msg)
            send_telegram_message(msg)
            return jsonify({"status": "closed", "qty": qty_to_close}), 200

        else:
            return jsonify({"error": "Unknown signal"}), 400

    except Exception as e:
        logger.error(f"Webhook Error: {e}")
        send_telegram_message(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500

# -------------------- MAIN --------------------
if __name__ == "__main__":
    logger.info("Starting Flask app")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
