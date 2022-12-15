from asyncio import get_event_loop, gather, sleep
import pandas as pd
import pandas_ta as ta
import time
import mplfinance as mpf 
from LineNotify import LineNotify
import config
import os
import pathlib
import logging
from logging.handlers import RotatingFileHandler
from random import randint

# -----------------------------------------------------------------------------
# API_KEY, API_SECRET, LINE_NOTIFY_TOKEN in config.ini
# -----------------------------------------------------------------------------

import ccxt.async_support as ccxt

# print('CCXT Version:', ccxt.__version__)
# -----------------------------------------------------------------------------

bot_name = 'EMA Futures (Binance) version 1.4.6'

# ansi escape code
CLS_SCREEN = '\033[2J\033[1;1H' # cls + set top left
CLS_LINE = '\033[0J'
SHOW_CURSOR = '\033[?25h'
HIDE_CURSOR = '\033[?25l'
CRED  = '\33[31m'
CGREEN  = '\33[32m'
CYELLOW  = '\33[33m'
CEND = '\033[0m'
CBOLD = '\33[1m'

# กำหนดเวลาที่ต้องการเลื่อนการอ่านข้อมูล เป็นจำนวนวินาที
TIME_SHIFT = config.TIME_SHIFT

TIMEFRAME_SECONDS = {
    '1m': 60,
    '3m': 60*3,
    '5m': 60*5,
    '15m': 60*15,
    '30m': 60*30,
    '1h': 60*60,
    '2h': 60*60*2,
    '4h': 60*60*4,
    '6h': 60*60*6,
    '8h': 60*60*8,
    '12h': 60*60*12,
    '1d': 60*60*24,
}

CANDLE_LIMIT = config.CANDLE_LIMIT
CANDLE_PLOT = config.CANDLE_PLOT

UB_TIMER_SECONDS = [
    TIMEFRAME_SECONDS[config.timeframe],
    15,
    20,
    30,
    60,
    int(TIMEFRAME_SECONDS[config.timeframe]/2)
]

POSITION_COLUMNS = ["symbol", "entryPrice", "unrealizedProfit", "positionAmt", "initialMargin"]
# POSITION_COLUMNS = ["symbol", "entryPrice", "unrealizedProfit", "isolatedWallet", "positionAmt", "positionSide", "initialMargin"]

CSV_COLUMNS = [
        "symbol", "signal_index", "margin_type",
        "trade_mode", "trade_long", "trade_short",
        "leverage", "cost_type", "cost_amount",
        "tpsl_mode",
        "tp_long", "tp_short",
        "tp_close_long", "tp_close_short",
        "sl_long", "sl_short",
        "trailing_stop_mode",
        "callback_long", "callback_short",
        "active_tl_long", "active_tl_short",
        "fast_type",
        "fast_value",
        "mid_type",
        "mid_value",
        "slow_type",
        "slow_value"
        ]

# ----------------------------------------------------------------------------
# global variable
# ----------------------------------------------------------------------------
notify = LineNotify(config.LINE_NOTIFY_TOKEN)

all_positions = pd.DataFrame(columns=POSITION_COLUMNS)
count_trade = 0
count_trade_long = 0
count_trade_short = 0

start_balance_total = 0.0
balance_entry = 0.0
balalce_total = 0.0

watch_list = []
all_symbols = {}
all_leverage = {}
all_candles = {}

orders_history = {}

RSI30 = [30 for i in range(0, CANDLE_PLOT)]
RSI50 = [50 for i in range(0, CANDLE_PLOT)]
RSI70 = [70 for i in range(0, CANDLE_PLOT)]

symbols_setting = pd.DataFrame(columns=CSV_COLUMNS)

def getExchange():
    exchange = ccxt.binance({
        "apiKey": config.API_KEY,
        "secret": config.API_SECRET,
        "options": {"defaultType": "future"},
        "enableRateLimit": True}
    )
    if config.SANDBOX:
        exchange.set_sandbox_mode(True)
    return exchange

async def line_chart(symbol, df, msg, pd=''):
    data = df.tail(CANDLE_PLOT)

    colors = ['green' if value >= 0 else 'red' for value in data['MACD']]
    added_plots = [
        mpf.make_addplot(data['fast'],color='red',width=0.5),
        mpf.make_addplot(data['mid'],color='orange',width=0.5),
        mpf.make_addplot(data['slow'],color='green',width=0.5),
        mpf.make_addplot(data['RSI'],ylim=(10, 90),panel=2,color='blue',width=0.75,ylabel=f"RSI ({config.RSI_PERIOD})"),
        mpf.make_addplot(RSI30,ylim=(10, 90),panel=2,color='red',linestyle='-.',width=0.5),
        mpf.make_addplot(RSI50,ylim=(10, 90),panel=2,color='red',linestyle='-.',width=0.5),
        mpf.make_addplot(RSI70,ylim=(10, 90),panel=2,color='red',linestyle='-.',width=0.5),
        mpf.make_addplot(data['MACD'],type='bar',width=0.7,panel=3,color=colors),
        mpf.make_addplot(data['MACDs'],panel=3,color='blue',width=0.75),
    ]

    filename = f"./plots/order_{symbol}.png"
    mpf.plot(
        data,
        volume=True,
        figratio=(8, 6),
        panel_ratios=(8,2,2,2),
        type="candle",
        title=f'{symbol} {pd} ({config.timeframe} @ {data.index[-1]})',
        addplot=added_plots,
        tight_layout=True,
        style="yahoo",
        savefig=filename,
    )

    notify.Send_Image(msg, image_path=filename)
    # await sleep(2)
    if config.Remove_Plot == 'no':
        os.remove(filename)
    return

def line_notify(message):
    notify.Send_Text(message)
    logger.info(message.replace('\n', ','))

