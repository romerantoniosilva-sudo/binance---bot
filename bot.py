import time
import os
import json
import hmac
import hashlib
import threading
import urllib.parse
import uuid
import requests
import ccxt
import websocket

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
BINANCE_SYMBOL = "BTCUSDT"

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

ARCHIVO_ESTADO = "bot_state.json"


# ==========================================
# 🌐 WEBSOCKETS
# ==========================================
MARKET_WS_URL = (
    "wss://stream.binance.com:9443/ws/"
    "btcusdt@trade"
)

USER_WS_URL = (
    "wss://ws-api.binance.com:443/ws-api/v3"
)


# ==========================================
# 🔌 CCXT BINANCE SPOT
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
# 📊 ESTADO
# ==========================================
precio_referencia = None
precio_actual = None

niveles_activos = {
    i: False
    for i in range(MAX_OPERACIONES)
}

niveles_completados = {
    i: False
    for i in range(MAX_OPERACIONES)
}

ordenes_tp = {}

# Precio WebSocket
ultimo_precio_ws = 0

# Estado WebSocket mercado
market_ws_conectado = False

# Estado WebSocket usuario
user_ws_conectado = False

# Evita operaciones simultáneas
operacion_en_curso = False

# Protección ante 418/429
bot_en_pausa = False
ultimo_error_binance = 0

# Control de mensajes Telegram
ultimo_aviso_limitacion = 0

# Locks
lock_estado = threading.Lock()
lock_precio = threading.Lock()
lock_operacion = threading.Lock()


# ==========================================
# 🌐 HEALTH CHECK
# ==========================================
@app.get("/")
def home():

    with lock_precio:
        precio = precio_actual

    return {
        "status": "ok",
        "bot": "running",
        "pair": SYMBOL,
        "price": precio,
        "reference_price": precio_referencia,
        "market_websocket": market_ws_conectado,
        "user_websocket": user_ws_conectado,
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
            f"❌ Error Telegram: {e}"
        )

        return False


# ==========================================
# 🛡️ ERROR BINANCE
# ==========================================
def manejar_error_binance(error):

    global ultimo_error_binance
    global bot_en_pausa
    global ultimo_aviso_limitacion

    mensaje = str(error)

    print(
        f"⚠️ Binance: {mensaje}"
    )

    es_limitacion = (
        "418" in mensaje
        or "429" in mensaje
        or "DDoSProtection" in mensaje
        or "Way too many requests" in mensaje
        or "Way too much request weight" in mensaje
    )

    if not es_limitacion:
        return False

    ahora = time.time()

    ultimo_error_binance = ahora
    bot_en_pausa = True

    # No enviar Telegram repetidamente
    if (
        ahora - ultimo_aviso_limitacion
        > 900
    ):

        ultimo_aviso_limitacion = ahora

        enviar_telegram(
            "🚨 BINANCE LIMITÓ LAS SOLICITUDES\n\n"
            "El bot entrará en espera para "
            "evitar aumentar el bloqueo.\n\n"
            f"Error: {mensaje[:400]}"
        )

    return True


# ==========================================
# 💾 GUARDAR ESTADO
# ==========================================
def guardar_estado():

    try:

        estado = {
            "precio_referencia": precio_referencia,
            "niveles_activos": niveles_activos,
            "niveles_completados": niveles_completados,
            "ordenes_tp": ordenes_tp
        }

        with lock_estado:

            temporal = (
                f"{ARCHIVO_ESTADO}.tmp"
            )

            with open(
                temporal,
                "w",
                encoding="utf-8"
            ) as archivo:

                json.dump(
                    estado,
                    archivo,
                    indent=2
                )

            os.replace(
                temporal,
                ARCHIVO_ESTADO
            )

    except Exception as e:

        print(
            f"⚠️ Error guardando estado: {e}"
        )


