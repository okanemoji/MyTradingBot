def force_clear_position(symbol):
    """ดึงจำนวนเหรียญจริงมาสั่งปิดให้หมดพอร์ต"""
    try:
        # 1. ดึงข้อมูล Position ทั้งหมด
        positions = client.futures_position_information(symbol=symbol)
        for p in positions:
            amt = float(p['positionAmt'])
            if amt != 0:
                # ถ้ามีของค้าง ไม่ว่าจะ Long (+) หรือ Short (-)
                # ยิง Market Order ฝั่งตรงข้ามตามจำนวนที่มีเป๊ะๆ
                side_to_close = SIDE_SELL if amt > 0 else SIDE_BUY
                client.futures_create_order(
                    symbol=symbol,
                    side=side_to_close,
                    type=ORDER_TYPE_MARKET,
                    quantity=abs(amt),
                    reduceOnly=True
                )
        # 2. ล้างออเดอร์ค้าง (SL/TP)
        client.futures_cancel_all_open_orders(symbol=symbol)
        return True
    except Exception as e:
        print(f"❌ Force Clear Error: {e}")
        return False

@app.route("/webhook", methods=["POST"])
def webhook():
    global last_side
    data = request.json
    action = data.get("action", "").upper()
    symbol = data.get("symbol")
    qty = data.get("amount")
    lev = data.get("leverage")

    try:
        # --- กรณีสั่ง CLOSE ---
        if action == "CLOSE":
            force_clear_position(symbol)
            last_side[symbol] = None
            print(f"🧹 {symbol} Fully Cleared")
            return jsonify({"status": "success"}), 200

        # --- กรณีสั่ง BUY หรือ SELL ---
        elif action in ["BUY", "SELL"]:
            if lev:
                client.futures_change_leverage(symbol=symbol, leverage=int(lev))

            # เช็คว่าถ้า 'สัญญาณใหม่' ไม่ตรงกับ 'ฝั่งที่ถืออยู่' (Reverse)
            # ต้องล้างพอร์ตให้เป็น 0 ก่อนเสมอ เพื่อป้องกันการลด Lot
            if symbol in last_side and last_side[symbol] is not None and last_side[symbol] != action:
                print(f"🔄 Signal Switch: {last_side[symbol]} -> {action}. Clearing first...")
                force_clear_position(symbol)

            # เปิดไม้ (ถ้าฝั่งเดิมจะเป็นการสะสมไม้/Re-entry)
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY if action == "BUY" else SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            last_side[symbol] = action
            return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"❌ Webhook Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400
