@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    action = data.get("action").upper()
    symbol = data.get("symbol")
    qty = data.get("amount")
    lev = data.get("leverage")

    try:
        # กรณีเปิดไม้ BUY / SELL
        if action == "BUY" or action == "SELL":
            client.futures_change_leverage(symbol=symbol, leverage=lev)
            side = SIDE_BUY if action == "BUY" else SIDE_SELL
            client.futures_create_order(
                symbol=symbol, 
                side=side, 
                type=ORDER_TYPE_MARKET, 
                quantity=qty
            )
            print(f"🚀 {action} {symbol} Executed")

        # กรณีสั่ง CLOSE (แก้ไขใหม่ให้ชัวร์ขึ้น)
        elif action == "CLOSE":
            # 1. ยกเลิกคำสั่งค้างทั้งหมดก่อน (SL/TP ที่อาจค้างในระบบ)
            client.futures_cancel_all_open_orders(symbol=symbol)
            
            # 2. เช็ค Position ปัจจุบัน
            pos = client.futures_position_information(symbol=symbol)
            for p in pos:
                amt = float(p['positionAmt'])
                if amt != 0:
                    # ถ้า amt > 0 คือถือ LONG ต้องส่ง SELL ปิด
                    # ถ้า amt < 0 คือถือ SHORT ต้องส่ง BUY ปิด
                    side_to_close = SIDE_SELL if amt > 0 else SIDE_BUY
                    client.futures_create_order(
                        symbol=symbol,
                        side=side_to_close,
                        type=ORDER_TYPE_MARKET,
                        quantity=abs(amt),
                        reduceOnly=True # สำคัญมาก: ป้องกันการเปิดไม้ใหม่ฝั่งตรงข้าม
                    )
                    print(f"✅ Closed {symbol} position: {amt}")
            
        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"❌ Error Detail: {str(e)}")
        # ส่ง Error กลับไปที่ Log เพื่อให้เรารู้ว่า Binance บ่นว่าอะไร
        return jsonify({"status": "error", "message": str(e)}), 400
