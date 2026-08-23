import time
import os
import threading
import requests
from ccxt import binance
from fastapi import FastAPI

# ==========================================
# 🌐 CONFIGURACIÓN DEL SERVIDOR WEB
# ==========================================
app = FastAPI()

# ==========================================
# 🔒 VARIABLES DE ENTORNO
# ==========================================
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ==========================================
# ⚙️ CONFIGURACIÓN
# ==========================================
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"

MONTO_POR_COMPRA_USDT = 10.0
MAX_OPERACIONES = 6
RESERVA_USDT = 4.0

PORCENTAJES_CAIDA = [-0.02, -0.04, -0.06, -0.09, -0.12, -0.15]
TAKE_PROFIT_PORCENTAJE = 0.06

INTERVALO_REVISION_SEGUNDOS = 300

# ==========================================
# 🔌 CONEXIÓN BINANCE
# ==========================================
exchange = binance({
    "apiKey": BINANCE_API_KEY,
    "secret": BINANCE_SECRET_KEY,
    "enableRateLimit": True,
    "options": {
        "defaultType": "spot"
    }
})

exchange.load_markets()

# ==========================================
# 📊 ESTADO DEL BOT
# ==========================================
precio_referencia = None

# Cada nivel representa una operación activa.
# True = hay una compra con TP pendiente.
niveles_activos = {
    i: False for i in range(MAX_OPERACIONES)
}

# Órdenes TP creadas por el bot.
ordenes_tp = {}

# ==========================================
# 🌐 SERVIDOR RENDER
# ==========================================
@app.get("/")
def home():
    return {
        "status": "ok",
        "bot": "running",
        "pair": SYMBOL,
        "reference_price": precio_referencia
    }


# ==========================================
# 📢 TELEGRAM
# ==========================================
def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram no está configurado.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje
    }

    try:
        respuesta = requests.post(
            url,
            json=payload,
            timeout=10
        )

        if not respuesta.ok:
            print(f"❌ Error Telegram: {respuesta.text}")

    except Exception as e:
        print(f"❌ Error enviando Telegram: {e}")


# ==========================================
# 💰 SALDO USDT
# ==========================================
def obtener_saldo_usdt():
    balance = exchange.fetch_balance()

    saldo_libre = balance["free"].get("USDT", 0)

    return float(saldo_libre)


# ==========================================
# ₿ PRECIO BTC
# ==========================================
def obtener_precio_actual():
    ticker = exchange.fetch_ticker(SYMBOL)

    return float(ticker["last"])


# ==========================================
# 🔢 PRECISIÓN DE CANTIDAD
# ==========================================
def ajustar_precision_cantidad(cantidad):
    try:
        cantidad_ajustada = exchange.amount_to_precision(
            SYMBOL,
            cantidad
        )

        return float(cantidad_ajustada)

    except Exception as e:
        print(f"❌ Error ajustando cantidad: {e}")
        return 0.0


# ==========================================
# 📋 OBTENER ÓRDENES ABIERTAS
# ==========================================
def obtener_ordenes_abiertas():
    try:
        return exchange.fetch_open_orders(SYMBOL)

    except Exception as e:
        print(f"❌ Error consultando órdenes abiertas: {e}")
        return []


# ==========================================
# 🔄 SINCRONIZAR ESTADO CON BINANCE
# ==========================================
def sincronizar_estado():

    global niveles_activos
    global ordenes_tp

    try:

        ordenes_abiertas = obtener_ordenes_abiertas()

        nuevos_niveles = {
            i: False for i in range(MAX_OPERACIONES)
        }

        nuevas_ordenes = {}

        for orden in ordenes_abiertas:

            if orden.get("side") != "sell":
                continue

            client_id = (
                orden.get("clientOrderId")
                or orden.get("info", {}).get("clientOrderId")
                or ""
            )

            # Nuestro formato:
            # BOT_TP_NIVEL_TIMESTAMP
            if client_id.startswith("BOT_TP_"):

                partes = client_id.split("_")

                if len(partes) >= 3:

                    try:
                        nivel = int(partes[2])

                        if 0 <= nivel < MAX_OPERACIONES:

                            nuevos_niveles[nivel] = True
                            nuevas_ordenes[nivel] = orden["id"]

                    except ValueError:
                        pass

        niveles_activos = nuevos_niveles
        ordenes_tp = nuevas_ordenes

    except Exception as e:

        print(f"❌ Error sincronizando estado: {e}")


