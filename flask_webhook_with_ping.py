# flask_webhook_with_ping.py
import os
import time
import json
import logging
from threading import Lock
from flask import Flask, request, jsonify
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinaceRequestException

# --- Configuration ---
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
BINANCE_TESTNET = os.environ.get("BINANCE_TESTNET", "false").lower() in ("1", "true", "yes")

# Rate limiting / controls (adjust as needed)
GLOBAL_MIN_SECONDS_BETWEEN_ORDERS = float(os.environ.get("MIN_SECONDS_BETWEEN_ORDERS", 1.0))
PER_SYMBOL_COOLDOWN = float(os.environ.get("PER_SYMBOL_COOLDOWN", 5.0))  # seconds
TOKEN_BUCKET_RATE = float(os.environ.get("TOKEN_BUCKET_RATE", 5.0))  # tokens per second
TOKEN_BUCKET_CAPACITY = float(os.environ.get("TOKEN_BUCKET_CAPACITY", 10.0))

# Retry settings
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", 3))
BASE_BACKOFF = float(os.environ.get("BASE_BACKOFF", 0.5))  # seconds

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("webhook")

app = Flask(__name__)

# Binance client (testnet if desired)
def create_client():
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    if BINANCE_TESTNET:
        client.API_URL = 'https://testnet.binance.vision/api'
        logger.info("Using Binance TESTNET endpoint")
    return client

client = create_client()

# In-memory rate limit bookkeeping
last_order_time = 0.0
last_order_lock = Lock()
per_symbol_last = {}
token_bucket = {"tokens": TOKEN_BUCKET_CAPACITY, "last_refill": time.time()}
token_lock = Lock()

def refill_tokens():
    with token_lock:
        now = time.time()
        elapsed = now - token_bucket["last_refill"]
        if elapsed <= 0:
            return
        add = elapsed * TOKEN_BUCKET_RATE
        token_bucket["tokens"] = min(TOKEN_BUCKET_CAPACITY, token_bucket["tokens"] + add)
        token_bucket["last_refill"] = now

def consume_token(cost=1.0):
    refill_tokens()
    with token_lock:
        if token_bucket["tokens"] >= cost:
            token_bucket["tokens"] -= cost
            return True
    return False

def allowed_to_send(symbol):
    global last_order_time
    now = time.time()
    with last_order_lock:
        if now - last_order_time < GLOBAL_MIN_SECONDS_BETWEEN_ORDERS:
            logger.info("Global rate limit: too soon since last order")
            return False
        last_for_sym = per_symbol_last.get(symbol, 0)
        if now - last_for_sym < PER_SYMBOL_COOLDOWN:
            logger.info("Per-symbol cooldown: %s (%.2fs left)", symbol, PER_SYMBOL_COOLDOWN - (now - last_for_sym))
            return False
        # check token bucket
        if not consume_token():
            logger.info("Token bucket empty — deferring order to avoid rate limit")
            return False
        # mark times
        last_order_time = now
        per_symbol_last[symbol] = now
        return True

# Helper to safely parse JSON even if Content-Type missing
def parse_json_request(req):
    # If Content-Type is JSON, use get_json
    try:
        if req.is_json:
            return req.get_json(force=True)
    except Exception:
        pass
    # fallback: try to decode raw data
    try:
        raw = req.get_data(as_text=True)
        if not raw:
            return {}
        return json.loads(raw)
    except Exception as e:
        logger.warning("Failed to parse request body as JSON: %s", e)
        # last attempt: if body looks like "key=val" form, try simple parse
        try:
            raw = req.get_data(as_text=True)
            d = {}
            for part in raw.split("&"):
                if "=" in part:
                    k, v = part.split("=",1)
                    d[k] = v
            return d
        except Exception:
            return {}

# Compose order size logic: user sends 'amount' (dollar) and 'leverage' OR sends 'qty' directly
def compute_order_quantity(symbol, data):
    """
    Expected payload options:
    - {'amount': 150, 'leverage': 125}  -> usd amount (approx) -> compute qty using symbol price
    - {'qty': 0.001} -> direct qty
    - {'size': '0.001'} etc.
    """
    # direct qty fields
    for k in ("qty", "quantity", "size"):
        if k in data:
            try:
                return float(data[k])
            except:
                pass

    # amount + leverage: estimate using current price
    amount = data.get("amount")
    leverage = data.get("leverage") or data.get("leverage_val")
    if amount is not None and leverage is not None:
        try:
            amount = float(amount)
            leverage = float(leverage)
            # get price
            ticker = client.get_symbol_ticker(symbol=symbol)
            price = float(ticker["price"])
            # position notional = amount * leverage, qty = notional / price
            notional = amount * leverage
            qty = notional / price
            # For BTCUSDT approximate precision to 6 decimals (adjust per symbol rules in production)
            return round(qty, 6)
        except Exception as e:
            logger.warning("Failed to compute qty from amount/leverage: %s", e)

    # fallback: try 'amount' interpreted as quote currency -> qty = amount/price
    if amount is not None:
        try:
            amount = float(amount)
            ticker = client.get_symbol_ticker(symbol=symbol)
            price = float(ticker["price"])
            qty = amount / price
            return round(qty, 6)
        except Exception:
            pass
    return None

