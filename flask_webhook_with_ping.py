import os
import json
import math
import time
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import requests

# ======= CONFIG =======
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TESTNET = False  # เปลี่ยนเป็น True ถ้าใช้ testnet
SYMBOL = "BTCUSDT"

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Order setting
LEVERAGE = 125
ORDER_SIZE_USD = 100

# ======= INIT =======
app = Flask(__name__)
client = Client(API_KEY, API_SECRET, testnet=TESTNET)

# ======= FUNC =======
def send_telegram_message(msg: str):
    """ส่งข้อความเข้า Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram not configured")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        print(f"⚠️ Telegram send error: {e}")

def set_leverage(symbol, leverage):
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        print(f"⚠️ Leverage error: {e}")

def get_quantity(symbol, amount_usd):
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    qty = round(amount_usd / price, 3)
    return qty

# ======= ROUTE =======
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    print(f"📩 Received Webhook: {data}")

    try:
        signal = data.get('signal')
        symbol = data.get('symbol', SYMBOL)
        amount = data.get('amount', ORDER_SIZE_USD)
        leverage = data.get('leverage', LEVERAGE)

        if signal not in ['buy', 'sell', 'close']:
            return jsonify({"status": "ignored"}), 200

        set_leverage(symbol, leverage)
        qty = get_quantity(symbol, amount)

        if signal == 'buy':
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            send_telegram_message(f"✅ Open BUY {symbol} qty={qty}")

        elif signal == 'sell':
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            send_telegram_message(f"✅ Open SELL {symbol} qty={qty}")

        elif signal == 'close':
            positions = client.futures_position_information(symbol=symbol)
            for pos in positions:
                qty_to_close = float(pos['positionAmt'])
                if qty_to_close != 0:
                    side = SIDE_SELL if qty_to_close > 0 else SIDE_BUY
                    client.futures_create_order(
                        symbol=symbol,
                        side=side,
                        type=ORDER_TYPE_MARKET,
                        quantity=abs(qty_to_close)
                    )
                    send_telegram_message(f"✅ Close {symbol} qty={abs(qty_to_close)}")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"❌ Error: {e}")
        send_telegram_message(f"❌ Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ======= MAIN =======
if __name__ == '__main__':
    print(f"✅ Binance client initialized (Testnet={TESTNET})")
    app.run(host='0.0.0.0', port=5000)