# ==========================================
# 🛒 EJECUTAR COMPRA
# ==========================================
def ejecutar_compra(nivel, precio_actual):

    global niveles_activos
    global ordenes_tp

    try:

        saldo_usdt = obtener_saldo_usdt()

        # Nunca utilizar la reserva de USDT.
        saldo_disponible_para_comprar = (
            saldo_usdt - RESERVA_USDT
        )

        if saldo_disponible_para_comprar < MONTO_POR_COMPRA_USDT:

            enviar_telegram(
                "⚠️ *Compra cancelada*\n"
                f"Saldo USDT: `{saldo_usdt:.2f}`\n"
                f"Reserva mínima: `{RESERVA_USDT:.2f}`"
            )

            return False

        # Calculamos BTC equivalente a 10 USDT.
        cantidad_btc = (
            MONTO_POR_COMPRA_USDT / precio_actual
        )

        cantidad_ajustada = ajustar_precision_cantidad(
            cantidad_btc
        )

        if cantidad_ajustada <= 0:
            print("❌ Cantidad BTC inválida.")
            return False

        enviar_telegram(
            f"⚠️ *Nivel {nivel + 1} alcanzado*\n"
            f"Caída: `{PORCENTAJES_CAIDA[nivel] * 100:.0f}%`\n"
            f"Precio: `{precio_actual:.2f} USDT`\n"
            f"💰 Ejecutando compra de `{MONTO_POR_COMPRA_USDT:.2f} USDT`..."
        )

        # ==========================================
        # 🛒 COMPRA DE MERCADO
        # ==========================================
        orden_compra = exchange.create_market_buy_order(
            SYMBOL,
            cantidad_ajustada
        )

        precio_ejecucion = (
            orden_compra.get("average")
            or orden_compra.get("price")
            or precio_actual
        )

        precio_ejecucion = float(precio_ejecucion)

        cantidad_real = (
            orden_compra.get("filled")
            or orden_compra.get("amount")
            or cantidad_ajustada
        )

        cantidad_real = float(cantidad_real)

        # Ajustamos nuevamente para Binance.
        cantidad_real = ajustar_precision_cantidad(
            cantidad_real
        )

        if cantidad_real <= 0:
            raise Exception(
                "Binance no devolvió una cantidad válida."
            )

        # ==========================================
        # 📈 TAKE PROFIT +6%
        # ==========================================
        precio_tp = (
            precio_ejecucion
            * (1 + TAKE_PROFIT_PORCENTAJE)
        )

        precio_tp = float(
            exchange.price_to_precision(
                SYMBOL,
                precio_tp
            )
        )

        # Identificador único para poder reconstruir
        # la operación si Render se reinicia.
        timestamp = int(time.time())

        client_order_id = (
            f"BOT_TP_{nivel}_{timestamp}"
        )

        orden_tp = exchange.create_limit_sell_order(
            SYMBOL,
            cantidad_real,
            precio_tp,
            {
                "newClientOrderId": client_order_id
            }
        )

        # Guardamos el estado.
        niveles_activos[nivel] = True

        ordenes_tp[nivel] = orden_tp["id"]

        enviar_telegram(
            "✅ *COMPRA EJECUTADA*\n"
            f"Nivel: `{nivel + 1}`\n"
            f"Cantidad: `{cantidad_real:.8f} BTC`\n"
            f"Precio compra: `{precio_ejecucion:.2f} USDT`\n"
            f"Inversión: aproximadamente `{MONTO_POR_COMPRA_USDT:.2f} USDT`"
        )

        enviar_telegram(
            "🎯 *TAKE PROFIT COLOCADO*\n"
            f"Nivel: `{nivel + 1}`\n"
            f"Venta: `{precio_tp:.2f} USDT`\n"
            f"Objetivo: `+{TAKE_PROFIT_PORCENTAJE * 100:.0f}%`"
        )

        return True

    except Exception as e:

        print(f"❌ Error ejecutando compra: {e}")

        enviar_telegram(
            f"❌ *Error ejecutando compra*\n"
            f"Nivel: `{nivel + 1}`\n"
            f"Error: `{str(e)}`"
        )

        return False