def add_indicator(symbol, bars):
    df = pd.DataFrame(
        bars, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).map(
        lambda x: x.tz_convert("Asia/Bangkok")
    )
    df = df.set_index("timestamp")

    # เอาข้อมูลใหม่ไปต่อท้าย ข้อมูลที่มีอยู่
    if symbol in all_candles.keys() and len(df) < CANDLE_LIMIT:
        df = pd.concat([all_candles[symbol], df], ignore_index=False)

        # เอาแท่งซ้ำออก เหลืออันใหม่สุด
        df = df[~df.index.duplicated(keep='last')].tail(CANDLE_LIMIT)

    df = df.tail(CANDLE_LIMIT)

    # คำนวนค่าต่างๆใหม่
    df['fast'] = 0
    df['mid'] = 0
    df['slow'] = 0
    df['MACD'] = 0
    df['MACDs'] = 0
    df['MACDh'] = 0
    df["RSI"] = 0

    try:
        fastType = config.Fast_Type 
        fastValue = config.Fast_Value
        midType = config.Mid_Type 
        midValue = config.Mid_Value        
        slowType = config.Slow_Type 
        slowValue = config.Slow_Value

        if symbol in symbols_setting.index:
            # print(symbols_setting.loc[symbol])
            fastType = symbols_setting.loc[symbol]['fast_type']
            fastValue = int(symbols_setting.loc[symbol]['fast_value'])
            midType = symbols_setting.loc[symbol]['mid_type']
            midValue = int(symbols_setting.loc[symbol]['mid_value'])
            slowType = symbols_setting.loc[symbol]['slow_type']
            slowValue = int(symbols_setting.loc[symbol]['slow_value'])

        if fastType == 'EMA':
            df['fast'] = ta.ema(df['close'],fastValue)
        elif fastType == 'SMA':
            df['fast'] = ta.sma(df['close'],fastValue)
        elif fastType == 'HMA':
            df['fast'] = ta.hma(df['close'],fastValue)
        elif fastType == 'RMA':
            df['fast'] = ta.rma(df['close'],fastValue)
        elif fastType == 'WMA':
            df['fast'] = ta.wma(df['close'],fastValue)
        elif fastType == 'VWMA':
            df['fast'] = ta.vwma(df['close'],df['volume'],fastValue)

        if midType == 'EMA':
            df['mid'] = ta.ema(df['close'],midValue)
        elif midType == 'SMA':
            df['mid'] = ta.sma(df['close'],midValue)
        elif midType == 'HMA':
            df['mid'] = ta.hma(df['close'],midValue)
        elif midType == 'RMA':
            df['mid'] = ta.rma(df['close'],midValue)
        elif midType == 'WMA':
            df['mid'] = ta.wma(df['close'],midValue)
        elif midType == 'VWMA':
            df['mid'] = ta.vwma(df['close'],df['volume'],midValue)

        if slowType == 'EMA':
            df['slow'] = ta.ema(df['close'],slowValue)
        elif slowType == 'SMA':
            df['slow'] = ta.sma(df['close'],slowValue)
        elif slowType == 'HMA':
            df['slow'] = ta.hma(df['close'],slowValue)
        elif slowType == 'RMA':
            df['slow'] = ta.rma(df['close'],slowValue)
        elif slowType == 'WMA':
            df['slow'] = ta.wma(df['close'],slowValue)
        elif slowType == 'VWMA':
            df['slow'] = ta.vwma(df['close'],df['volume'],slowValue)

        # cal MACD
        ewm_fast     = df['close'].ewm(span=config.MACD_FAST, adjust=False).mean()
        ewm_slow     = df['close'].ewm(span=config.MACD_SLOW, adjust=False).mean()
        df['MACD']   = ewm_fast - ewm_slow
        df['MACDs']  = df['MACD'].ewm(span=config.MACD_SIGNAL).mean()
        df['MACDh']  = df['MACD'] - df['MACDs']

        # cal RSI
        # change = df['close'].diff(1)
        # gain = change.mask(change<0,0)
        # loss = change.mask(change>0,0)
        # avg_gain = gain.ewm(com = config.RSI_PERIOD-1,min_periods=config.RSI_PERIOD).mean()
        # avg_loss = loss.ewm(com = config.RSI_PERIOD-1,min_periods=config.RSI_PERIOD).mean()
        # rs = abs(avg_gain / avg_loss)
        # df["RSI"] = 100 - ( 100 / ( 1 + rs ))
        df["RSI"] = ta.rsi(df['close'],config.RSI_PERIOD)

    except Exception as ex:
        print(type(ex).__name__, str(ex))
        logger.exception('add_indicator')

    return df

"""
fetch_ohlcv - อ่านแท่งเทียน
exchange: binance exchange
symbol: coins symbol
timeframe: candle time frame
limit: จำนวนแท่งที่ต้องการ, ใส่ 0 หากต้องการให้เอาแท่งใหม่ที่ไม่มาครบ
timestamp: ระบุเวลาปัจจุบัน ถ้า limit=0
"""
async def fetch_ohlcv(exchange, symbol, timeframe, limit=1, timestamp=0):
    try:
        # กำหนดการอ่านแท่งเทียนแบบไม่ระบุจำนวน
        if limit == 0 and symbol in all_candles.keys():
            timeframe_secs = TIMEFRAME_SECONDS[timeframe]
            last_candle_time = int(pd.Timestamp(all_candles[symbol].index[-1]).tz_convert('UTC').timestamp())
            # ให้อ่านแท่งสำรองเพิ่มอีก 2 แท่ง
            ohlcv_bars = await exchange.fetch_ohlcv(symbol, timeframe, None, round(1.5+(timestamp-last_candle_time)/timeframe_secs))
        else:
            ohlcv_bars = await exchange.fetch_ohlcv(symbol, timeframe, None, limit)
        if len(ohlcv_bars):
            all_candles[symbol] = add_indicator(symbol, ohlcv_bars)
            # print(symbol)
    except Exception as ex:
        print(type(ex).__name__, str(ex))
        logger.exception('fetch_ohlcv')
        if limit == 0 and symbol in all_candles.keys():
            print('----->', timestamp, last_candle_time, timestamp-last_candle_time, round(2.5+(timestamp-last_candle_time)/timeframe_secs))

async def set_leverage(exchange, symbol, marginType):
    try:
        if config.automaxLeverage == "on":
            symbol_ccxt = all_symbols[symbol]['symbol']
            params  = {"settle": marginType}
            lv_tiers = await exchange.fetchLeverageTiers([symbol], params=params)
            leverage = int(lv_tiers[symbol_ccxt][0]['maxLeverage'])
            # print(symbol, symbol_ccxt, leverage)
            await exchange.set_leverage(leverage, symbol)
        else:
            leverage = config.Leverage
            if symbol in symbols_setting.index:
                leverage = int(symbols_setting.loc[symbol]['leverage'])
            await exchange.set_leverage(leverage, symbol)

        # เก็บค่า leverage ไว้ใน all_leverage เพื่อเอาไปใช้ต่อที่อื่น
        all_leverage[symbol] = leverage
    except Exception as ex:
        logger.debug(f'{symbol} {type(ex).__name__} {str(ex)}')
        leverage = 5
        if type(ex).__name__ == 'ExchangeError' and '-4300' in str(ex):
            leverage = 20
        print(symbol, f'found leverage error, Bot will set leverage = {leverage}')
        logger.info(f'{symbol} found leverage error, Bot will set leverage = {leverage}')

        # เก็บค่า leverage ไว้ใน all_leverage เพื่อเอาไปใช้ต่อที่อื่น
        all_leverage[symbol] = leverage
        try:
            await exchange.set_leverage(leverage, symbol)
        except Exception as ex:
            # print(type(ex).__name__, str(ex))
            print(symbol, f'can not set leverage')
            logger.info(f'{symbol} can not set leverage')

