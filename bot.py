s
import time
import math
import os
import threading
import requests
from ccxt import binance
from fastapi import FastAPI

# ==========================================
# 🌐 CONFIGURACIÓN DEL SERVIDOR WEB (Para Render)
# ==========================================
app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok", "bot": "running", "pair": SYMBOL}

# ==========================================
# 🔒 CONFIGURACIÓN DE SEGURIDAD (Variables de Entorno)
# ==========================================
# Render inyectará estos valores de forma privada y segura
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Parámetros operativos del par
SYMBOL = 'BTC/USDT'
TIMEFRAME = '1h'

# Parámetros de la estrategia
MONTO_POR_COMPRA_USDT = 10.0
MAX_OPERACIONES = 6
RESERVA_USDT = 4.0

PORCENTAJES_CAIDA = [-0.02, -0.04, -0.06, -0.09, -0.12, -0.15]
TAKE_PROFIT_PORCENTAJE = 0.06

# Inicialización de CCXT
exchange = binance({
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_SECRET_KEY,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

# Control de estado interno
niveles_comprados = {i: False for i in range(MAX_OPERACIONES)}
precio_referencia = None

# ==========================================
# 📢 FUNCIÓN DE NOTIFICACIÓN TELEGRAM
# ==========================================
def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Configuración de Telegram incompleta.")
        return
    url = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': mensaje, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ Error Telegram: {e}")

# ==========================================
# 🔧 FUNCIONES AUXILIARES
# ==========================================
def obtener_precio_actual():
    ticker = exchange.fetch_ticker(SYMBOL)
    return float(ticker['last'])

def ajustar_precision_cantidad(simbolo, cantidad):
    market = exchange.market(simbolo)
    step_size = market['limits']['amount']['min']
    precision = int(-math.log10(step_size))
    return round(cantidad, precision)

# ==========================================
# 🤖 LÓGICA PRINCIPAL DEL BOT
# ==========================================
def ejecutar_bot():
    global precio_referencia, niveles_comprados
    try:
        precio_actual = obtener_precio_actual()
        
        if precio_referencia is None:
            precio_referencia = precio_actual
            msg = f"📌 *Bot Inicializado en Render*\nPrecio de referencia: `{precio_referencia} USDT`"
            print(msg.replace('*', '').replace('`', ''))
            enviar_telegram(msg)
            return

        print(f"🔍 Revisando... Precio: {precio_actual} USDT | Ref: {precio_referencia} USDT")

        for i, caida in enumerate(PORCENTAJES_CAIDA):
            if niveles_comprados[i]:
                continue
                
            precio_objetivo_nivel = precio_referencia * (1 + caida)
            
            if precio_actual <= precio_objetivo_nivel:
                msg_alerta = f"⚠️ *Nivel {i+1} Alcanzado ({caida*100}%).*\nPrecio actual: `{precio_actual:.2f}`\nEjecutando compra..."
                enviar_telegram(msg_alerta)
                
                cantidad_btc = MONTO_POR_COMPRA_USDT / precio_actual
                cantidad_ajustada = ajustar_precision_cantidad(SYMBOL, cantidad_btc)
                
                orden_compra = exchange.create_market_buy_order(SYMBOL, cantidad_ajustada)
                precio_ejecucion = orden_compra['average'] if orden_compra['average'] else precio_actual
                
                msg_compra = f"✅ *Compra Ejecutada*\nCantidad: `{cantidad_ajustada} BTC`\nPrecio Promedio: `{precio_ejecucion} USDT`"
                enviar_telegram(msg_compra)
                
                niveles_comprados[i] = True
                
                precio_tp = precio_ejecucion * (1 + TAKE_PROFIT_PORCENTAJE)
                exchange.create_limit_sell_order(SYMBOL, cantidad_ajustada, precio_tp)
                
                msg_tp = f"⏳ *Take Profit Colocado (+6%)*\nPrecio de venta: `{precio_tp:.2f} USDT`"
                enviar_telegram(msg_tp)
                
        if all(niveles_comprados.values()):
            enviar_telegram("🔄 *Ciclo Completado*\nReiniciando precio de referencia.")
            precio_referencia = None
            niveles_comprados = {i: False for i in range(MAX_OPERACIONES)}

    except Exception as e:
        enviar_telegram(f"❌ *Error en el Bot:* `{str(e)}`")

def bucle_bot():
    print("🚀 Hilo del bot de trading iniciado.")
    while True:
        ejecutar_bot()
        time.sleep(300)

# ==========================================
# 🚀 ARRANQUE EN RENDER
# ==========================================
if __name__ == '__main__':
    import uvicorn
    
    hilo_trading = threading.Thread(target=bucle_bot, daemon=True)
    hilo_trading.start()
    
    puerto = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=puerto)
