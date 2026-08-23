import time
import os
import threading
import requests
import ccxt
from fastapi import FastAPI


# ==========================================
# 🌐 SERVIDOR WEB PARA RENDER
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
# ⚙️ CONFIGURACIÓN DEL BOT
# ==========================================
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"

MONTO_POR_COMPRA_USDT = 10.0
MAX_OPERACIONES = 6
RESERVA_USDT = 4.0

PORCENTAJES_CAIDA = [
    -0.02,
    -0.04,
    -0.06,
    -0.09,
    -0.12,
    -0.15
]

TAKE_PROFIT_PORCENTAJE = 0.06

# Revisar cada 5 minutos
INTERVALO_REVISION_SEGUNDOS = 300

# Tiempo de espera ante errores 418/429
ESPERA_ERROR_BINANCE = 900


# ==========================================
# 🔌 CONEXIÓN BINANCE SPOT
# ==========================================
exchange = ccxt.binance({
    "apiKey": BINANCE_API_KEY,
    "secret": BINANCE_SECRET_KEY,
    "enableRateLimit": True,
    "options": {
        "defaultType": "spot"
    }
})


# ==========================================
# 📊 ESTADO DEL BOT
# ==========================================
precio_referencia = None

niveles_activos = {
    i: False
    for i in range(MAX_OPERACIONES)
}

ordenes_tp = {}

ultimo_error_binance = 0


# ==========================================
# 🌐 HEALTH CHECK
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

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

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

            print(
                f"❌ Error Telegram: "
                f"{respuesta.text}"
            )

    except Exception as e:

        print(
            f"❌ Error enviando Telegram: {e}"
        )


# ==========================================
# 🛡️ MANEJO DE ERRORES BINANCE
# ==========================================
def manejar_error_binance(error):

    global ultimo_error_binance

    mensaje = str(error)

    print(
        f"⚠️ Binance rechazó la solicitud: "
        f"{mensaje}"
    )

    if (
        "418" in mensaje
        or "429" in mensaje
        or "DDoSProtection" in mensaje
    ):

        ahora = time.time()

        if (
            ahora - ultimo_error_binance
            > ESPERA_ERROR_BINANCE
        ):

            ultimo_error_binance = ahora

            enviar_telegram(
                "🚨 *BINANCE LIMITÓ LAS SOLICITUDES*\n\n"
                "El bot entrará en espera para "
                "evitar aumentar el bloqueo.\n\n"
                f"Error: `{mensaje[:300]}`"
            )

        return True

    return False


# ==========================================
# 💰 SALDO USDT
# ==========================================
def obtener_saldo_usdt():

    try:

        balance = exchange.fetch_balance()

        saldo_libre = (
            balance
            .get("free", {})
            .get("USDT", 0)
        )

        return float(saldo_libre)

    except Exception as e:

        manejar_error_binance(e)
        raise


# ==========================================
# ₿ PRECIO BTC
# ==========================================
def obtener_precio_actual():

    try:

        ticker = exchange.fetch_ticker(SYMBOL)

        precio = ticker.get("last")

        if precio is None:

            raise Exception(
                "Binance no devolvió precio."
            )

        return float(precio)

    except Exception as e:

        manejar_error_binance(e)
        raise


# ==========================================
# 🔢 PRECISIÓN DE CANTIDAD
# ==========================================
def ajustar_precision_cantidad(cantidad):

    try:

        cantidad_ajustada = (
            exchange.amount_to_precision(
                SYMBOL,
                cantidad
            )
        )

        return float(cantidad_ajustada)

    except Exception as e:

        print(
            f"❌ Error ajustando cantidad: {e}"
        )

        return 0.0


# ==========================================
# 📋 ÓRDENES ABIERTAS
# ==========================================
def obtener_ordenes_abiertas():

    try:

        return exchange.fetch_open_orders(
            SYMBOL
        )

    except Exception as e:

        manejar_error_binance(e)

        return []


# ==========================================
# 🔄 SINCRONIZAR ESTADO
# ==========================================
def sincronizar_estado():

    global niveles_activos
    global ordenes_tp

    try:

        ordenes_abiertas = (
            obtener_ordenes_abiertas()
        )

        nuevos_niveles = {
            i: False
            for i in range(MAX_OPERACIONES)
        }

        nuevas_ordenes = {}

        for orden in ordenes_abiertas:

            if orden.get("side") != "sell":
                continue

            client_id = (
                orden.get("clientOrderId")
                or orden.get("info", {})
                .get("clientOrderId")
                or ""
            )

            if client_id.startswith("BOT_TP_"):

                partes = client_id.split("_")

                if len(partes) >= 3:

                    try:

                        nivel = int(
                            partes[2]
                        )

                        if (
                            0 <= nivel
                            < MAX_OPERACIONES
                        ):

                            nuevos_niveles[
                                nivel
                            ] = True

                            nuevas_ordenes[
                                nivel
                            ] = orden["id"]

                    except ValueError:

                        pass

        niveles_activos = nuevos_niveles
        ordenes_tp = nuevas_ordenes

    except Exception as e:

        print(
            f"❌ Error sincronizando estado: "
            f"{e}"
        )