# ==========================================
# 💾 CARGAR ESTADO
# ==========================================
def cargar_estado():

    global precio_referencia
    global niveles_activos
    global niveles_completados
    global ordenes_tp

    if not os.path.exists(
        ARCHIVO_ESTADO
    ):

        print(
            "ℹ️ No existe estado anterior."
        )

        return

    try:

        with open(
            ARCHIVO_ESTADO,
            "r",
            encoding="utf-8"
        ) as archivo:

            estado = json.load(
                archivo
            )

        referencia = estado.get(
            "precio_referencia"
        )

        if referencia is not None:

            precio_referencia = float(
                referencia
            )

        guardados = estado.get(
            "niveles_activos",
            {}
        )

        for i in range(
            MAX_OPERACIONES
        ):

            niveles_activos[i] = bool(
                guardados.get(
                    str(i),
                    guardados.get(
                        i,
                        False
                    )
                )
            )

        guardados = estado.get(
            "niveles_completados",
            {}
        )

        for i in range(
            MAX_OPERACIONES
        ):

            niveles_completados[i] = bool(
                guardados.get(
                    str(i),
                    guardados.get(
                        i,
                        False
                    )
                )
            )

        ordenes_guardadas = estado.get(
            "ordenes_tp",
            {}
        )

        ordenes_tp.clear()

        for i in range(
            MAX_OPERACIONES
        ):

            valor = ordenes_guardadas.get(
                str(i),
                ordenes_guardadas.get(
                    i
                )
            )

            if valor:

                ordenes_tp[i] = str(
                    valor
                )

        print(
            "✅ Estado anterior recuperado."
        )

    except Exception as e:

        print(
            f"⚠️ Error cargando estado: {e}"
        )


# ==========================================
# 💰 SALDO USDT
# ==========================================
def obtener_saldo_usdt():

    try:

        balance = exchange.fetch_balance()

        saldo = (
            balance
            .get("free", {})
            .get("USDT", 0)
        )

        return float(saldo)

    except Exception as e:

        manejar_error_binance(e)

        return 0.0


# ==========================================
# 🔢 PRECISIÓN CANTIDAD
# ==========================================
def ajustar_precision_cantidad(
    cantidad
):

    try:

        return float(
            exchange.amount_to_precision(
                SYMBOL,
                cantidad
            )
        )

    except Exception as e:

        print(
            f"❌ Error cantidad: {e}"
        )

        return 0.0


# ==========================================
# 💲 PRECISIÓN PRECIO
# ==========================================
def ajustar_precision_precio(
    precio
):

    try:

        return float(
            exchange.price_to_precision(
                SYMBOL,
                precio
            )
        )

    except Exception as e:

        print(
            f"❌ Error precio: {e}"
        )

        return 0.0