async def fetch_ohlcv_trade(exchange, symbol, timeframe, limit=1, timestamp=0):
    await fetch_ohlcv(exchange, symbol, timeframe, limit, timestamp)
    await gather( go_trade(exchange, symbol) )
# order management zone --------------------------------------------------------
async def set_order_history(positions_list):
    global orders_history
    for symbol in positions_list:
        orders_history[symbol] = {
                'position': 'open', 
                'win': 0, 
                'loss': 0, 
                'trade': 1,
                'last_loss': 0
                }
    logger.debug(orders_history)
async def add_order_history(symbol):
    global orders_history
    if symbol not in orders_history.keys():
        orders_history[symbol] = {
                'position': 'open', 
                'win': 0, 
                'loss': 0, 
                'trade': 1,
                'last_loss': 0
                }
    else:
        orders_history[symbol]['trade'] = orders_history[symbol]['trade'] + 1
async def close_order_history(symbol):
    global orders_history
    if symbol not in orders_history.keys():
        orders_history[symbol] = {
                'position': 'close', 
                'win': 0, 
                'loss': 0, 
                'trade': 1,
                'last_loss': 0
                }
    else:
        orders_history[symbol]['position'] = 'close'
    positionInfo = all_positions.loc[all_positions['symbol']==symbol]
    logger.debug(positionInfo)
    profit = 0
    if not positionInfo.empty and float(positionInfo.iloc[-1]["unrealizedProfit"]) != 0:
        profit = float(positionInfo.iloc[-1]["unrealizedProfit"])
    if profit > 0:
        orders_history[symbol]['win'] = orders_history[symbol]['win'] + 1
        orders_history[symbol]['last_loss'] = 0
    elif profit < 0:
        orders_history[symbol]['loss'] = orders_history[symbol]['loss'] + 1
        orders_history[symbol]['last_loss'] = orders_history[symbol]['last_loss'] + 1
def save_orders_history():
    oh_json = [{
        'symbol':symbol,
        'win':orders_history[symbol]['win'],
        'loss':orders_history[symbol]['loss'],
        'trade':orders_history[symbol]['trade']
    } for symbol in orders_history.keys()]
    oh_df = pd.DataFrame(oh_json)
    oh_df.to_csv('orders_history.csv', index=False)

# trading zone -----------------------------------------------------------------
async def long_enter(exchange, symbol, amount):
    order = await exchange.create_market_buy_order(symbol, amount)
    await add_order_history(symbol)
    # print("Status : LONG ENTERING PROCESSING...")
    logger.debug(order)
    return
#-------------------------------------------------------------------------------
async def long_close(exchange, symbol, positionAmt):
    order = await exchange.create_market_sell_order(symbol, positionAmt, params={"reduceOnly":True})
    await close_order_history(symbol)
    logger.debug(order)
    return
#-------------------------------------------------------------------------------
async def short_enter(exchange, symbol, amount):
    order = await exchange.create_market_sell_order(symbol, amount)
    await add_order_history(symbol)
    # print("Status : SHORT ENTERING PROCESSING...")
    logger.debug(order)
    return
#-------------------------------------------------------------------------------
async def short_close(exchange, symbol, positionAmt):
    order = await exchange.create_market_buy_order(symbol, (positionAmt*-1), params={"reduceOnly":True})
    await close_order_history(symbol)
    logger.debug(order)
    return
#-------------------------------------------------------------------------------
async def cancel_order(exchange, symbol):
    await sleep(1)
    order = await exchange.cancel_all_orders(symbol, params={'conditionalOrdersOnly':False})
    logger.debug(order)
    return
#-------------------------------------------------------------------------------
async def long_TPSL(exchange, symbol, amount, PriceEntry, pricetp, pricesl, closeRate):
    closetp=(closeRate/100.0)
    logger.debug(f'{symbol}: amount:{amount}, PriceEntry:{PriceEntry}, pricetp:{pricetp}, pricesl:{pricesl}, closetp:{closetp}, closeamt:{amount*closetp}')
    params = {
        'reduceOnly': True
    }
    params['stopPrice'] = pricetp
    order = await exchange.create_order(symbol, 'TAKE_PROFIT_MARKET', 'sell', (amount*closetp), PriceEntry, params)
    logger.debug(order)
    await sleep(1)
    params['stopPrice'] = pricesl
    order = await exchange.create_order(symbol, 'STOP_MARKET', 'sell', amount, PriceEntry, params)
    logger.debug(order)
    await sleep(1)
    return
#-------------------------------------------------------------------------------
async def long_TLSTOP(exchange, symbol, amount: float, priceTL: float, callbackRate: float):
    params = {
        # 'quantityIsRequired': False, 
        'activationPrice': priceTL, 
        'callbackRate': callbackRate, 
        'reduceOnly': True
    }
    logger.debug(params)
    order = await exchange.create_order(symbol, 'TRAILING_STOP_MARKET', 'sell', amount, None, params)
    logger.debug(order)
    await sleep(1)
    return
#-------------------------------------------------------------------------------
async def short_TPSL(exchange, symbol, amount, PriceEntry, pricetp, pricesl, closeRate):
    closetp=(closeRate/100.0)
    logger.debug(f'{symbol}: amount:{amount}, PriceEntry:{PriceEntry}, pricetp:{pricetp}, pricesl:{pricesl}, closetp:{closetp}, closeamt:{amount*closetp}')
    params = {
        'quantityIsRequired': False, 
        'reduceOnly': True
    }
    params['stopPrice'] = pricetp
    order = await exchange.create_order(symbol, 'TAKE_PROFIT_MARKET', 'buy', (amount*closetp), PriceEntry, params)
    logger.debug(order)
    await sleep(1)
    params['stopPrice'] = pricesl
    order = await exchange.create_order(symbol, 'STOP_MARKET', 'buy', amount, PriceEntry, params)        
    logger.debug(order)           
    await sleep(1)
    return
#-------------------------------------------------------------------------------
async def short_TLSTOP(exchange, symbol, amount: float, priceTL: float, callbackRate: float):
    params = {
        'quantityIsRequired': False, 
        'activationPrice': priceTL, 
        'callbackRate': callbackRate, 
        'reduceOnly': True
    }
    logger.debug(params)
    order = await exchange.create_order(symbol, 'TRAILING_STOP_MARKET', 'buy', amount, None, params)
    logger.debug(order)
    await sleep(1)
    return
