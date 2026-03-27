# services/sesion_service.py

from django.utils import timezone

from ..models import SesionUnidad, Vehiculo


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