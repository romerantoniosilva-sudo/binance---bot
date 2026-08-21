
import time
import math
from ccxt import binance

# ==========================================
# ⚙️ CONFIGURACIÓN DE SEGURIDAD Y PARÁMETROS
# ==========================================
# Las claves se mantienen seguras en tus variables de entorno o archivo de config
BINANCE_API_KEY = "TU_API_KEY_AQUI"
BINANCE_SECRET_KEY = "TU_SECRET_KEY_AQUI"

SYMBOL = 'BTC/USDT'
TIMEFRAME = '1h'

# Parámetros de la estrategia
MONTO_POR_COMPRA_USDT = 10.0
MAX_OPERACIONES = 6
RESERVA_USDT = 4.0

# Niveles de caída desde el precio de referencia
PORCENTAJES_CAIDA = [-0.02, -0.04, -0.06, -0.09, -0.12, -0.15]
TAKE_PROFIT_PORCENTAJE = 0.06

# Inicialización de CCXT
exchange = binance({
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_SECRET_KEY,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot'  # Forzar mercado Spot
    }
})

# ==========================================
# 📊 VARIABLES DE CONTROL DE ESTADO
# ==========================================
# Registra qué niveles ya fueron ejecutados en el ciclo actual para no duplicar
niveles_comprados = {i: False for i in range(MAX_OPERACIONES)}
precio_referencia = None

def obtener_precio_actual():
    ticker = exchange.fetch_ticker(SYMBOL)
    return float(ticker['last'])

def ajustar_precision_cantidad(simbolo, cantidad):
    market = exchange.market(simbolo)
    step_size = market['limits']['amount']['min']
    precision = int(-math.log10(step_size))
    return round(cantidad, precision)

def ejecutar_bot():
    global precio_referencia, niveles_comprados
    
    try:
        precio_actual = obtener_precio_actual()
        
        # 1. Establecer precio de referencia inicial (primera ejecución)
        if precio_referencia is None:
            precio_referencia = precio_actual
            print(f"📌 Precio de referencia inicial establecido en: {precio_referencia} USDT")
            return

        print(f"🔍 Revisando mercado... Precio actual: {precio_actual} USDT | Referencia: {precio_referencia} USDT")

        # 2. Evaluar cada nivel de caída de la estrategia
        for i, caida in enumerate(PORCENTAJES_CAIDA):
            # Si el nivel ya se usó, se salta para no comprar dos veces
            if niveles_comprados[i]:
                continue
                
            precio_objetivo_nivel = precio_referencia * (1 + caida)
            
            # Si el precio actual cruzó hacia abajo el nivel objetivo
            if precio_actual <= precio_objetivo_nivel:
                print(f"⚠️ Nivel {i+1} alcanzado ({caida*100}%). Comprando {MONTO_POR_COMPRA_USDT} USDT...")
                
                # Calcular la cantidad de BTC exacta basada en el precio de mercado actual
                cantidad_btc = MONTO_POR_COMPRA_USDT / precio_actual
                cantidad_ajustada = ajustar_precision_cantidad(SYMBOL, cantidad_btc)
                
                # Ejecutar Compra a Mercado (Evita el error de interpretar 10 USDT como 10 BTC)
                orden_compra = exchange.create_market_buy_order(SYMBOL, cantidad_ajustada)
                precio_ejecucion = orden_compra['average'] if orden_compra['average'] else precio_actual
                
                print(f"✅ Compra ejecutada: {cantidad_ajustada} BTC a un precio prom. de {precio_ejecucion} USDT")
                
                # Marcar nivel como utilizado inmediatamente
                niveles_comprados[i] = True
                
                # 3. Colocar automáticamente el Take Profit LIMIT de +6%
                precio_tp = precio_ejecucion * (1 + TAKE_PROFIT_PORCENTAJE)
                print(f"⏳ Colocando orden Take Profit LIMIT (+6%) en: {precio_tp:.2f} USDT")
                
                exchange.create_limit_sell_order(SYMBOL, cantidad_ajustada, precio_tp)
                
        # Resetear la referencia si todos los niveles se completaron (fin del ciclo)
        if all(niveles_comprados.values()):
            print("🔄 Todos los niveles ejecutados. Reiniciando ciclo y precio de referencia.")
            precio_referencia = None
            niveles_comprados = {i: False for i in range(MAX_OPERACIONES)}

    except Exception as e:
        print(f"❌ Error en la ejecución: {e}")

# ==========================================
# 🔄 BUCLE DE EJECUCIÓN CONTINUA (Cada 5 min)
# ==========================================
if __name__ == '__main__':
    print("🚀 Bot iniciado. Monitoreando BTC/USDT cada 5 minutos...")
    while True:
        ejecutar_bot()
        time.sleep(300)  # 300 segundos = 5 minutos