#-------------------------------------------------------------------------------
async def cal_amount(exchange, symbol, leverage, costType, costAmount, closePrice, chkLastPrice):
    # คำนวนจำนวนเหรียญที่ใช้เปิดออเดอร์
    priceEntry = float(closePrice)
    # minAmount = float(all_symbols[symbol]['minAmount'])
    # minCost = float(all_symbols[symbol]['minCost'])
    if chkLastPrice:
        try:
            ticker = await exchange.fetch_ticker(symbol)
            logger.debug(f'{symbol}:ticker\n{ticker}')
            priceEntry = float(ticker['last'])
        except Exception as ex:
            print(type(ex).__name__, str(ex))
    if costType=='#':
        amount = costAmount / priceEntry
    elif costType=='$':
        amount = costAmount * float(leverage) / priceEntry
    # elif costType=='M':
    #     # amount = priceEntry * minAmount / float(leverage) * 1.1
    #     amount =  minCost / float(leverage) / priceEntry * 1.1 
    else:
        amount = (float(balance_entry)/100) * costAmount * float(leverage) / priceEntry

    logger.info(f'{symbol} lev:{leverage} close:{closePrice} last:{priceEntry} amt:{amount}')

    return (float(priceEntry), float(amount))

async def go_trade(exchange, symbol, chkLastPrice=True):
    global all_positions, balance_entry, count_trade, count_trade_long, count_trade_short

    # delay เพื่อให้กระจายการ trade ของ symbol มากขึ้น
    delay = randint(5,10)
    # จัดลำดับการ trade symbol
    if symbol in orders_history.keys():
        winRate = orders_history[symbol]['win']/orders_history[symbol]['trade']
        if winRate > 0.5:
            delay = 0
        elif winRate == 0.5:
             delay = 4
    await sleep(delay)

    # อ่านข้อมูลแท่งเทียนที่เก็บไว้ใน all_candles
    if symbol in all_candles.keys() and len(all_candles[symbol]) >= CANDLE_LIMIT:
        df = all_candles[symbol]
    else:
        print(f'not found candles for {symbol}')
        return
    # อ่านข้อมูล leverage ที่เก็บไว้ใน all_leverage
    if symbol in all_leverage.keys():
        leverage = all_leverage[symbol]
    else:
        print(f'not found leverage for {symbol}')
        return

    # limitTrade = config.limit_Trade

    hasLongPosition = False
    hasShortPosition = False
    positionAmt = 0.0
    
    positionInfo = all_positions.loc[all_positions['symbol']==symbol]

    #market_info = pd.DataFrame(await exchange.fapiPrivate_get_positionrisk(), columns=["symbol", "entryPrice", "leverage" ,"unrealizedProfit", "isolatedWallet", "positionAmt"])

    if not positionInfo.empty and float(positionInfo.iloc[-1]["positionAmt"]) != 0:
        positionAmt = float(positionInfo.iloc[-1]["positionAmt"])

    hasLongPosition = (positionAmt > 0)
    hasShortPosition = (positionAmt < 0)

    # print(countTrade, positionAmt, hasLongPosition, hasShortPosition, amount)

    # if positionAmt == 0 and symbol in orders_history.keys():
    #     await cancel_order(exchange, symbol)

    try:
        signalIdx = config.SignalIndex
        tradeMode = config.Trade_Mode
        TPSLMode = config.TPSL_Mode
        trailingStopMode = config.Trailing_Stop_Mode
        costType = config.CostType
        costAmount = config.CostAmount
        if symbol in symbols_setting.index:
            signalIdx = int(symbols_setting.loc[symbol]['signal_index'])
            tradeMode = symbols_setting.loc[symbol]['trade_mode']
            TPSLMode = symbols_setting.loc[symbol]['tpsl_mode']
            trailingStopMode = symbols_setting.loc[symbol]['trailing_stop_mode']
            costType = symbols_setting.loc[symbol]['cost_type']
            costAmount = float(symbols_setting.loc[symbol]['cost_amount'])

        fast = (df.iloc[signalIdx-1]['fast'], df.iloc[signalIdx]['fast'])
        mid = (df.iloc[signalIdx-1]['mid'], df.iloc[signalIdx]['mid'])
        slow = (df.iloc[signalIdx-1]['slow'], df.iloc[signalIdx]['slow'])
        
        isLongEnter = (fast[0] < slow[0] and fast[1] > slow[1])
        isLongExit = (fast[0] > mid[0] and fast[1] < mid[1])

        isShortEnter = (fast[0] > slow[0] and fast[1] < slow[1])
        isShortExit = (fast[0] < mid[0] and fast[1] > mid[1])

        # print(symbol, isBullish, isBearish, fast, slow)

        closePrice = df.iloc[-1]["close"]

        if tradeMode == 'on' and isShortExit == True and hasShortPosition == True:
            count_trade_short = count_trade_short - 1 if count_trade_short > 0 else 0
            count_trade = count_trade_long + count_trade_short
            await short_close(exchange, symbol, positionAmt)
            print(f"[{symbol}] สถานะ : Short Exit processing...")
            await cancel_order(exchange, symbol)
            # line_notify(f'{symbol}\nสถานะ : Short Exit')
            gather( line_chart(symbol, df, f'{symbol}\nสถานะ : Short Exit', 'SHORT EXIT') )

        elif tradeMode == 'on' and isLongExit == True and hasLongPosition == True:
            count_trade_long = count_trade_long - 1 if count_trade_long > 0 else 0
            count_trade = count_trade_long + count_trade_short
            await long_close(exchange, symbol, positionAmt)
            print(f"[{symbol}] สถานะ : Long Exit processing...")
            await cancel_order(exchange, symbol)
            # line_notify(f'{symbol}\nสถานะ : Long Exit')
            gather( line_chart(symbol, df, f'{symbol}\nสถานะ : Long Exit', 'LONG EXIT') )

        notify_msg = []
        notify_msg.append(symbol)

        if isLongEnter == True and config.Long == 'on' and hasLongPosition == False:
            TPLong = config.TP_Long
            TPCloseLong = config.TPclose_Long
            SLLong = config.SL_Long
            callbackLong = config.Callback_Long[0]
            activeTLLong = config.Active_TL_Long
            if symbol in symbols_setting.index:
                TPLong = float(symbols_setting.loc[symbol]['tp_long'])
                TPCloseLong = float(symbols_setting.loc[symbol]['tp_close_long'])
                SLLong = float(symbols_setting.loc[symbol]['sl_long'])
                # callbackLong = float(symbols_setting.loc[symbol]['callback_long'])
                callbackLong = [float(x.strip()) for x in symbols_setting.loc[symbol]['callback_long'].split(',')][0]
                activeTLLong = float(symbols_setting.loc[symbol]['active_tl_long'])

            print(f'{symbol:12} LONG')
            if tradeMode == 'on' and balance_entry > config.Not_Trade \
                and (config.limit_Trade > count_trade or config.limit_Trade_Long > count_trade_long) :
                count_trade_long = count_trade_long + 1
                count_trade = count_trade_long + count_trade_short
                (priceEntry, amount) = await cal_amount(exchange, symbol, leverage, costType, costAmount, closePrice, chkLastPrice)
                # ปรับปรุงค่า balance_entry
                balance_entry = balance_entry - (amount * priceEntry / leverage)
                print('balance_entry', balance_entry)
                await long_enter(exchange, symbol, amount)
                print(f"[{symbol}] Status : LONG ENTERING PROCESSING...")
                await cancel_order(exchange, symbol)
                notify_msg.append(f'สถานะ : Long\nCross Up\nราคา : {priceEntry:.5f}')

                logger.debug(f'{symbol} LONG\n{df.tail(3)}')
            
                closeRate = 100.0
                if TPSLMode == 'on':
                    notify_msg.append(f'# TPSL')
                    if config.TP_PNL > 0 or config.TP_PNL_Long > 0:
                        if config.TP_PNL_Long > 0:
                            pricetp = priceEntry + (config.TP_PNL_Long / amount)
                            notify_msg.append(f'TP PNL: {config.TP_PNL_Long:.2f} @{pricetp:.5f}')
                        else:
                            pricetp = priceEntry + (config.TP_PNL / amount)
                            notify_msg.append(f'TP PNL: {config.TP_PNL:.2f} @{pricetp:.5f}')
                    else:
                        closeRate = TPCloseLong
                        pricetp = priceEntry + (priceEntry * (TPLong / 100.0))
                        notify_msg.append(f'TP: {TPLong:.2f}% @{pricetp:.5f}')
                    notify_msg.append(f'TP close: {closeRate:.2f}%')
                    if config.SL_PNL > 0 or config.SL_PNL_Long > 0:
                        if config.SL_PNL_Long > 0:
                            pricesl = priceEntry - (config.SL_PNL_Long / amount)
                            notify_msg.append(f'SL PNL: {config.SL_PNL_Long:.2f} @{pricesl:.5f}')
                        else:
                            pricesl = priceEntry - (config.SL_PNL / amount)
                            notify_msg.append(f'SL PNL: {config.SL_PNL:.2f} @{pricesl:.5f}')
                    else:
                        pricesl = priceEntry - (priceEntry * (SLLong / 100.0))
                        notify_msg.append(f'SL: {SLLong:.2f}% @{pricesl:.5f}')

                    await long_TPSL(exchange, symbol, amount, priceEntry, pricetp, pricesl, closeRate)
                    print(f'[{symbol}] Set TP {pricetp:.5f} SL {pricesl:.5f}')
                    
                if trailingStopMode == 'on' and closeRate < 100.0:
                    priceTL = priceEntry + (priceEntry * (activeTLLong / 100.0))
                    await long_TLSTOP(exchange, symbol, amount, priceTL, callbackLong)
                    print(f'[{symbol}] Set Trailing Stop {priceTL:.4f}')
                    # callbackLong_str = ','.join(['{:.2f}%'.format(cb) for cb in callbackLong])
                    notify_msg.append(f'# TrailingStop\nCall Back: {callbackLong:.2f}%\nActive Price: {priceTL:.4f} {config.MarginType}')

                gather( line_chart(symbol, df, '\n'.join(notify_msg), 'LONG') )
                
            elif tradeMode != 'on' :
                gather( line_chart(symbol, df, f'{symbol}\nสถานะ : Long\nCross Up', 'LONG') )

        elif isShortEnter == True and config.Short == 'on' and hasShortPosition == False:
            TPShort = config.TP_Short
            TPCloseShort = config.TPclose_Short
            SLShort = config.SL_Short
            callbackShort = config.Callback_Short[0]
            activeTLShort = config.Active_TL_Short
            if symbol in symbols_setting.index:
                TPShort = float(symbols_setting.loc[symbol]['tp_short'])
                TPCloseShort = float(symbols_setting.loc[symbol]['tp_close_short'])
                SLShort = float(symbols_setting.loc[symbol]['sl_short'])
                # callbackShort = float(symbols_setting.loc[symbol]['callback_short'])
                callbackShort = [float(x.strip()) for x in symbols_setting.loc[symbol]['callback_short'].split(',')][0]
                activeTLShort = float(symbols_setting.loc[symbol]['active_tl_short'])

            print(f'{symbol:12} SHORT')
            if tradeMode == 'on' and balance_entry > config.Not_Trade \
                and (config.limit_Trade > count_trade or config.limit_Trade_Short > count_trade_short) :
                count_trade_short = count_trade_short + 1
                count_trade = count_trade_long + count_trade_short
                (priceEntry, amount) = await cal_amount(exchange, symbol, leverage, costType, costAmount, closePrice, chkLastPrice)
                # ปรับปรุงค่า balance_entry
                balance_entry = balance_entry - (amount * priceEntry / leverage)
                print('balance_entry', balance_entry)
                await short_enter(exchange, symbol, amount)
                print(f"[{symbol}] Status : SHORT ENTERING PROCESSING...")
                await cancel_order(exchange, symbol)
                notify_msg.append(f'สถานะ : Short\nCross Down\nราคา : {priceEntry:.5f}')

                logger.debug(f'{symbol} SHORT\n{df.tail(3)}')
            
                closeRate = 100.0
                if TPSLMode == 'on':
                    notify_msg.append(f'# TPSL')
                    if config.TP_PNL > 0 or config.TP_PNL_Short > 0:
                        if config.TP_PNL_Short > 0:
                            pricetp = priceEntry - (config.TP_PNL_Short / amount)
                            notify_msg.append(f'TP PNL: {config.TP_PNL_Short:.2f} @{pricetp:.5f}')
                        else:
                            pricetp = priceEntry - (config.TP_PNL / amount)
                            notify_msg.append(f'TP PNL: {config.TP_PNL:.2f} @{pricetp:.5f}')
                    else:
                        closeRate = TPCloseShort
                        pricetp = priceEntry - (priceEntry * (TPShort / 100.0))
                        notify_msg.append(f'TP: {TPShort:.2f}% @{pricetp:.5f}')
                    notify_msg.append(f'TP close: {closeRate:.2f}%')
                    if config.SL_PNL > 0 or config.SL_PNL_Short > 0:
                        if config.SL_PNL_Short > 0:
                            pricesl = priceEntry + (config.SL_PNL_Short / amount)
                            notify_msg.append(f'SL PNL: {config.SL_PNL_Short:.2f} @{pricesl:.5f}')
                        else:
                            pricesl = priceEntry + (config.SL_PNL / amount)
                            notify_msg.append(f'SL PNL: {config.SL_PNL:.2f} @{pricesl:.5f}')
                    else:
                        pricesl = priceEntry + (priceEntry * (SLShort / 100.0))
                        notify_msg.append(f'SL: {SLShort:.2f}% @{pricesl:.5f}')

                    await short_TPSL(exchange, symbol, amount, priceEntry, pricetp, pricesl, closeRate)
                    print(f'[{symbol}] Set TP {pricetp:.5f} SL {pricesl:.5f}')

                if trailingStopMode == 'on' and closeRate < 100.0:
                    priceTL = priceEntry - ((activeTLShort / 100.0) / amount)
                    await short_TLSTOP(exchange, symbol, amount, priceTL, callbackShort)
                    print(f'[{symbol}] Set Trailing Stop {priceTL:.4f}')
                    # callbackShort_str = ','.join(['{:.2f}%'.format(cb) for cb in callbackShort])
                    notify_msg.append(f'# TrailingStop\nCall Back: {callbackShort:.2f}%\nActive Price: {priceTL:.5f} {config.MarginType}')
 
                gather( line_chart(symbol, df, '\n'.join(notify_msg), 'SHORT') )

            elif tradeMode != 'on' :
                gather( line_chart(symbol, df, f'{symbol}\nสถานะ : Short\nCross Down', 'SHORT') )

    except Exception as ex:
        print(type(ex).__name__, str(ex))
        logger.exception('go_trade')
        pass

