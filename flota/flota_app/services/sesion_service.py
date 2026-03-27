# services/sesion_service.py

from django.utils import timezone
from datetime import timedelta

HEARTBEAT_TIMEOUT = timedelta(seconds=90)
GPS_TIMEOUT = timedelta(minutes=3)

from ..models import SesionUnidad, GPSRegistro



# =================================================
# 🔐 CREAR SESIÓN (QR)
# =================================================
def crear_sesion_qr(vehiculo):

    # cerrar anteriores
    SesionUnidad.objects.filter(
        vehiculo=vehiculo,
        activa=True
    ).update(activa=False)

    sesion = SesionUnidad.objects.create(
        vehiculo=vehiculo,
        activa=True
    )

    return sesion


# =================================================
# 🔍 VALIDAR SESIÓN
# =================================================
def obtener_sesion_valida(token):

    try:
        sesion = SesionUnidad.objects.select_related("vehiculo").get(
            token=token,
            activa=True
        )
        return sesion
    except SesionUnidad.DoesNotExist:
        return None


# =================================================
# 🫀 HEARTBEAT
# =================================================
def actualizar_heartbeat(sesion):
    sesion.last_heartbeat = timezone.now()
    sesion.save(update_fields=["last_heartbeat"])
    return sesion


# =================================================
# 🚍 OBTENER VEHÍCULO DESDE TOKEN
# =================================================
def obtener_vehiculo_desde_token(token):
    sesion = obtener_sesion_valida(token)
    return sesion.vehiculo if sesion else None

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

    if not sesion or not sesion.activa:
        return "BLOQUEADO"

    ahora = timezone.now()

    # =============================================
    # 🫀 HEARTBEAT
    # =============================================
    if not sesion.last_heartbeat:
        return "SIN_SENAL"

    if ahora - sesion.last_heartbeat > HEARTBEAT_TIMEOUT:
        return "SIN_SENAL"

    # =============================================
    # 📍 ÚLTIMO GPS
    # =============================================
    ultimo_gps = (
        GPSRegistro.objects
        .filter(sesion=sesion)
        .order_by("-timestamp")
        .first()
    )

    if not ultimo_gps:
        return "SIN_GPS"

    if ahora - ultimo_gps.timestamp > GPS_TIMEOUT:
        return "SIN_SENAL"

    # =============================================
    # 🚍 ESTADO POR VELOCIDAD
    # =============================================
    if ultimo_gps.velocidad and ultimo_gps.velocidad > 5:
        return "EN_RUTA"

    return "DETENIDO"