def send_binance_order(symbol, side, qty, is_test=False):
    """
    side: 'BUY' or 'SELL'
    qty: float quantity
    is_test: if True, use create_test_order (testnet)
    """
    if qty is None or qty <= 0:
        raise ValueError("Invalid qty")

    side = side.upper()
    order_type = "MARKET"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info("Sending order attempt %d: %s %s %s (test=%s)", attempt, side, qty, symbol, is_test)
            if is_test or BINANCE_TESTNET:
                # test order (won't create a real order)
                resp = client.create_test_order(symbol=symbol, side=side, type=order_type, quantity=qty)
                logger.info("Test order created (no fill) - response: %s", resp)
                return {"test": True}
            else:
                resp = client.create_order(symbol=symbol, side=side, type=order_type, quantity=qty)
                logger.info("Binance order executed: %s", resp.get("orderId"))
                return resp
        except BinanceAPIException as e:
            # If rate-limit error (-1003) detected, log and backoff
            msg = str(e)
            logger.error("BinanceAPIException: %s", msg)
            if "-1003" in msg or "Way too much request weight" in msg:
                # try to extract banned-until timestamp if present
                try:
                    # sometimes message contains "IP banned until <timestamp>"
                    if "until" in msg:
                        parts = msg.split("until")
                        banned_ts = parts[-1].strip().strip(".")
                        # if numeric milliseconds, compute wait
                        if banned_ts.isdigit():
                            wait_seconds = int(banned_ts) / 1000.0 - time.time()
                            if wait_seconds > 0:
                                logger.error("IP banned until %s (waiting %.1fs)", banned_ts, wait_seconds)
                                time.sleep(min(wait_seconds, 10))  # wait up to 10s then retry (don't block forever)
                except Exception:
                    pass
                # exponential backoff and continue tries
                backoff = BASE_BACKOFF * (2 ** (attempt - 1))
                time.sleep(backoff)
                continue
            # non-rate-limit Binance error => rethrow
            raise
        except Exception as e:
            logger.exception("Unexpected exception when sending order: %s", e)
            backoff = BASE_BACKOFF * (2 ** (attempt - 1))
            time.sleep(backoff)
            continue
    raise RuntimeError("Failed to place order after retries")

def handle_trade_signal(data):
    """
    Expected data example:
    {'signal':'buy', 'symbol':'BTCUSDT', 'amount':150, 'leverage':125}
    or {'signal':'sell', 'symbol':'BTCUSDT', 'qty':0.001}
    """
    symbol = data.get("symbol") or data.get("ticker") or "BTCUSDT"
    raw_signal = data.get("signal") or data.get("side") or ""
    signal = raw_signal.lower()
    if signal not in ("buy", "sell", "long", "short"):
        logger.warning("Unknown signal: %s", raw_signal)
        return {"status": "ignored", "reason": "unknown signal"}

    side = "BUY" if signal in ("buy", "long") else "SELL"

    # check allowed by rate-limit/token-bucket
    if not allowed_to_send(symbol):
        return {"status": "deferred", "reason": "rate_limited"}

    qty = compute_order_quantity(symbol, data)
    if qty is None:
        logger.error("Cannot determine quantity from payload: %s", data)
        return {"status": "error", "reason": "cannot determine qty"}

    # send order (use test endpoint if configured)
    try:
        resp = send_binance_order(symbol=symbol, side=side, qty=qty, is_test=False)
        return {"status": "ok", "resp": resp}
    except Exception as e:
        logger.exception("Order failed: %s", e)
        return {"status": "error", "error": str(e)}


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = parse_json_request(request)
        logger.info("Received Webhook: %s", data)

        # ping handling (skip any Binance calls)
        if data.get("type") == "ping" or data.get("ping") or data.get("action") == "ping":
            logger.info("🟢 Received ping alert → skip Binance API.")
            return jsonify({"status": "ping ok"}), 200

        # if Content-Type missing, warn but continue
        if not request.is_json:
            logger.debug("Request had no JSON Content-Type header; attempted fallback parse.")

        # main signal handling
        if data.get("signal") or data.get("side") or data.get("action"):
            result = handle_trade_signal(data)
            if result.get("status") == "ok":
                return jsonify(result), 200
            elif result.get("status") == "deferred":
                return jsonify(result), 429
            else:
                return jsonify(result), 500

        # no recognizable payload
        logger.warning("No actionable field in webhook payload")
        return jsonify({"status": "ignored", "reason": "no action"}), 400

    except Exception as e:
        logger.exception("❌ Webhook Error: %s", e)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # For debug only; in production use gunicorn
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False)