# ==========================================
# 🛒 EJECUTAR COMPRA
# ==========================================
def ejecutar_compra(
    nivel,
    precio
):

    global operacion_en_curso

    with lock_operacion:

        if operacion_en_curso:

            print(
                "⏳ Ya hay una operación en curso."
            )

            return False

        operacion_en_curso = True

    try:

        # ----------------------------------
        # Saldo
        # ----------------------------------
        saldo = obtener_saldo_usdt()

        if saldo <= 0:

            enviar_telegram(
                "⚠️ No se pudo obtener "
                "el saldo USDT."
            )

            return False

        if (
            saldo - RESERVA_USDT
            < MONTO_POR_COMPRA_USDT
        ):

            enviar_telegram(
                "⚠️ COMPRA CANCELADA\n"
                f"Saldo: {saldo:.2f} USDT\n"
                f"Reserva: {RESERVA_USDT:.2f} USDT"
            )

            return False

        # ----------------------------------
        # Cantidad
        # ----------------------------------
        cantidad = (
            MONTO_POR_COMPRA_USDT
            / precio
        )

        cantidad = (
            ajustar_precision_cantidad(
                cantidad
            )
        )

        if cantidad <= 0:

            raise Exception(
                "Cantidad BTC inválida."
            )

        enviar_telegram(
            f"⚠️ NIVEL {nivel + 1} ALCANZADO\n"
            f"Caída: "
            f"{PORCENTAJES_CAIDA[nivel] * 100:.0f}%\n"
            f"Precio: {precio:.2f} USDT\n"
            f"Compra: "
            f"{MONTO_POR_COMPRA_USDT:.2f} USDT"
        )

        # ----------------------------------
        # MARKET BUY
        # ----------------------------------
        orden_compra = (
            exchange.create_market_buy_order(
                SYMBOL,
                cantidad
            )
        )

        precio_ejecucion = (
            orden_compra.get("average")
            or orden_compra.get("price")
            or precio
        )

        precio_ejecucion = float(
            precio_ejecucion
        )

        cantidad_real = (
            orden_compra.get("filled")
            or orden_compra.get("amount")
            or cantidad
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
                "Cantidad ejecutada inválida."
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
        # CLIENT ORDER ID
        # ----------------------------------
        client_order_id = (
            f"BOT_TP_{nivel}_"
            f"{int(time.time())}_"
            f"{uuid.uuid4().hex[:6]}"
        )

        # ----------------------------------
        # SELL LIMIT
        # ----------------------------------
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

        order_id = orden_tp.get(
            "id"
        )

        if not order_id:

            raise Exception(
                "Binance no devolvió "
                "ID del TP."
            )

        # ----------------------------------
        # Registrar
        # ----------------------------------
        niveles_activos[
            nivel
        ] = True

        ordenes_tp[
            nivel
        ] = str(order_id)

        guardar_estado()

        enviar_telegram(
            "✅ COMPRA EJECUTADA\n"
            f"Nivel: {nivel + 1}\n"
            f"BTC: {cantidad_real:.8f}\n"
            f"Entrada: "
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
            f"❌ Error compra: {e}"
        )

        manejar_error_binance(e)

        enviar_telegram(
            "❌ ERROR EJECUTANDO COMPRA\n"
            f"Nivel: {nivel + 1}\n"
            f"{str(e)[:300]}"
        )

        return False

    finally:

        with lock_operacion:

            operacion_en_curso = False


# ==========================================
# 🧠 EVALUAR ESTRATEGIA
# ==========================================
def evaluar_estrategia(
    precio
):

    global precio_referencia
    global precio_actual
    global niveles_completados

    precio_actual = precio

    # --------------------------------------
    # Pausa
    # --------------------------------------
    if bot_en_pausa:

        if (
            time.time()
            - ultimo_error_binance
            < 900
        ):

            return

    # --------------------------------------
    # Referencia inicial
    # --------------------------------------
    if precio_referencia is None:

        precio_referencia = precio

        guardar_estado()

        enviar_telegram(
            "🚀 BOT INICIADO\n"
            f"BTC: {precio:.2f} USDT\n"
            f"Referencia: {precio:.2f}\n\n"
            "Niveles:\n"
            "1️⃣ -2%\n"
            "2️⃣ -4%\n"
            "3️⃣ -6%\n"
            "4️⃣ -9%\n"
            "5️⃣ -12%\n"
            "6️⃣ -15%\n\n"
            "🎯 Take Profit: +6%\n"
            "📡 Precio: WebSocket"
        )

        return

    # --------------------------------------
    # Buscar nivel
    # --------------------------------------
    for i, caida in enumerate(
        PORCENTAJES_CAIDA
    ):

        if niveles_activos[i]:
            continue

        if niveles_completados[i]:
            continue

        objetivo = (
            precio_referencia
            * (1 + caida)
        )

        if precio <= objetivo:

            print(
                f"🚨 NIVEL {i + 1} "
                f"ALCANZADO\n"
                f"Precio: {precio:.2f}\n"
                f"Objetivo: {objetivo:.2f}"
            )

            ejecutada = (
                ejecutar_compra(
                    i,
                    precio
                )
            )

            if ejecutada:

                break

    # --------------------------------------
    # Nuevo ciclo
    # --------------------------------------
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

        if precio >= precio_nuevo_ciclo:

            anterior = (
                precio_referencia
            )

            precio_referencia = precio

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
                f"{anterior:.2f}\n"
                f"Nueva referencia: "
                f"{precio:.2f}"
            )


# ==========================================
# 📡 MARKET WEBSOCKET
# ==========================================
def market_on_message(
    ws,
    message
):

    global ultimo_precio_ws

    try:

        data = json.loads(
            message
        )

        if data.get("e") != "trade":
            return

        precio = data.get("p")

        if precio is None:
            return

        precio = float(
            precio
        )

        with lock_precio:

            ultimo_precio_ws = (
                time.time()
            )

        evaluar_estrategia(
            precio
        )

    except Exception as e:

        print(
            f"❌ Error market WS: {e}"
        )