# ==========================================
# 🤖 LÓGICA PRINCIPAL
# ==========================================
def ejecutar_bot():

    global precio_referencia

    try:

        # ==========================================
        # 1. PRECIO ACTUAL
        # ==========================================
        precio_actual = obtener_precio_actual()

        # ==========================================
        # 2. SINCRONIZAR CON BINANCE
        # ==========================================
        sincronizar_estado()

        # ==========================================
        # 3. ESTABLECER REFERENCIA
        # ==========================================
        if precio_referencia is None:

            precio_referencia = precio_actual

            mensaje = (
                "🚀 *BOT INICIADO*\n"
                f"BTC: `{precio_actual:.2f} USDT`\n"
                f"Precio referencia: `{precio_referencia:.2f}`\n\n"
                "Niveles:\n"
                "1️⃣ -2%\n"
                "2️⃣ -4%\n"
                "3️⃣ -6%\n"
                "4️⃣ -9%\n"
                "5️⃣ -12%\n"
                "6️⃣ -15%\n\n"
                "🎯 Take Profit: +6%"
            )

            print(mensaje.replace("*", ""))

            enviar_telegram(mensaje)

            return

        print(
            f"🔍 BTC: {precio_actual:.2f} | "
            f"Referencia: {precio_referencia:.2f}"
        )

        # ==========================================
        # 4. MOSTRAR NIVELES ACTIVOS
        # ==========================================
        niveles_ocupados = [
            i + 1
            for i, activo in niveles_activos.items()
            if activo
        ]

        print(
            f"📊 Operaciones activas: "
            f"{niveles_ocupados if niveles_ocupados else 'ninguna'}"
        )

        # ==========================================
        # 5. BUSCAR NIVEL DE COMPRA
        # ==========================================
        compra_realizada = False

        for i, caida in enumerate(PORCENTAJES_CAIDA):

            # Este nivel ya tiene una operación abierta.
            if niveles_activos[i]:
                continue

            precio_objetivo = (
                precio_referencia
                * (1 + caida)
            )

            print(
                f"Nivel {i + 1}: "
                f"{precio_objetivo:.2f} USDT"
            )

            if precio_actual <= precio_objetivo:

                compra_realizada = ejecutar_compra(
                    i,
                    precio_actual
                )

                # MUY IMPORTANTE:
                # máximo una compra por revisión.
                if compra_realizada:
                    break

        # ==========================================
        # 6. CICLO DE SUBIDA
        # ==========================================
        #
        # Si no hay ninguna operación abierta y BTC
        # subió al menos 6% desde la referencia,
        # movemos la referencia hacia arriba.
        #
        # Esto permite que el bot siga al mercado
        # durante tendencias alcistas.
        #
        if not any(niveles_activos.values()):

            if precio_actual >= (
                precio_referencia
                * (1 + TAKE_PROFIT_PORCENTAJE)
            ):

                precio_anterior = precio_referencia

                precio_referencia = precio_actual

                enviar_telegram(
                    "🔄 *NUEVO CICLO*\n"
                    f"Referencia anterior: `{precio_anterior:.2f}`\n"
                    f"Nueva referencia: `{precio_referencia:.2f}`"
                )

    except Exception as e:

        print(f"❌ Error general: {e}")

        enviar_telegram(
            f"❌ *ERROR DEL BOT*\n`{str(e)}`"
        )


# ==========================================
# 🔁 BUCLE PRINCIPAL
# ==========================================
def bucle_bot():

    print("🚀 Hilo del bot de trading iniciado.")

    enviar_telegram(
        "🟢 *Bot de Binance iniciado correctamente*"
    )

    while True:

        ejecutar_bot()

        time.sleep(
            INTERVALO_REVISION_SEGUNDOS
        )


# ==========================================
# 🚀 ARRANQUE EN RENDER
# ==========================================
if __name__ == "__main__":

    import uvicorn

    hilo_trading = threading.Thread(
        target=bucle_bot,
        daemon=True
    )

    hilo_trading.start()

    puerto = int(
        os.environ.get("PORT", 10000)
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=puerto
    )
