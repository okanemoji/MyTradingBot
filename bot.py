from binance.client import Client
from binance.enums import *

API_KEY = "YOUR_KEY"
API_SECRET = "YOUR_SECRET"
client = Client(API_KEY, API_SECRET)

# ตัวอย่าง Hedge Mode
symbol = "XAUUSDT"
qty = 0.01  # ต้องตรง stepSize
leverage = 5

client.futures_change_leverage(symbol=symbol, leverage=leverage)

resp = client.futures_create_order(
    symbol=symbol,
    side=SIDE_BUY,
    type=FUTURE_ORDER_TYPE_MARKET,
    quantity=qty,
    positionSide="LONG"
)

print(resp)
