
strategy("MACD Trend Following Strategy v2", overlay=true, initial_capital=2000, default_qty_value=100, default_qty_type=strategy.cash) 

// --- Input Parameters ---
emaLength = input.int(2000, "EMA Trend Filter Length", minval=1) 
fastLength = input.int(12, "MACD Fast Length", minval=1) 
slowLength = input.int(26, "MACD Slow Length", minval=1) 
signalLength = input.int(9, "MACD Signal Length", minval=1) // Standard MACD signal length 
macdThreshold = input.float(-140, "MACD Buy Entry Threshold", step=1) // Threshold for entry in buy direction 
macdThresholdSell = input.float(140, "MACD Sell Entry Threshold", step=1) // Threshold for entry in sell direction 

// TP/SL Thresholds for MACD
macdTpHighThreshold = input.float(140, "MACD TP High Threshold (for Long)", step=1) // New: TP for Long when MACD peaks above this 
macdTpLowThreshold = input.float(-140, "MACD TP Low Threshold (for Short)", step=1) // New: TP for Short when MACD troughs below this 

slPoints = input.float(10000, "Stop Loss Points from Entry", minval=1, step=1) // กำหนด SL เป็นจำนวนจุด 
macdThresholdLookback = input.int(5, "MACD Entry Threshold Lookback Bars", minval=1, maxval=50) 

// --- Strategy Settings ---
tradeSizeUSD = 100 // Ordersize in USD 
leverage = 125 // Leverage 

// --- Trend Filter (EMA 2000) ---
ema2000 = ta.ema(close, emaLength) 
plot(ema2000, "EMA 2000", color=color.rgb(128, 0, 128), linewidth=2) // เพิ่มการ plot EMA2000 

// --- MACD Calculations ---
[macdLine, signalLine, hist] = ta.macd(close, fastLength, slowLength, signalLength) 

// Function to check if MACD Histogram is "red" (negative for buy) or "green" (positive for sell)
isMacdHistRed() => hist < 0 
isMacdHistGreen() => hist > 0 

// --- Entry Conditions ---
buyEntryThresholdReachedRecently = ta.barssince(macdLine <= macdThreshold) <= (macdThresholdLookback - 1) 
buyCondition = close > ema2000 and ta.crossover(macdLine, signalLine) and buyEntryThresholdReachedRecently 

sellEntryThresholdReachedRecently = ta.barssince(macdLine >= macdThresholdSell) <= (macdThresholdLookback - 1) 
sellCondition = close < ema2000 and ta.crossunder(macdLine, signalLine) and sellEntryThresholdReachedRecently 

// --- Entry Logic ---
var label entryLabel = na 
var label slLabel = na 

var float storedEntryPrice = na 
var int storedEntryBarIndex = na 
var string storedTradeDirection = "" 
var float storedSLPrice = na 

var bool macdPeakForLongTP = false 
var bool macdTroughForShortTP = false 

// --- Alert Message (JSON Payload) ---
// This JSON format provides all necessary data for the webhook listener
// Using consistent naming with main.py: SignalType, OrderSizeUSD, SLPrice, CalculatedQty, PositionSize, PositionDirection
// Note: position_size is positive for long, negative for short. We send absolute value and direction.

// Calculate quantity here for alert, similar to how it would be done for entry
float usdtPerContract = close
float calculatedQty = (tradeSizeUSD * leverage) / usdtPerContract

buyAlertMessage = "{ \"Type\": \"Signal\", \"SignalType\": \"buy\", \"Symbol\": \"{{ticker}}\", \"Price\": \"{{close}}\", \"OrderSizeUSD\": \"" + str.tostring(tradeSizeUSD) + "\", \"Leverage\": \"" + str.tostring(leverage) + "\", \"CalculatedQty\": \"" + str.tostring(calculatedQty) + "\", \"SLPrice\": \"" + str.tostring(storedSLPrice) + "\" }"
sellAlertMessage = "{ \"Type\": \"Signal\", \"SignalType\": \"sell\", \"Symbol\": \"{{ticker}}\", \"Price\": \"{{close}}\", \"OrderSizeUSD\": \"" + str.tostring(tradeSizeUSD) + "\", \"Leverage\": \"" + str.tostring(leverage) + "\", \"CalculatedQty\": \"" + str.tostring(calculatedQty) + "\", \"SLPrice\": \"" + str.tostring(storedSLPrice) + "\" }"

// For close signals, we need to know the position direction and size being closed.
# We'll pass `strategy.position_size` and `strategy.position_avg_price` as references.
closeAlertMessageLong = "{ \"Type\": \"Signal\", \"SignalType\": \"close\", \"Symbol\": \"{{ticker}}\", \"Price\": \"{{close}}\", \"PositionSize\": \"" + str.tostring(math.abs(strategy.position_size)) + "\", \"PositionDirection\": \"Long\", \"SLPrice\": \"" + str.tostring(storedSLPrice) + "\" }"
closeAlertMessageShort = "{ \"Type\": \"Signal\", \"SignalType\": \"close\", \"Symbol\": \"{{ticker}}\", \"Price\": \"{{close}}\", \"PositionSize\": \"" + str.tostring(math.abs(strategy.position_size)) + "\", \"PositionDirection\": \"Short\", \"SLPrice\": \"" + str.tostring(storedSLPrice) + "\" }"