# ==========================================
# 🛒 EJECUTAR COMPRA
# ==========================================
def ejecutar_compra(
    nivel,
    precio_actual
):

    global niveles_activos
    global ordenes_tp

    try:

        # ------------------------------
        # Verificar saldo
        # ------------------------------
        saldo_usdt = obtener_saldo_usdt()

        saldo_disponible = (
            saldo_usdt
            - RESERVA_USDT
        )

        if (
            saldo_disponible
            < MONTO_POR_COMPRA_USDT
        ):

            enviar_telegram(
                "⚠️ *Compra cancelada*\n"
                f"Saldo USDT: "
                f"`{saldo_usdt:.2f}`\n"
                f"Reserva: "
                f"`{RESERVA_USDT:.2f}`"
            )

            return False

        # ------------------------------
        # Cantidad BTC
        # ------------------------------
        cantidad_btc = (
            MONTO_POR_COMPRA_USDT
            / precio_actual
        )

        cantidad_ajustada = (
            ajustar_precision_cantidad(
                cantidad_btc
            )
        )

        if cantidad_ajustada <= 0:

            print(
                "❌ Cantidad BTC inválida."
            )

            return False

        enviar_telegram(
            f"⚠️ *Nivel {nivel + 1} alcanzado*\n"
            f"Caída: "
            f"`{PORCENTAJES_CAIDA[nivel] * 100:.0f}%`\n"
            f"Precio: "
            f"`{precio_actual:.2f} USDT`\n"
            f"💰 Compra: "
            f"`{MONTO_POR_COMPRA_USDT:.2f} USDT`"
        )

        # ------------------------------
        # COMPRA MARKET
        # ------------------------------
        orden_compra = (
            exchange.create_market_buy_order(
                SYMBOL,
                cantidad_ajustada
            )
        )

        precio_ejecucion = (
            orden_compra.get("average")
            or orden_compra.get("price")
            or precio_actual
        )

        precio_ejecucion = float(
            precio_ejecucion
        )

        cantidad_real = (
            orden_compra.get("filled")
            or orden_compra.get("amount")
            or cantidad_ajustada
        )

        cantidad_real = float(
            cantidad_real
        )

        cantidad_real = (
            ajustar_precision_cantidad(
                cantidad_real
            )
        )

        if cantidad_real <= 0:

            raise Exception(
                "Binance no devolvió "
                "cantidad válida."
            )

        # ------------------------------
        # TAKE PROFIT +6%
        # ------------------------------
        precio_tp = (
            precio_ejecucion
            * (
                1
                + TAKE_PROFIT_PORCENTAJE
            )
        )

        precio_tp = float(
            exchange.price_to_precision(
                SYMBOL,
                precio_tp
            )
        )

        timestamp = int(
            time.time()
        )

        client_order_id = (
            f"BOT_TP_{nivel}_{timestamp}"
        )

        # ------------------------------
        # ORDEN LIMIT SELL
        # ------------------------------
        orden_tp = (
            exchange.create_limit_sell_order(
                SYMBOL,
                cantidad_real,
                precio_tp,
                {
                    "newClientOrderId":
                    client_order_id
                }
            )
        )

        niveles_activos[
            nivel
        ] = True

        ordenes_tp[
            nivel
        ] = orden_tp["id"]

        enviar_telegram(
            "✅ *COMPRA EJECUTADA*\n"
            f"Nivel: `{nivel + 1}`\n"
            f"Cantidad: "
            f"`{cantidad_real:.8f} BTC`\n"
            f"Precio: "
            f"`{precio_ejecucion:.2f} USDT`\n"
            f"Inversión: "
            f"`~{MONTO_POR_COMPRA_USDT:.2f} USDT`"
        )

        enviar_telegram(
            "🎯 *TAKE PROFIT COLOCADO*\n"
            f"Nivel: `{nivel + 1}`\n"
            f"Venta: `{precio_tp:.2f} USDT`\n"
            f"Objetivo: `+6%`"
        )

        return True

    except Exception as e:

        print(
            f"❌ Error ejecutando compra: {e}"
        )

        manejar_error_binance(e)

        enviar_telegram(
            "❌ *Error ejecutando compra*\n"
            f"Nivel: `{nivel + 1}`\n"
            f"Error: `{str(e)[:300]}`"
        )

        return False


