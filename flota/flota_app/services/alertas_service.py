from datetime import timedelta
from django.utils import timezone

from ..models import UbicacionVehiculo, RegistroSalida


def generar_alertas(empresa):

    ahora = timezone.now()
    hoy = timezone.localdate()

    alertas = []

    ubicaciones = UbicacionVehiculo.objects.filter(
        vehiculo__empresa=empresa
    ).select_related("vehiculo")

    for ub in ubicaciones:

        delta = ahora - ub.updated_at

        # 🚨 GPS OFFLINE
        if delta > timedelta(minutes=2):
            alertas.append({
                "tipo": "GPS_OFFLINE",
                "unidad": ub.vehiculo.codigo,
                "detalle": "Sin señal GPS"
            })

        # 🚨 GPS LENTO
        elif delta > timedelta(seconds=30):
            alertas.append({
                "tipo": "GPS_LENTO",
                "unidad": ub.vehiculo.codigo,
                "detalle": "GPS lento"
            })

    # 🚨 UNIDADES SIN SALIDA
    salidas = RegistroSalida.objects.filter(
        empresa=empresa,
        fecha=hoy,
        activo=True
    )

    if not salidas.exists():
        alertas.append({
            "tipo": "SIN_OPERACION",
            "detalle": "No hay unidades operando hoy"
        })

    return alertas