async def load_all_symbols():
    global all_symbols, watch_list
    try:
        exchange = getExchange()

        # t1=time.time()
        markets = await exchange.fetch_markets()
        # print(markets[0])
        mdf = pd.DataFrame(markets, columns=['id','quote','symbol','limits'])
        mdf.drop(mdf[mdf.quote != config.MarginType].index, inplace=True)
        # mdf.to_csv("fetch_markets.csv",index=False)
        # mdf['minAmount'] = mdf['limits'].apply(lambda x: x['amount']['min'])
        mdf['minCost'] = mdf['limits'].apply(lambda x: x['cost']['min'])
        # print(mdf.columns)
        # print(mdf.head())
        drop_value = ['BTCUSDT_221230','ETHUSDT_221230']
        # all_symbols = {r['id']:{'symbol':r['symbol'],'minAmount':r['minAmount']} for r in mdf[~mdf['id'].isin(drop_value)][['id','symbol','minAmount']].to_dict('records')}
        all_symbols = {r['id']:{'symbol':r['symbol'],'minCost':r['minCost']} for r in mdf[~mdf['id'].isin(drop_value)][['id','symbol','minCost']].to_dict('records')}
        # print(all_symbols, len(all_symbols))
        # print(all_symbols.keys())
        if len(config.watch_list) > 0:
            watch_list_tmp = list(filter(lambda x: x in all_symbols.keys(), config.watch_list))
        else:
            watch_list_tmp = all_symbols.keys()
        # remove sysbol if in back_list
        watch_list = list(filter(lambda x: x not in config.back_list, watch_list_tmp))
        # print(watch_list)
        # t2=(time.time())-t1
        # print(f'ใช้เวลาหาว่ามีเหรียญ เทรดฟิวเจอร์ : {t2:0.2f} วินาที')
        
        print(f'total     : {len(all_symbols.keys())} symbols')
        print(f'target    : {len(watch_list)} symbols')

        logger.info(f'all:{len(all_symbols.keys())} watch:{len(watch_list)}')

    except Exception as ex:
        print(type(ex).__name__, str(ex))
        logger.exception('load_all_symbols')

    finally:
        await exchange.close()