if (buyCondition)
    if strategy.position_size == 0 // Only enter if no open position
        // float usdtPerContract = close // Already defined above for alert message
        // float calculatedQty = (tradeSizeUSD * leverage) / usdtPerContract // Already defined above for alert message
        
        strategy.entry("Buy", strategy.long, qty=calculatedQty, comment="Buy Entry", alert_message=buyAlertMessage) 
        
        storedEntryPrice := close 
        storedEntryBarIndex := bar_index 
        storedTradeDirection := "Long" 
        storedSLPrice := close - slPoints // SL for Long 
        macdPeakForLongTP := false // Reset for new trade 
        macdPeakForLongTP := macdLine >= macdTpHighThreshold // Check if already above threshold at entry 
        macdTroughForShortTP := false // Not relevant for Long, but reset 

if (sellCondition)
    if strategy.position_size == 0 // Only enter if no open position
        // float usdtPerContract = close // Already defined above for alert message
        // float calculatedQty = (tradeSizeUSD * leverage) / usdtPerContract // Already defined above for alert message

        strategy.entry("Sell", strategy.short, qty=calculatedQty, comment="Sell Entry", alert_message=sellAlertMessage) 
        
        storedEntryPrice := close 
        storedEntryBarIndex := bar_index 
        storedTradeDirection := "Short" 
        storedSLPrice := close + slPoints // SL for Short 
        macdPeakForLongTP := false // Not relevant for Short, but reset 
        macdTroughForShortTP := false // Reset for new trade 
        macdTroughForShortTP := macdLine <= macdTpLowThreshold // Check if already below threshold at entry 

// --- Update MACD Peak/Trough tracking while position is open ---
if strategy.position_size > 0 // In a long position 
    if not macdPeakForLongTP and macdLine >= macdTpHighThreshold 
        macdPeakForLongTP := true // Set to true once MACD fast line reaches the high threshold 
else if strategy.position_size < 0 // In a short position 
    if not macdTroughForShortTP and macdLine <= macdTpLowThreshold 
        macdTroughForShortTP := true // Set to true once MACD fast line reaches the low threshold 


// --- Stop Loss and Take Profit Logic ---

// For Long Position
if strategy.position_size > 0 // If in a long position 
    slTriggeredByPrice = close <= storedSLPrice 
    
    tpConditionLong = macdPeakForLongTP and ta.crossunder(macdLine, 0) and isMacdHistRed() 

    // NEW EXIT CONDITION: Close Long if price closes below EMA2000 
    emaExitLongCondition = close < ema2000 // Define the EMA exit condition for Long 

    if slTriggeredByPrice 
        strategy.close("Buy", comment="SL - Price Hit", alert_message=closeAlertMessageLong) 
        // Reset stored variables upon closing a trade 
        storedEntryPrice := na 
        storedEntryBarIndex := na 
        storedTradeDirection := "" 
        storedSLPrice := na 
        macdPeakForLongTP := false 
        macdTroughForShortTP := false // Ensure all flags are reset 
    else if tpConditionLong
        strategy.close("Buy", comment="TP - MACD Peak & Cross 0, Hist Red", alert_message=closeAlertMessageLong) 
        // Reset stored variables upon closing a trade 
        storedEntryPrice := na 
        storedEntryBarIndex := na 
        storedTradeDirection := "" 
        storedSLPrice := na 
        macdPeakForLongTP := false 
        macdTroughForShortTP := false // Ensure all flags are reset 
    else if emaExitLongCondition // <--- NEW EXIT CONDITION FOR LONG 
        strategy.close("Buy", comment="Exit Long on EMA2000 Cross Down", alert_message=closeAlertMessageLong) 
        // Reset stored variables upon closing a trade 
        storedEntryPrice := na 
        storedEntryBarIndex := na 
        storedTradeDirection := "" 
        storedSLPrice := na 
        macdPeakForLongTP := false 
        macdTroughForShortTP := false 


