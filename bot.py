import time
import os
import json
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

# Espera ante errores 418 / 429
ESPERA_ERROR_BINANCE = 900

# Archivo de estado
ARCHIVO_ESTADO = "bot_state.json"


# ==========================================
# 🔌 CONEXIÓN BINANCE SPOT
# ==========================================
exchange = ccxt.binance({
    "apiKey": BINANCE_API_KEY,
    "secret": BINANCE_SECRET_KEY,
    "enableRateLimit": True,
    "timeout": 30000,
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

niveles_completados = {
    i: False
    for i in range(MAX_OPERACIONES)
}

ultimo_error_binance = 0

bot_en_pausa = False

lock_estado = threading.Lock()


# ==========================================
# 🌐 HEALTH CHECK
# ==========================================
@app.get("/")
def home():

    return {
        "status": "ok",
        "bot": "running",
        "pair": SYMBOL,
        "reference_price": precio_referencia,
        "active_levels": [
            i + 1
            for i, activo in niveles_activos.items()
            if activo
        ],
        "completed_levels": [
            i + 1
            for i, completado in niveles_completados.items()
            if completado
        ],
        "paused": bot_en_pausa
    }


# ==========================================
# 📢 TELEGRAM
# ==========================================
def enviar_telegram(mensaje):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram no está configurado.")
        return False

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

            return False

        return True

    except Exception as e:

        print(
            f"❌ Error enviando Telegram: {e}"
        )

        return False


# ==========================================
# 🛡️ MANEJO DE ERRORES BINANCE
# ==========================================
def manejar_error_binance(error):

    global ultimo_error_binance
    global bot_en_pausa

    mensaje = str(error)

    print(
        f"⚠️ Binance rechazó la solicitud: "
        f"{mensaje}"
    )

    es_limitacion = (
        "418" in mensaje
        or "429" in mensaje
        or "DDoSProtection" in mensaje
        or "Way too much request weight" in mensaje
    )

    if es_limitacion:

        ahora = time.time()

        if (
            ahora - ultimo_error_binance
            > ESPERA_ERROR_BINANCE
        ):

            ultimo_error_binance = ahora
            bot_en_pausa = True

            enviar_telegram(
                "🚨 BINANCE LIMITÓ LAS SOLICITUDES\n\n"
                "El bot entrará temporalmente en pausa "
                "para evitar aumentar el bloqueo.\n\n"
                f"Error: {mensaje[:300]}"
            )

        return True

    return False


# ==========================================
# 💾 GUARDAR ESTADO
# ==========================================
def guardar_estado():

    try:

        estado = {
            "precio_referencia": precio_referencia,
            "niveles_activos": niveles_activos,
            "ordenes_tp": ordenes_tp,
            "niveles_completados": niveles_completados
        }

        with lock_estado:

            archivo_temporal = (
                f"{ARCHIVO_ESTADO}.tmp"
            )

            with open(
                archivo_temporal,
                "w",
                encoding="utf-8"
            ) as archivo:

                json.dump(
                    estado,
                    archivo,
                    indent=2
                )

            os.replace(
                archivo_temporal,
                ARCHIVO_ESTADO
            )

    except Exception as e:

        print(
            f"⚠️ No se pudo guardar estado: {e}"
        )


# ==========================================
# 💾 CARGAR ESTADO
# ==========================================
def cargar_estado():

    global precio_referencia
    global niveles_activos
    global ordenes_tp
    global niveles_completados

    if not os.path.exists(ARCHIVO_ESTADO):

        print(
            "ℹ️ No existe estado anterior."
        )

        return False

    try:

        with open(
            ARCHIVO_ESTADO,
            "r",
            encoding="utf-8"
        ) as archivo:

            estado = json.load(archivo)

        referencia = (
            estado.get(
                "precio_referencia"
            )
        )

        if referencia is not None:

            precio_referencia = float(
                referencia
            )

        niveles_guardados = (
            estado.get(
                "niveles_activos",
                {}
            )
        )

        for i in range(MAX_OPERACIONES):

            niveles_activos[i] = bool(
                niveles_guardados.get(
                    str(i),
                    niveles_guardados.get(
                        i,
                        False
                    )
                )
            )

        ordenes_guardadas = (
            estado.get(
                "ordenes_tp",
                {}
            )
        )

        ordenes_tp.clear()

        for i in range(MAX_OPERACIONES):

            valor = ordenes_guardadas.get(
                str(i),
                ordenes_guardadas.get(i)
            )

            if valor:

                ordenes_tp[i] = valor

        completados_guardados = (
            estado.get(
                "niveles_completados",
                {}
            )
        )

        for i in range(MAX_OPERACIONES):

            niveles_completados[i] = bool(
                completados_guardados.get(
                    str(i),
                    completados_guardados.get(
                        i,
                        False
                    )
                )
            )

        print(
            "✅ Estado anterior recuperado."
        )

        return True

    except Exception as e:

        print(
            f"⚠️ Error cargando estado: {e}"
        )

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

        ticker = exchange.fetch_ticker(
            SYMBOL
        )

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

        return float(
            cantidad_ajustada
        )

    except Exception as e:

        print(
            f"❌ Error ajustando cantidad: {e}"
        )

        return 0.0


# ==========================================
# 💲 PRECISIÓN DE PRECIO
# ==========================================
def ajustar_precision_precio(precio):

    try:

        precio_ajustado = (
            exchange.price_to_precision(
                SYMBOL,
                precio
            )
        )

        return float(
            precio_ajustado
        )

    except Exception as e:

        print(
            f"❌ Error ajustando precio: {e}"
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
# 🔍 BUSCAR ORDEN POR ID
# ==========================================
def obtener_orden_por_id(order_id):

    try:

        return exchange.fetch_order(
            order_id,
            SYMBOL
        )

    except Exception as e:

        manejar_error_binance(e)

        return None


# ==========================================
# 🔄 SINCRONIZAR ESTADO
# ==========================================
def sincronizar_estado():

    global niveles_activos
    global ordenes_tp
    global niveles_completados
    global bot_en_pausa

    try:

        ordenes_abiertas = (
            obtener_ordenes_abiertas()
        )

        ids_abiertos = set()

        nuevos_niveles = {
            i: False
            for i in range(MAX_OPERACIONES)
        }

        nuevas_ordenes = {}

        # ----------------------------------
        # Revisar órdenes TP abiertas
        # ----------------------------------
        for orden in ordenes_abiertas:

            if orden.get("side") != "sell":
                continue

            client_id = (
                orden.get("clientOrderId")
                or orden.get("info", {})
                .get("clientOrderId")
                or ""
            )

            if not client_id.startswith(
                "BOT_TP_"
            ):

                continue

            ids_abiertos.add(
                str(
                    orden.get("id")
                )
            )

            partes = client_id.split("_")

            if len(partes) < 3:
                continue

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

                continue

        # ----------------------------------
        # Detectar TPs ejecutados
        # ----------------------------------
        for nivel, order_id in list(
            ordenes_tp.items()
        ):

            if (
                str(order_id)
                in ids_abiertos
            ):

                continue

            orden = obtener_orden_por_id(
                order_id
            )

            if not orden:
                continue

            estado = orden.get(
                "status"
            )

            if estado == "closed":

                print(
                    f"🎯 TP ejecutado "
                    f"nivel {nivel + 1}"
                )

                niveles_completados[
                    nivel
                ] = True

                enviar_telegram(
                    "💰 TAKE PROFIT EJECUTADO\n"
                    f"Nivel: {nivel + 1}\n"
                    "Objetivo: +6%"
                )

        niveles_activos = nuevos_niveles
        ordenes_tp = nuevas_ordenes

        # ----------------------------------
        # Nuevo ciclo
        # ----------------------------------
        if (
            not any(
                niveles_activos.values()
            )
            and all(
                niveles_completados.values()
            )
        ):

            niveles_completados = {
                i: False
                for i in range(
                    MAX_OPERACIONES
                )
            }

            guardar_estado()

        bot_en_pausa = False

        guardar_estado()

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

        # ----------------------------------
        # Verificar saldo
        # ----------------------------------
        saldo_usdt = (
            obtener_saldo_usdt()
        )

        saldo_disponible = (
            saldo_usdt
            - RESERVA_USDT
        )

        if (
            saldo_disponible
            < MONTO_POR_COMPRA_USDT
        ):

            enviar_telegram(
                "⚠️ COMPRA CANCELADA\n"
                f"Saldo USDT: "
                f"{saldo_usdt:.2f}\n"
                f"Reserva: "
                f"{RESERVA_USDT:.2f}"
            )

            return False

        # ----------------------------------
        # Cantidad BTC
        # ----------------------------------
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
            f"⚠️ NIVEL {nivel + 1} ALCANZADO\n"
            f"Caída: "
            f"{PORCENTAJES_CAIDA[nivel] * 100:.0f}%\n"
            f"Precio: "
            f"{precio_actual:.2f} USDT\n"
            f"Compra: "
            f"{MONTO_POR_COMPRA_USDT:.2f} USDT"
        )

        # ----------------------------------
        # COMPRA MARKET
        # ----------------------------------
        orden_compra = (
            exchange.create_market_buy_order(
                SYMBOL,
                cantidad_ajustada
            )
        )

        # ----------------------------------
        # Precio real de ejecución
        # ----------------------------------
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
                "cantidad ejecutada válida."
            )

        # ----------------------------------
        # TAKE PROFIT +6%
        # ----------------------------------
        precio_tp = (
            precio_ejecucion
            * (
                1
                + TAKE_PROFIT_PORCENTAJE
            )
        )

        precio_tp = (
            ajustar_precision_precio(
                precio_tp
            )
        )

        if precio_tp <= 0:

            raise Exception(
                "Precio TP inválido."
            )

        # ----------------------------------
        # ID ÚNICO
        # ----------------------------------
        timestamp = int(
            time.time()
        )

        client_order_id = (
            f"BOT_TP_{nivel}_{timestamp}"
        )

        # ----------------------------------
        # ORDEN SELL LIMIT
        # ----------------------------------
        try:

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

        except Exception as error_tp:

            print(
                "🚨 COMPRA EJECUTADA PERO "
                "NO SE PUDO COLOCAR TP."
            )

            manejar_error_binance(
                error_tp
            )

            enviar_telegram(
                "🚨 ALERTA CRÍTICA\n\n"
                "La compra fue ejecutada, "
                "pero el Take Profit no pudo colocarse.\n\n"
                f"Nivel: {nivel + 1}\n"
                f"BTC: {cantidad_real:.8f}\n"
                f"Entrada: "
                f"{precio_ejecucion:.2f}"
            )

            return False

        # ----------------------------------
        # Registrar operación
        # ----------------------------------
        niveles_activos[
            nivel
        ] = True

        ordenes_tp[
            nivel
        ] = orden_tp["id"]

        guardar_estado()

        # ----------------------------------
        # Telegram
        # ----------------------------------
        enviar_telegram(
            "✅ COMPRA EJECUTADA\n"
            f"Nivel: {nivel + 1}\n"
            f"Cantidad: "
            f"{cantidad_real:.8f} BTC\n"
            f"Precio: "
            f"{precio_ejecucion:.2f} USDT\n"
            f"Inversión: "
            f"~{MONTO_POR_COMPRA_USDT:.2f} USDT"
        )

        enviar_telegram(
            "🎯 TAKE PROFIT COLOCADO\n"
            f"Nivel: {nivel + 1}\n"
            f"Venta: {precio_tp:.2f} USDT\n"
            "Objetivo: +6%"
        )

        return True

    except Exception as e:

        print(
            f"❌ Error ejecutando compra: {e}"
        )

        manejar_error_binance(e)

        enviar_telegram(
            "❌ ERROR EJECUTANDO COMPRA\n"
            f"Nivel: {nivel + 1}\n"
            f"Error: {str(e)[:300]}"
        )

        return False


# ==========================================
# 🤖 LÓGICA PRINCIPAL
# ==========================================
def ejecutar_bot():

    global precio_referencia
    global bot_en_pausa
    global niveles_completados

    try:

        # ----------------------------------
        # Pausa por limitación Binance
        # ----------------------------------
        if bot_en_pausa:

            ahora = time.time()

            if (
                ahora - ultimo_error_binance
                < ESPERA_ERROR_BINANCE
            ):

                restante = (
                    ESPERA_ERROR_BINANCE
                    - (
                        ahora
                        - ultimo_error_binance
                    )
                )

                print(
                    f"⏸️ Bot en pausa. "
                    f"Faltan {restante:.0f}s."
                )

                return

            bot_en_pausa = False

        # ----------------------------------
        # Precio actual
        # ----------------------------------
        precio_actual = (
            obtener_precio_actual()
        )

        # ----------------------------------
        # Sincronizar órdenes
        # ----------------------------------
        sincronizar_estado()

        # ----------------------------------
        # Referencia inicial
        # ----------------------------------
        if precio_referencia is None:

            precio_referencia = (
                precio_actual
            )

            guardar_estado()

            mensaje = (
                "🚀 BOT INICIADO\n"
                f"BTC: "
                f"{precio_actual:.2f} USDT\n"
                f"Referencia: "
                f"{precio_referencia:.2f}\n\n"
                "Niveles:\n"
                "1️⃣ -2%\n"
                "2️⃣ -4%\n"
                "3️⃣ -6%\n"
                "4️⃣ -9%\n"
                "5️⃣ -12%\n"
                "6️⃣ -15%\n\n"
                "🎯 Take Profit: +6%"
            )

            print(mensaje)

            enviar_telegram(
                mensaje
            )

            return

        # ----------------------------------
        # Mostrar precio
        # ----------------------------------
        print(
            f"🔍 BTC: "
            f"{precio_actual:.2f} | "
            f"Referencia: "
            f"{precio_referencia:.2f}"
        )

        # ----------------------------------
        # Mostrar niveles
        # ----------------------------------
        for i, caida in enumerate(
            PORCENTAJES_CAIDA
        ):

            precio_objetivo = (
                precio_referencia
                * (1 + caida)
            )

            print(
                f"Nivel {i + 1}: "
                f"{precio_objetivo:.2f} USDT"
            )

        # ----------------------------------
        # Niveles ocupados
        # ----------------------------------
        niveles_ocupados = [
            i + 1
            for i, activo
            in niveles_activos.items()
            if activo
        ]

        print(
            "📊 Operaciones activas: "
            f"{niveles_ocupados "
            if niveles_ocupados
            else 'ninguna'}"
        )

        # ----------------------------------
        # Buscar compra
        # ----------------------------------
        compra_realizada = False

        for i, caida in enumerate(
            PORCENTAJES_CAIDA
        ):

            if niveles_activos[i]:
                continue

            if niveles_completados[i]:
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

        # ----------------------------------
        # NUEVO CICLO ALCISTA
        # ----------------------------------
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

                niveles_completados = {
                    i: False
                    for i in range(
                        MAX_OPERACIONES
                    )
                }

                guardar_estado()

                enviar_telegram(
                    "🔄 NUEVO CICLO\n"
                    f"Referencia anterior: "
                    f"{precio_anterior:.2f}\n"
                    f"Nueva referencia: "
                    f"{precio_referencia:.2f}"
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

    cargar_estado()

    enviar_telegram(
        "🟢 Bot de Binance iniciado."
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

    hilo_trading = threading.Thread(
        target=bucle_bot,
        daemon=True
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