def market_on_open(ws):

    global market_ws_conectado

    market_ws_conectado = True

    print(
        "📡 WebSocket de mercado conectado."
    )

    enviar_telegram(
        "📡 WebSocket BTC conectado.\n"
        "Precio en tiempo real."
    )


def market_on_error(
    ws,
    error
):

    print(
        f"❌ Market WS: {error}"
    )


def market_on_close(
    ws,
    code,
    message
):

    global market_ws_conectado

    market_ws_conectado = False

    print(
        "⚠️ Market WebSocket cerrado."
    )

    print(
        f"Código: {code} | "
        f"{message}"
    )


def ejecutar_market_websocket():

    while True:

        try:

            socket = (
                websocket.WebSocketApp(
                    MARKET_WS_URL,
                    on_open=market_on_open,
                    on_message=market_on_message,
                    on_error=market_on_error,
                    on_close=market_on_close
                )
            )

            socket.run_forever(
                ping_interval=20,
                ping_timeout=10
            )

        except Exception as e:

            print(
                f"❌ Market WS error: {e}"
            )

        time.sleep(5)


# ==========================================
# 🔐 FIRMA HMAC
# ==========================================
def generar_firma(
    params
):

    payload = (
        urllib.parse.urlencode(
            params
        )
    )

    firma = hmac.new(
        BINANCE_SECRET_KEY.encode(
            "utf-8"
        ),
        payload.encode(
            "utf-8"
        ),
        hashlib.sha256
    ).hexdigest()

    return firma


# ==========================================
# 👤 USER DATA WEBSOCKET
# ==========================================
def usuario_ws_message(
    ws,
    message
):

    global user_ws_conectado

    try:

        data = json.loads(
            message
        )

        # ----------------------------------
        # Respuesta de suscripción
        # ----------------------------------
        if (
            "status" in data
            and "result" in data
        ):

            if data.get("status") == 200:

                print(
                    "✅ User Data Stream "
                    "suscrito."
                )

            return

        # ----------------------------------
        # Evento de cuenta
        # ----------------------------------
        evento = data.get(
            "event",
            {}
        )

        event_type = evento.get(
            "e"
        )

        if event_type == "executionReport":

            procesar_execution_report(
                evento
            )

    except Exception as e:

        print(
            f"❌ Error User WS: {e}"
        )


def procesar_execution_report(
    evento
):

    global niveles_activos
    global niveles_completados

    symbol = evento.get(
        "s"
    )

    if symbol != BINANCE_SYMBOL:
        return

    order_status = evento.get(
        "X"
    )

    side = evento.get(
        "S"
    )

    client_id = evento.get(
        "c",
        ""
    )

    order_id = evento.get(
        "i"
    )

    # --------------------------------------
    # TP SELL ejecutado
    # --------------------------------------
    if (
        side == "SELL"
        and client_id.startswith(
            "BOT_TP_"
        )
        and order_status == "FILLED"
    ):

        partes = client_id.split(
            "_"
        )

        if len(partes) >= 3:

            try:

                nivel = int(
                    partes[2]
                )

                if (
                    0 <= nivel
                    < MAX_OPERACIONES
                ):

                    niveles_activos[
                        nivel
                    ] = False

                    niveles_completados[
                        nivel
                    ] = True

                    ordenes_tp.pop(
                        nivel,
                        None
                    )

                    guardar_estado()

                    enviar_telegram(
                        "💰 TAKE PROFIT EJECUTADO\n"
                        f"Nivel: {nivel + 1}\n"
                        f"Orden: {order_id}\n"
                        "Objetivo: +6%"
                    )

                    print(
                        f"🎯 TP ejecutado "
                        f"nivel {nivel + 1}"
                    )

            except ValueError:

                pass


# ==========================================
# USER WS OPEN
# ==========================================
def usuario_ws_open(
    ws
):

    global user_ws_conectado

    user_ws_conectado = True

    print(
        "🔐 User WebSocket conectado."
    )

    # --------------------------------------
    # Parámetros firmados
    # --------------------------------------
    params = {
        "apiKey": BINANCE_API_KEY,
        "timestamp": int(
            time.time() * 1000
        ),
        "recvWindow": 5000
    }

    firma = generar_firma(
        params
    )

    params[
        "signature"
    ] = firma

    request = {
        "id": str(
            uuid.uuid4()
        ),
        "method":
            "userDataStream.subscribe.signature",
        "params": params
    }

    ws.send(
        json.dumps(
            request
        )
    )

    print(
        "🔐 Suscripción User Data "
        "enviada."
    )