# ==========================================
# 🤖 LÓGICA PRINCIPAL
# ==========================================
def ejecutar_bot():

    global precio_referencia

    try:

        # ------------------------------
        # 1. Precio
        # ------------------------------
        precio_actual = (
            obtener_precio_actual()
        )

        # ------------------------------
        # 2. Sincronizar órdenes
        # ------------------------------
        sincronizar_estado()

        # ------------------------------
        # 3. Referencia inicial
        # ------------------------------
        if precio_referencia is None:

            precio_referencia = (
                precio_actual
            )

            mensaje = (
                "🚀 *BOT INICIADO*\n"
                f"BTC: "
                f"`{precio_actual:.2f} USDT`\n"
                f"Referencia: "
                f"`{precio_referencia:.2f}`\n\n"
                "Niveles:\n"
                "1️⃣ -2%\n"
                "2️⃣ -4%\n"
                "3️⃣ -6%\n"
                "4️⃣ -9%\n"
                "5️⃣ -12%\n"
                "6️⃣ -15%\n\n"
                "🎯 Take Profit: +6%"
            )

            print(
                mensaje.replace(
                    "*",
                    ""
                )
            )

            enviar_telegram(
                mensaje
            )

            return

        # ------------------------------
        # Mostrar precio actual
        # ------------------------------
        print(
            f"🔍 BTC: "
            f"{precio_actual:.2f} | "
            f"Referencia: "
            f"{precio_referencia:.2f}"
        )

        # ------------------------------
        # 4. Mostrar niveles
        # ------------------------------
        for i, caida in enumerate(
            PORCENTAJES_CAIDA
        ):

            precio_objetivo = (
                precio_referencia
                * (1 + caida)
            )

            print(
                f"Nivel {i + 1}: "
                f"{precio_objetivo:.2f} "
                f"USDT"
            )

        # ------------------------------
        # 5. Niveles ocupados
        # ------------------------------
        niveles_ocupados = [
            i + 1
            for i, activo in niveles_activos.items()
            if activo
        ]

        print(
            "📊 Operaciones activas: "
            f"{niveles_ocupados if niveles_ocupados else 'ninguna'}"
        )

        # ------------------------------
        # 6. Buscar compra
        # ------------------------------
        compra_realizada = False

        for i, caida in enumerate(
            PORCENTAJES_CAIDA
        ):

            if niveles_activos[i]:
                continue

            precio_objetivo = (
                precio_referencia
                * (1 + caida)
            )

            if (
                precio_actual
                <= precio_objetivo
            ):

                compra_realizada = (
                    ejecutar_compra(
                        i,
                        precio_actual
                    )
                )

                # Máximo una compra
                # por revisión.
                if compra_realizada:
                    break

        # ------------------------------
        # 7. Nuevo ciclo alcista
        # ------------------------------
        if not any(
            niveles_activos.values()
        ):

            precio_nuevo_ciclo = (
                precio_referencia
                * (
                    1
                    + TAKE_PROFIT_PORCENTAJE
                )
            )

            if (
                precio_actual
                >= precio_nuevo_ciclo
            ):

                precio_anterior = (
                    precio_referencia
                )

                precio_referencia = (
                    precio_actual
                )

                enviar_telegram(
                    "🔄 *NUEVO CICLO*\n"
                    f"Referencia anterior: "
                    f"`{precio_anterior:.2f}`\n"
                    f"Nueva referencia: "
                    f"`{precio_referencia:.2f}`"
                )

    except Exception as e:

        print(
            f"❌ Error general: {e}"
        )

        manejar_error_binance(e)

# ==========================================
# 🔁 BUCLE PRINCIPAL
# ==========================================
def bucle_bot():

    print(
        "🚀 Hilo del bot iniciado."
    )

    enviar_telegram(
        "🟢 *Bot de Binance iniciado*"
    )

    while True:

        inicio = time.time()

        ejecutar_bot()

        duracion = (
            time.time() - inicio
        )

        espera = max(
            0,
            INTERVALO_REVISION_SEGUNDOS
            - duracion
        )

        print(
            f"⏳ Próxima revisión en "
            f"{espera:.0f} segundos."
        )

        time.sleep(
            espera
        )


# ==========================================
# 🚀 ARRANQUE RENDER
# ==========================================
if __name__ == "__main__":

    import uvicorn

    hilo_trading = (
        threading.Thread(
            target=bucle_bot,
            daemon=True
        )
    )

    hilo_trading.start()

    puerto = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=puerto
    )