async def set_all_leverage():
    try:
        exchange = getExchange()

        # set leverage
        loops = [set_leverage(exchange, symbol, config.MarginType) for symbol in watch_list]
        await gather(*loops)
        # แสดงค่า leverage
        # print(all_leverage)
        print(f'#leverage : {len(all_leverage.keys())} symbols')

        logger.info(f'leverage:{len(all_leverage.keys())}')

    except Exception as ex:
        print(type(ex).__name__, str(ex))
        logger.exception('set_all_leverage')

    finally:
        await exchange.close()

async def fetch_first_ohlcv():
    try:
        exchange = getExchange()

        # ครั้งแรกอ่าน 1000 แท่ง -> CANDLE_LIMIT
        limit = CANDLE_LIMIT

        if TIMEFRAME_SECONDS[config.timeframe] >= TIMEFRAME_SECONDS['4h']:
            # อ่านแท่งเทียนแบบ async และ เทรดตามสัญญาน
            loops = [fetch_ohlcv_trade(exchange, symbol, config.timeframe, limit) for symbol in watch_list]
            await gather(*loops)
        else:
            # อ่านแท่งเทียนแบบ async แต่ ยังไม่เทรด
            loops = [fetch_ohlcv(exchange, symbol, config.timeframe, limit) for symbol in watch_list]
            await gather(*loops)

    except Exception as ex:
        print(type(ex).__name__, str(ex))
        logger.exception('set_all_leverage')

    finally:
        await exchange.close()

async def fetch_next_ohlcv(next_ticker):
    try:
        exchange = getExchange()

        # กำหนด limit การอ่านแท่งเทียนแบบ 0=ไม่ระบุจำนวน, n=จำนวน n แท่ง
        limit = 0

        # อ่านแท่งเทียนแบบ async และ เทรดตามสัญญาน
        loops = [fetch_ohlcv_trade(exchange, symbol, config.timeframe, limit, next_ticker) for symbol in watch_list]
        await gather(*loops)

    except Exception as ex:
        print(type(ex).__name__, str(ex))
        logger.exception('fetch_next_ohlcv')

    finally:
        await exchange.close()