// For Short Position
if strategy.position_size < 0 // If in a short position 
    slTriggeredByPrice = close >= storedSLPrice 

    tpConditionShort = macdTroughForShortTP and ta.crossover(macdLine, 0) and isMacdHistGreen() 

    // NEW EXIT CONDITION: Close Short if price closes above EMA2000 
    emaExitShortCondition = close > ema2000 // Define the EMA exit condition for Short 

    if slTriggeredByPrice 
        strategy.close("Sell", comment="SL - Price Hit", alert_message=closeAlertMessageShort) 
        // Reset stored variables upon closing a trade 
        storedEntryPrice := na 
        storedEntryBarIndex := na 
        storedTradeDirection := "" 
        storedSLPrice := na 
        macdPeakForLongTP := false // Ensure all flags are reset 
        macdTroughForShortTP := false 
    else if tpConditionShort
        strategy.close("Sell", comment="TP - MACD Trough & Cross 0, Hist Green", alert_message=closeAlertMessageShort) 
        // Reset stored variables upon closing a trade 
        storedEntryPrice := na 
        storedEntryBarIndex := na 
        storedTradeDirection := "" 
        storedSLPrice := na 
        macdPeakForLongTP := false 
        macdTroughForShortTP := false 
    else if emaExitShortCondition // <--- NEW EXIT CONDITION FOR SHORT 
        strategy.close("Sell", comment="Exit Short on EMA2000 Cross Up", alert_message=closeAlertMessageShort) 
        // Reset stored variables upon closing a trade 
        storedEntryPrice := na 
        storedEntryBarIndex := na 
        storedTradeDirection := "" 
        storedSLPrice := na 
        macdPeakForLongTP := false 
        macdTroughForShortTP := false 


// --- Plotting and Labels ---
plot(macdLine, "MACD Line", color=color.blue) 
plot(signalLine, "Signal Line", color=color.orange) 
plot(hist, "MACD Histogram", color=hist >= 0 ? color.new(color.green, 20) : color.new(color.red, 20), style=plot.style_columns) 
hline(0, "MACD Zero Line", color=color.gray, linestyle=hline.style_dotted) 
hline(macdThreshold, "MACD Buy Entry Thresh", color=color.purple, linestyle=hline.style_dotted) 
hline(macdThresholdSell, "MACD Sell Entry Thresh", color=color.purple, linestyle=hline.style_dotted) 

// New hline for TP thresholds
hline(macdTpHighThreshold, "MACD TP High Thresh", color=color.blue, linestyle=hline.style_dotted, linewidth=1) 
hline(macdTpLowThreshold, "MACD TP Low Thresh", color=color.red, linestyle=hline.style_dotted, linewidth=1) 


// --- Labels for Entry, SL ---

// Clear labels on strategy exit (for a cleaner backtest view)
if strategy.position_size[1] != 0 and strategy.position_size == 0 // If position was closed on this bar 
    if na(entryLabel) == false // Check if label was actually created 
        label.delete(entryLabel) 
    if na(slLabel) == false 
        label.delete(slLabel) 
    storedEntryPrice := na 
    storedEntryBarIndex := na 
    storedTradeDirection := "" 
    storedSLPrice := na 
    macdPeakForLongTP := false 
    macdTroughForShortTP := false 

// Logic for plotting entry, SL
bool newTradeOpened = (strategy.position_size != 0) and (nz(strategy.position_size[1], 0) == 0) 

# If there's an open position (or a new trade just opened) 
if strategy.position_size != 0 or newTradeOpened 
    if newTradeOpened 
        entryLabel := label.new(x=bar_index, y=storedEntryPrice, 
                             text="Entry: " + str.tostring(storedEntryPrice, "#.##") + "\n" + 
                                   "Date: " + str.format_time(time[0], "dd MMM yy HH:mm") + "\n" + 
                                   "Dir: " + storedTradeDirection, 
                             xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_down, 
                             color=storedTradeDirection == "Long" ? color.new(color.green, 20) : color.new(color.red, 20), 
                             textcolor=color.white, size=size.small) 
        
        slLabel := label.new(x=bar_index, y=storedSLPrice, 
                             text="SL: " + str.tostring(storedSLPrice, "#.##"), 
                             xloc=xloc.bar_index, yloc=yloc.price, style=label.style_label_left, 
                             color=color.new(color.gray, 50), textcolor=color.white, size=size.small) 
    
    if na(entryLabel) == false and strategy.position_size != 0 
        label.set_x(entryLabel, bar_index) 
        label.set_y(entryLabel, strategy.position_avg_price) 

        label.set_x(slLabel, bar_index) 
        label.set_y(slLabel, storedSLPrice) 

        float currentPositionEntryPrice = strategy.position_avg_price 
        float currentPrice = close 
        float pnl = (currentPrice - currentPositionEntryPrice) * strategy.position_size 
        string pnlText = "PnL: " + str.tostring(pnl, "#.##") + " USD" 

        label.set_text(entryLabel, 
             "Entry: " + str.tostring(currentPositionEntryPrice, "#.##") + "\n" + 
             "Date: " + str.format_time(time[0], "dd MMM yy HH:mm") + "\n" + 
             "Dir: " + storedTradeDirection + "\n" + 
             pnlText) 
        label.set_color(entryLabel, pnl >= 0 ? color.new(color.green, 20) : color.new(color.red, 20)) 

var float equitySeries = 0.0 
var float netProfitSeries = 0.0 

equitySeries := strategy.equity 
netProfitSeries := strategy.netprofit