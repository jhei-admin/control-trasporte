# flota_app/services/sesion_service.py

from django.utils import timezone
from ..models import SesionUnidad


def validar_sesion(token):
    try:
        sesion = SesionUnidad.objects.select_related("vehiculo").get(
            token=token,
            activa=True
        )
        return sesion
    except SesionUnidad.DoesNotExist:
        return None


def calcular_estado_sesion(sesion):
    ahora = timezone.now()

    if not sesion.last_heartbeat:
        return "OFFLINE"

    delta = ahora - sesion.last_heartbeat

    if delta.total_seconds() <= 30:
        return "ONLINE"
    elif delta.total_seconds() <= 120:
        return "LENTO"
    else:
        return "OFFLINE"