async def mm_strategy(exchange, mm_positions):
    try:
        # sumProfit = sum([float(position['unrealizedProfit']) for position in mm_positions])
        sumLongProfit = sum([float(position['unrealizedProfit']) for position in mm_positions if float(position['positionAmt']) >= 0])
        sumShortProfit = sum([float(position['unrealizedProfit']) for position in mm_positions if float(position['positionAmt']) < 0])
        sumProfit = sumLongProfit + sumShortProfit

        # Money Management (MM) Strategy
        logger.debug(f'Profit: Long[{sumLongProfit}] + Short[{sumShortProfit}] = All[{sumProfit}]')
        logger.debug(f'All: {config.TP_Profit}, {config.SL_Profit}')
        logger.debug(f'Long: {config.TP_Profit_Long}, {config.SL_Profit_Long}')
        logger.debug(f'Short: {config.TP_Profit_Short}, {config.SL_Profit_Short}')
        # logger.debug(f'PNL: {config.TP_PNL}, {config.SL_PNL}')

        if (config.TP_Profit > 0 and sumProfit > config.TP_Profit) or \
            (config.SL_Profit > 0 and sumProfit < -config.SL_Profit):

            exit_loops = []
            cancel_loops = []
            mm_notify = []
            # exit all positions
            for position in mm_positions:
                symbol = position['symbol']
                positionAmt = float(position['positionAmt'])
                if positionAmt > 0.0:
                    print(f"[{symbol}] สถานะ : MM Long Exit processing...")
                    exit_loops.append(long_close(exchange, symbol, positionAmt))
                    # line_notify(f'{symbol}\nสถานะ : MM Long Exit\nProfit = {sumProfit}')
                    mm_notify.append(f'{symbol} : MM Long Exit')
                elif positionAmt < 0.0:
                    print(f"[{symbol}] สถานะ : MM Short Exit processing...")
                    exit_loops.append(short_close(exchange, symbol, positionAmt))
                    # line_notify(f'{symbol}\nสถานะ : MM Short Exit\nProfit = {sumProfit}')
                    mm_notify.append(f'{symbol} : MM Short Exit')
                cancel_loops.append(cancel_order(exchange, symbol))
            await gather(*exit_loops)
            await gather(*cancel_loops)
            if len(mm_notify) > 0:
                txt_notify = '\n'.join(mm_notify)
                line_notify(f'\nสถานะ...\n{txt_notify}\nProfit = {sumProfit:.4f}')
            logger.debug(mm_positions)
        
        else:

            isTPLongExit = (config.TP_Profit_Long > 0 and sumLongProfit > config.TP_Profit_Long)
            isSLLongExit = (config.SL_Profit_Long > 0 and sumLongProfit < -config.SL_Profit_Long)
            isTPShortExit = (config.TP_Profit_Short > 0 and sumShortProfit > config.TP_Profit_Short)
            isSLShortExit = (config.SL_Profit_Short > 0 and sumShortProfit < -config.SL_Profit_Short)

            if isTPLongExit or isSLLongExit:
                exit_loops = []
                cancel_loops = []
                mm_notify = []
                # exit all positions
                for position in mm_positions:
                    symbol = position['symbol']
                    positionAmt = float(position['positionAmt'])
                    if positionAmt > 0.0:
                        print(f"[{symbol}] สถานะ : MM Long Exit processing...")
                        exit_loops.append(long_close(exchange, symbol, positionAmt))
                        # line_notify(f'{symbol}\nสถานะ : MM Long Exit\nProfit = {sumProfit}')
                        mm_notify.append(f'{symbol} : MM Long Exit')
                        cancel_loops.append(cancel_order(exchange, symbol))
                await gather(*exit_loops)
                await gather(*cancel_loops)
                if len(mm_notify) > 0:
                    txt_notify = '\n'.join(mm_notify)
                    line_notify(f'\nสถานะ...\n{txt_notify}\nProfit = {sumProfit:.4f}')
                logger.debug(mm_positions)

            if isTPShortExit or isSLShortExit:
                exit_loops = []
                cancel_loops = []
                mm_notify = []
                # exit all positions
                for position in mm_positions:
                    symbol = position['symbol']
                    positionAmt = float(position['positionAmt'])
                    if positionAmt < 0.0:
                        print(f"[{symbol}] สถานะ : MM Short Exit processing...")
                        exit_loops.append(short_close(exchange, symbol, positionAmt))
                        # line_notify(f'{symbol}\nสถานะ : MM Short Exit\nProfit = {sumProfit}')
                        mm_notify.append(f'{symbol} : MM Short Exit')
                        cancel_loops.append(cancel_order(exchange, symbol))
                await gather(*exit_loops)
                await gather(*cancel_loops)
                if len(mm_notify) > 0:
                    txt_notify = '\n'.join(mm_notify)
                    line_notify(f'\nสถานะ...\n{txt_notify}\nProfit = {sumProfit:.4f}')
                logger.debug(mm_positions)

        #loss conter
        if config.Loss_Limit > 0:
            for symbol in orders_history.keys():
                if orders_history[symbol]['last_loss'] >= config.Loss_Limit and symbol in watch_list:
                    watch_list.pop(symbol)
                    print(f'{symbol} removed from watch_list, last loss = {orders_history[symbol]["last_loss"]}')
                    logger.info(f'{symbol} removed from watch_list, last loss = {orders_history[symbol]["last_loss"]}')

    except Exception as ex:
        print(type(ex).__name__, str(ex))
        logger.exception('mm_strategy')

async def update_all_balance(marginType, checkMM=True):
    global all_positions, balance_entry, balalce_total, count_trade, count_trade_long, count_trade_short, orders_history
    try:
        exchange = getExchange()

        balance = await exchange.fetch_balance()
        ex_positions = balance['info']['positions']
        positions = [position for position in ex_positions 
            if position['symbol'].endswith(marginType) and float(position['positionAmt']) != 0]

        if checkMM:
            await mm_strategy(exchange, positions)
        
        # sumLongProfit = sum([float(position['unrealizedProfit']) for position in positions if float(position['positionAmt']) >= 0])
        # sumShortProfit = sum([float(position['unrealizedProfit']) for position in positions if float(position['positionAmt']) < 0])
        # sumProfit = sumLongProfit + sumShortProfit
        # sumLongMargin = sum([float(position['initialMargin']) for position in positions if float(position['positionAmt']) >= 0])
        # sumShortMargin = sum([float(position['initialMargin']) for position in positions if float(position['positionAmt']) < 0])
        sumProfit = sum([float(position['unrealizedProfit']) for position in positions])
        sumMargin = sum([float(position['initialMargin']) for position in positions])

        all_positions = pd.DataFrame(positions, columns=POSITION_COLUMNS)
        all_positions["pd."] = all_positions['positionAmt'].apply(lambda x: 'LONG' if float(x) >= 0 else 'SHORT')
        count_trade = len(all_positions)
        count_trade_long = sum(all_positions["pd."].map(lambda x : x == 'LONG'))
        count_trade_short = sum(all_positions["pd."].map(lambda x : x == 'SHORT'))
        freeBalance =  await exchange.fetch_free_balance()
        balance_entry = float(freeBalance[marginType])

        all_positions['unrealizedProfit'] = all_positions['unrealizedProfit'].apply(lambda x: '{:,.2f}'.format(float(x)))
        all_positions['initialMargin'] = all_positions['initialMargin'].apply(lambda x: '{:,.2f}'.format(float(x)))
        balalce_total = balance_entry + sumMargin + sumProfit
        balance_change = balalce_total - start_balance_total if start_balance_total > 0 else 0
        if config.Trade_Mode == 'on':
            # print("all_positions ================")
            print(all_positions)
            if config.limit_Trade > 0:
                print(f"Count Trade ===== {count_trade}/{config.limit_Trade}")
            else:
                print(f"Count Trade ===== Long: {count_trade_long}/{config.limit_Trade_Long} Short: {count_trade_short}/{config.limit_Trade_Short}")
            print(f"Balance Entry === {balance_entry:,.4f} Margin: {sumMargin:+,.4f} Profit: {sumProfit:+,.4f}")
            print(f"Total Balance === {balalce_total:,.4f} Change: {balance_change:+,.4f}")
                
        logger.info(f'countTrade:{count_trade} (L:{count_trade_long},S:{count_trade_short}) balance_entry:{balance_entry} sumMargin:{sumMargin} sumProfit:{sumProfit}')

        loops = [cancel_order(exchange, symbol) for symbol in orders_history.keys() if orders_history[symbol]['position'] == 'open' and symbol not in all_positions['symbol'].to_list()]
        await gather(*loops)

        for symbol in orders_history.keys():
            if orders_history[symbol]['position'] == 'open' and symbol not in all_positions['symbol'].to_list():
                orders_history[symbol]['position'] = 'close' 
    
        logger.debug(orders_history)
        save_orders_history()

    except Exception as ex:
        print(type(ex).__name__, str(ex))
        logger.exception('update_all_balance')

    finally:
        await exchange.close()

