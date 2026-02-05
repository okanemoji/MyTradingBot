from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException
import os, requests, logging, time
from math import floor

# ================= CONFIG =================
API_KEY    = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

PROXY_URL = os.getenv("PROXY_URL")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

DEFAULT_AMOUNT_USD = float(os.getenv("DEFAULT_AMOUNT_USD", "10"))   # ไม้ละ 10$
FIXED_LEVERAGE     = int(os.getenv("FIXED_LEVERAGE", "100"))        # leverage จริง
SYMBOL_DEFAULT     = os.getenv("DEFAULT_SYMBOL", "XPTUSDT")

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("bot")

# ================= APP =================
app = Flask(__name__)

# ================= BINANCE CLIENT =================
def make_binance_client():
    client = Client(API_KEY, API_SECRET)
    if PROXY_URL:
        client.session.proxies.update({"http": PROXY_URL, "https": PROXY_URL})
    return client

client = make_binance_client()

# ================= TELEGRAM =================
requests_sess = requests.Session()
if PROXY_URL:
    requests_sess.proxies.update({"http": PROXY_URL, "https": PROXY_URL})

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests_sess.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=5)
    except Exception as e:
        logger.warning(f"Telegram error: {e}")

# ================= GLOBAL STATE =================
STEP_SIZE = {}
POSITION_MODE_HEDGE = False
LEVERAGE_SET = set()

EXCHANGE_READY = False
LAST_PRELOAD_ATTEMPT = 0
PRELOAD_COOLDOWN = 60 * 10  # 10 นาที

LAST_SIGNAL = {}

# ================= HELPERS =================
def floor_to_step(qty: float, step: float) -> float:
    return float(f"{floor(qty / step) * step:.10f}")

def usdt_to_qty(symbol, usd, price):
    step = STEP_SIZE.get(symbol, 0.001)
    raw = (usd * FIXED_LEVERAGE) / price
    return floor_to_step(raw, step)

def debounce(symbol, signal, sec=2):
    key = f"{symbol}:{signal}"
    now = time.time()
    if key in LAST_SIGNAL and now - LAST_SIGNAL[key] < sec:
        return True
    LAST_SIGNAL[key] = now
    return False

# ================= LAZY PRELOAD =================
def try_preload():
    global STEP_SIZE, POSITION_MODE_HEDGE, EXCHANGE_READY, LAST_PRELOAD_ATTEMPT

    if EXCHANGE_READY:
        return True

    now = time.time()
    if now - LAST_PRELOAD_ATTEMPT < PRELOAD_COOLDOWN:
        return False

    LAST_PRELOAD_ATTEMPT = now
    logger.info("Trying preload exchange info...")

    try:
        info = client.futures_exchange_info()
        STEP_SIZE.clear()

        for s in info["symbols"]:
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    STEP_SIZE[s["symbol"]] = float(f["stepSize"])

        res = client.futures_get_position_mode()
        POSITION_MODE_HEDGE = bool(res.get("dualSidePosition", False))

        EXCHANGE_READY = True
        logger.info("✅ Preload success")
        return True

    except Exception as e:
        logger.error(f"❌ Preload failed (will retry later): {e}")
        return False

# ================= ROOT =================
@app.route("/", methods=["GET"])
def root():
    return "OK", 200

# ================= WEBHOOK =================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    logger.info(f"Webhook: {data}")

    if not data:
        return jsonify({"status": "noop"}), 200

    # preload แบบปลอดภัย
    if not try_preload():
        return jsonify({
            "status": "exchange_not_ready",
            "message": "Binance API not ready yet"
        }), 503

    signal = str(data.get("signal", "")).lower()
    symbol = str(data.get("symbol", SYMBOL_DEFAULT)).replace("BINANCE:", "")
    amount = float(data.get("amount", DEFAULT_AMOUNT_USD))

    if debounce(symbol, signal):
        return jsonify({"status": "duplicate"}), 200

    # -------- SET LEVERAGE (ครั้งเดียว) --------
    try:
        if symbol not in LEVERAGE_SET:
            client.futures_change_leverage(symbol=symbol, leverage=FIXED_LEVERAGE)
            LEVERAGE_SET.add(symbol)
    except Exception as e:
        logger.warning(f"Leverage set error: {e}")

    try:
        price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        qty   = usdt_to_qty(symbol, amount, price)

        if qty <= 0:
            return jsonify({"error": "qty <= 0"}), 400

        # -------- OPEN --------
        if signal in ["buy", "sell"]:
            side = SIDE_BUY if signal == "buy" else SIDE_SELL

            params = {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": qty
            }

            if POSITION_MODE_HEDGE:
                params["positionSide"] = "LONG" if signal == "buy" else "SHORT"

            client.futures_create_order(**params)
            send_telegram(f"✅ OPEN {signal.upper()} {symbol} qty={qty}")
            return jsonify({"status": "opened", "qty": qty}), 200

        # -------- CLOSE --------
        if signal == "close":
            positions = client.futures_position_information(symbol=symbol)

            for p in positions:
                amt = float(p["positionAmt"])
                if amt == 0:
                    continue

                close_side = SIDE_SELL if amt > 0 else SIDE_BUY
                params = {
                    "symbol": symbol,
                    "side": close_side,
                    "type": "MARKET",
                    "quantity": abs(amt),
                    "reduceOnly": True
                }

                if POSITION_MODE_HEDGE:
                    params["positionSide"] = p["positionSide"]

                client.futures_create_order(**params)

            send_telegram(f"✅ CLOSE {symbol}")
            return jsonify({"status": "closed"}), 200

        return jsonify({"error": "unknown signal"}), 400

    except BinanceAPIException as e:
        if e.code == -1003:
            logger.critical("RATE LIMIT HIT - STOP")
            return jsonify({"error": "rate limited"}), 429
        raise

    except Exception as e:
        logger.error(f"Error: {e}")
        send_telegram(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500

# ================= MAIN =================
if __name__ == "__main__":
    logger.info("Starting bot")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
