import math
from datetime import timedelta
from django.utils import timezone

from .models import (
    RegistroSalida,
    ConfiguracionDespacho,
    MarcacionPunto,
    SesionUnidad,
    GPSRegistro,
)

# =================================================
# 📏 DISTANCIA GPS (HAVERSINE)
# =================================================
def distancia_metros(lat1, lon1, lat2, lon2):
    """
    Distancia en metros entre dos coordenadas GPS.
    Soporta Decimal y float (fix para app móvil).
    """

    lat1 = float(lat1)
    lon1 = float(lon1)
    lat2 = float(lat2)
    lon2 = float(lon2)

    R = 6371000  # Radio de la Tierra (m)

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2 +
        math.cos(phi1) * math.cos(phi2) *
        math.sin(dlambda / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


# =================================================
# 🔥 CIERRE AUTOMÁTICO DE SALIDAS ANTIGUAS (PASO 2)
# =================================================
def cerrar_salidas_antiguas(vehiculo=None):
    """
    Cierra automáticamente salidas ACTIVAS
    de días anteriores al actual.

    🔑 Regla de campo:
    - Ninguna salida puede quedar activa si no es de HOY
    """

    hoy = timezone.localdate()

    qs = RegistroSalida.objects.filter(
        activo=True,
        fecha__lt=hoy
    )

    if vehiculo:
        qs = qs.filter(vehiculo=vehiculo)

    qs.update(
        activo=False,
        en_cola=False
    )


# =================================================
# 🧠 OBTENER SALIDA ACTIVA DEL DÍA (UTILIDAD CENTRAL)
# =================================================
def obtener_salida_hoy(vehiculo):
    """
    Devuelve la salida ACTIVA del día actual
    para un vehículo específico.
    """

    hoy = timezone.localdate()

    return (
        RegistroSalida.objects
        .filter(
            vehiculo=vehiculo,
            fecha=hoy,
            activo=True
        )
        .order_by("-id")
        .first()
    )


# =================================================
# 🧠 EVALUAR CUMPLIMIENTO DE SALIDA (UNIDAD)
# =================================================
def evaluar_salida(hora_programada, hora_real, tolerancia_min=10):
    """
    Compara hora programada vs hora real de SALIDA.

    Regla campo real:
    - ± tolerancia_min minutos → a_tiempo
    """

    if not hora_programada or not hora_real:
        return None

    diferencia = (hora_real - hora_programada).total_seconds() / 60

    if -tolerancia_min <= diferencia <= tolerancia_min:
        return "a_tiempo"
    elif diferencia > tolerancia_min:
        return "tarde"
    else:
        return "adelantado"


# =================================================
# 📍 EVALUAR MARCACIÓN DE PUNTO (GPS / MANUAL)
# =================================================
def evaluar_marcacion(marcacion: MarcacionPunto, tolerancia_min=2):
    """
    Calcula:
    - diferencia_minutos
    - estado (adelantado / a_tiempo / tarde)
    """

    if not marcacion.hora_programada or not marcacion.hora_marcada:
        return

    diferencia = marcacion.hora_marcada - marcacion.hora_programada
    minutos = int(diferencia.total_seconds() / 60)

    marcacion.diferencia_minutos = minutos

    if minutos < -tolerancia_min:
        marcacion.estado = "adelantado"
    elif minutos > tolerancia_min:
        marcacion.estado = "tarde"
    else:
        marcacion.estado = "a_tiempo"

    marcacion.save(
        update_fields=["diferencia_minutos", "estado"]
    )


# =================================================
# 🚫 MARCACIÓN AUTOMÁTICA POR TIEMPO (DESACTIVADA)
# =================================================
def marcar_salidas_automaticas():
    """
    ⚠️ DESACTIVADO INTENCIONALMENTE
    El sistema SOLO marca por:
    - GPS real
    - Acción del despachador
    """
    return


# =================================================
# 🔐 VALIDACIÓN CENTRAL DE SESIÓN
# =================================================
def validar_sesion(token):
    """
    Valida una sesión activa por VEHÍCULO.
    """

    if not token:
        return None

    sesion = (
        SesionUnidad.objects
        .filter(token=token, activa=True)
        .select_related("vehiculo")
        .first()
    )

    if not sesion:
        return None

    if not sesion.esta_valida():
        return None

    return sesion


# =================================================
# 🧠 ESTADO INTELIGENTE DEL VEHÍCULO (HEARTBEAT + GPS)
# =================================================
HEARTBEAT_TIMEOUT = timedelta(seconds=90)  # ⏱️ 1 min 30 s


def calcular_estado_sesion(sesion: SesionUnidad):
    """
    Determina el estado REAL del vehículo usando:
    - Sesión
    - Heartbeat
    - Último GPS

    Estados posibles:
    - BLOQUEADO
    - SIN_GPS
    - SIN_SENAL
    - EN_RUTA
    - DETENIDO
    """

    # 🔒 Sesión inválida
    if not sesion or not sesion.activa:
        return "BLOQUEADO"

    ahora = timezone.now()

    # =================================================
    # 🫀 HEARTBEAT (PRIORIDAD ALTA)
    # =================================================
    if sesion.last_heartbeat:
        if ahora - sesion.last_heartbeat > HEARTBEAT_TIMEOUT:
            return "SIN_SENAL"

    # =================================================
    # 📍 ÚLTIMO GPS
    # =================================================
    ultimo_gps = (
        GPSRegistro.objects
        .filter(sesion=sesion)
        .order_by("-timestamp")
        .first()
    )

    if not ultimo_gps:
        return "SIN_GPS"

    diferencia = ahora - ultimo_gps.timestamp

    # GPS demasiado viejo
    if diferencia > timedelta(minutes=3):
        return "SIN_SENAL"

    # Estado por velocidad
    if ultimo_gps.velocidad is not None:
        if ultimo_gps.velocidad > 5:
            return "EN_RUTA"
        else:
            return "DETENIDO"

    # Fallback seguro
    return "DETENIDO"