async def load_symbols_setting():
    global symbols_setting
    try:
        if config.CSV_NAME:
            symbols_setting = pd.read_csv(config.CSV_NAME, skipinitialspace=True)
            if any(x in CSV_COLUMNS for x in symbols_setting.columns.to_list()):
                symbols_setting.drop(symbols_setting[symbols_setting.margin_type != config.MarginType].index, inplace=True)
                symbols_setting['id'] = symbols_setting['symbol']+symbols_setting['margin_type']
                symbols_setting.set_index('id', inplace=True)
                # เอาอันซ้ำออก เหลืออันใหม่สุด
                symbols_setting = symbols_setting[~symbols_setting.index.duplicated(keep='last')]

                # validate all values
                int_columns = [
                        'fast_value', 'mid_value', 'slow_value', 'signal_index', 'leverage'
                        ]
                float_columns = [
                        'cost_amount', 
                        'tp_long', 'tp_close_long', 'sl_long', 'callback_long', 'active_tl_long',
                        'tp_short', 'tp_close_short', 'sl_short', 'callback_short', 'active_tl_short'
                        ]
                symbols_setting[int_columns] = symbols_setting[int_columns].apply(pd.to_numeric, errors='coerce')
                symbols_setting[float_columns] = symbols_setting[float_columns].apply(pd.to_numeric, downcast='float', errors='coerce')
                symbols_setting.dropna(inplace=True)

                # print(symbols_setting.head())
                # print(symbols_setting.iloc[1])
                # validate all setting

                logger.info(f'success load symbols_setting from {config.CSV_NAME}')
            else:
                symbols_setting = pd.DataFrame(columns=CSV_COLUMNS)
                print(f'fail load symbols_setting from {config.CSV_NAME}, all columns not match')
                logger.info(f'fail load symbols_setting from {config.CSV_NAME}, all columns not match')

    except Exception as ex:
        symbols_setting = pd.DataFrame(columns=CSV_COLUMNS)
        print(type(ex).__name__, str(ex))
        logger.exception('load_symbols_setting')

async def close_non_position_order(watch_list, positions_list):
    try:
        exchange = getExchange()

        loops = [cancel_order(exchange, symbol) for symbol in watch_list if symbol not in positions_list]
        await gather(*loops)
    
    except Exception as ex:
        print(type(ex).__name__, str(ex))
        logger.exception('update_all_balance')

    finally:
        await exchange.close()

async def main():
    global start_balance_total

    if config.SANDBOX:
        bot_title = f'{bot_name} - {config.timeframe} - {config.MarginType} (SANDBOX)'
    else:
        bot_title = f'{bot_name} - {config.timeframe} - {config.MarginType}'

    # set cursor At top, left (1,1)
    print(CLS_SCREEN+bot_title)

    await load_all_symbols()

    await load_symbols_setting()

    await set_all_leverage()

    # kwargs = dict(
    #     limitTrade=config.limit_Trade,
    # )

    time_wait = TIMEFRAME_SECONDS[config.timeframe] # กำหนดเวลาต่อ 1 รอบ
    time_wait_ub = UB_TIMER_SECONDS[config.UB_TIMER_MODE] # กำหนดเวลา update balance

    # อ่านแท่งเทียนทุกเหรียญ
    t1=time.time()
    local_time = time.ctime(t1)
    print(f'get all candles: {local_time}')

    await fetch_first_ohlcv()
        
    t2=(time.time())-t1
    print(f'total time : {t2:0.2f} secs')
    logger.info(f'first ohlcv: {t2:0.2f} secs')

    # แสดงค่า positions & balance
    await update_all_balance(config.MarginType)
    start_balance_total = balalce_total

    await set_order_history(all_positions['symbol'].to_list())
    await close_non_position_order(watch_list, all_positions['symbol'].to_list())

    try:
        start_ticker = time.time()
        next_ticker = start_ticker - (start_ticker % time_wait) # ตั้งรอบเวลา
        next_ticker += time_wait # กำหนดรอบเวลาถัดไป
        next_ticker_ub = start_ticker - (start_ticker % time_wait_ub)
        next_ticker_ub += time_wait_ub
        while True:
            seconds = time.time()
            if seconds >= next_ticker + TIME_SHIFT: # ครบรอบ
                # set cursor At top, left (1,1)
                print(CLS_SCREEN+bot_title)

                local_time = time.ctime(seconds)
                print(f'calculate new indicator: {local_time}')
                
                await update_all_balance(config.MarginType)

                t1=time.time()

                await fetch_next_ohlcv(next_ticker)

                t2=(time.time())-t1
                print(f'total time : {t2:0.2f} secs')
                logger.info(f'update ohlcv: {t2:0.2f} secs (include trade)')

                next_ticker += time_wait # กำหนดรอบเวลาถัดไป
                next_ticker_ub += time_wait_ub

                await sleep(10)

            elif config.Trade_Mode == 'on' and seconds >= next_ticker_ub + TIME_SHIFT:
                # set cursor At top, left (1,1)
                print(CLS_SCREEN+bot_title)
                balance_time = time.ctime(seconds)
                print(f'last indicator: {local_time}, last balance: {balance_time}')
                await update_all_balance(config.MarginType)
                next_ticker_ub += time_wait_ub

            await sleep(1)

    except KeyboardInterrupt:
        pass

    except Exception as ex:
        print(type(ex).__name__, str(ex))
        logger.exception('main')

async def waiting():
    count = 0
    status = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
    while True:
        await sleep(1)
        print('\r'+CGREEN+CBOLD+status[count%len(status)]+' waiting...\r'+CEND, end='')
        count += 1
        count = count%len(status)

if __name__ == "__main__":
    try:
        pathlib.Path('./plots').mkdir(parents=True, exist_ok=True)
        pathlib.Path('./logs').mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger("App Log")
        logger.setLevel(config.LOG_LEVEL)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler = RotatingFileHandler('./logs/app.log', maxBytes=200000, backupCount=5)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        logger.info('start ==========')
        os.system("color") # enables ansi escape characters in terminal
        print(HIDE_CURSOR, end="")
        loop = get_event_loop()
        # แสดง status waiting ระหว่างที่รอ...
        loop.create_task(main())
        loop.run_until_complete(waiting())        

    except KeyboardInterrupt:
        print(CLS_LINE+'\rbye')

    except Exception as ex:
        print(type(ex).__name__, str(ex))
        logger.exception('app')

    finally:
        print(SHOW_CURSOR, end="")