def usuario_ws_error(
    ws,
    error
):

    print(
        f"❌ User WS error: {error}"
    )


def usuario_ws_close(
    ws,
    code,
    message
):

    global user_ws_conectado

    user_ws_conectado = False

    print(
        "⚠️ User WebSocket cerrado."
    )

    print(
        f"Código: {code} | "
        f"{message}"
    )


# ==========================================
# 🔁 EJECUTAR USER WEBSOCKET
# ==========================================
def ejecutar_user_websocket():

    while True:

        try:

            socket = (
                websocket.WebSocketApp(
                    USER_WS_URL,
                    on_open=usuario_ws_open,
                    on_message=usuario_ws_message,
                    on_error=usuario_ws_error,
                    on_close=usuario_ws_close
                )
            )

            socket.run_forever(
                ping_interval=20,
                ping_timeout=10
            )

        except Exception as e:

            print(
                f"❌ User WS exception: {e}"
            )

        time.sleep(10)


# ==========================================
# 📊 ESTADO CONSOLA
# ==========================================
def monitor_estado():

    while True:

        try:

            with lock_precio:

                precio = precio_actual

            niveles = [
                i + 1
                for i, activo
                in niveles_activos.items()
                if activo
            ]

            print(
                "━━━━━━━━━━━━━━━━━━━━"
            )

            print(
                f"₿ BTC: "
                f"{precio if precio else 0:.2f}"
            )

            print(
                f"📌 Referencia: "
                f"{precio_referencia if precio_referencia else 0:.2f}"
            )

            print(
                "📊 Operaciones activas: "
                f"{niveles if niveles else 'ninguna'}"
            )

            print(
                f"📡 Market WS: "
                f"{market_ws_conectado}"
            )

            print(
                f"🔐 User WS: "
                f"{user_ws_conectado}"
            )

            print(
                "━━━━━━━━━━━━━━━━━━━━"
            )

        except Exception as e:

            print(
                f"⚠️ Monitor: {e}"
            )

        time.sleep(60)


# ==========================================
# 🚀 ARRANQUE
# ==========================================
if __name__ == "__main__":

    print(
        "🚀 INICIANDO BOT BITCOIN V2"
    )

    # --------------------------------------
    # Validar variables
    # --------------------------------------
    if not BINANCE_API_KEY:
        print(
            "❌ Falta BINANCE_API_KEY"
        )

    if not BINANCE_SECRET_KEY:
        print(
            "❌ Falta BINANCE_SECRET_KEY"
        )

    # --------------------------------------
    # Cargar mercados
    # --------------------------------------
    try:

        exchange.load_markets()

        print(
            "✅ Mercados Binance cargados."
        )

    except Exception as e:

        print(
            f"⚠️ No se pudieron cargar "
            f"los mercados: {e}"
        )

    # --------------------------------------
    # Cargar estado
    # --------------------------------------
    cargar_estado()

    # --------------------------------------
    # Market WebSocket
    # --------------------------------------
    hilo_market = threading.Thread(
        target=ejecutar_market_websocket,
        daemon=True
    )

    hilo_market.start()

    # --------------------------------------
    # User WebSocket
    # --------------------------------------
    hilo_user = threading.Thread(
        target=ejecutar_user_websocket,
        daemon=True
    )

    hilo_user.start()

    # --------------------------------------
    # Monitor
    # --------------------------------------
    hilo_monitor = threading.Thread(
        target=monitor_estado,
        daemon=True
    )

    hilo_monitor.start()

    # --------------------------------------
    # Telegram
    # --------------------------------------
    enviar_telegram(
        "🟢 BOT BITCOIN V2 INICIADO\n\n"
        "📡 Market WebSocket: activo\n"
        "🔐 User Data WebSocket: activo\n"
        "💵 Compras: 6 × 10 USDT\n"
        "🎯 Take Profit: +6%"
    )

    # --------------------------------------
    # FastAPI / Render
    # --------------------------------------
    import uvicorn

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
