import os
import time
import ccxt
import pandas as pd
import pandas_ta as ta

API_KEY = os.getenv('BINANCE_API_KEY')
SECRET_KEY = os.getenv('BINANCE_SECRET_KEY')

exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot'
    }
})

SYMBOL = 'BTC/USDT'
TIMEFRAME = '1h'
TRADE_AMOUNT_USDT = 20

def check_position():
    try:
        balance = exchange.fetch_balance()
        btc_free = balance['free'].get('BTC', 0.0)
        return btc_free > 0.0001 
    except Exception as e:
        print(f"❌ Error al consultar balance: {e}")
        return False

def get_market_data():
    try:
        ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        df['ema20'] = ta.ema(df['close'], length=20)
        df['ema50'] = ta.ema(df['close'], length=50)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['vol_ma'] = ta.sma(df['volume'], length=20)
        
        return df.iloc[-1]
    except Exception as e:
        print(f"❌ Error obteniendo datos de mercado: {e}")
        return None

def execute_trade():
    print("🔎 Analizando métricas de mercado...")
    data = get_market_data()
    if data is None:
        return

    close_price = data['close']
    ema20 = data['ema20']
    ema50 = data['ema50']
    rsi = data['rsi']
    volume = data['volume']
    vol_ma = data['vol_ma']
    
    has_position = check_position()
    
    print(f"Precio: {close_price} | EMA20: {ema20:.2f} | EMA50: {ema50:.2f} | RSI: {rsi:.2f}")

    buy_condition = (ema20 > ema50) and (45 < rsi < 60) and (volume > vol_ma * 1.2)
    sell_condition = (ema20 < ema50) or (rsi > 75)

    if buy_condition and not has_position:
        print(f"🚀 Señal de COMPRA detectada...")
        try:
            order = exchange.create_market_buy_order(SYMBOL, TRADE_AMOUNT_USDT / close_price)
            print(f"✅ Compra exitosa: {order['id']}")
        except Exception as e:
            print(f"❌ Error al comprar: {e}")

    elif sell_condition and has_position:
        print(f"⚠️ Señal de VENTA detectada...")
        try:
            balance = exchange.fetch_balance()
            btc_to_sell = balance['free'].get('BTC', 0.0)
            order = exchange.create_market_sell_order(SYMBOL, btc_to_sell)
            print(f"✅ Venta exitosa: {order['id']}")
        except Exception as e:
            print(f"❌ Error al vender: {e}")
    else:
        print("⏳ Criterios no cumplidos. Esperando próxima ventana.")

if __name__ == "__main__":
    print("🤖 Bot iniciado...")
    while True:
        execute_trade()
        time.sleep(900)
