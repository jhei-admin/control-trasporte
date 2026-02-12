import math
from datetime import timedelta
from django.utils import timezone

from .models import (
    RegistroSalida,
    MarcacionPunto,
)

# =================================================
# 📏 DISTANCIA GPS (HAVERSINE)
# =================================================
def distancia_metros(lat1, lon1, lat2, lon2):

    lat1 = float(lat1)
    lon1 = float(lon1)
    lat2 = float(lat2)
    lon2 = float(lon2)

    R = 6371000

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
# 🔥 CIERRE AUTOMÁTICO DE SALIDAS ANTIGUAS
# =================================================
def cerrar_salidas_antiguas(vehiculo=None):

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
# 🧠 OBTENER SALIDA ACTIVA DEL DÍA
# =================================================
def obtener_salida_hoy(vehiculo):

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
# 📍 EVALUAR MARCACIÓN
# =================================================
def evaluar_marcacion(marcacion: MarcacionPunto, tolerancia_min=2):

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

    marcacion.save(update_fields=[
        "diferencia_minutos",
        "estado"
    ])
