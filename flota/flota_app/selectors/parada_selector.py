from ..models import Parada


# =================================================
# 🛑 OBTENER PARADA ACTIVA
# =================================================
def obtener_parada_activa(vehiculo):
    return (
        Parada.objects.for_empresa(vehiculo.empresa)
        .filter(
            vehiculo=vehiculo,
            activa=True
        )
        .order_by("-inicio")
        .first()
    )


# =================================================
# 🛑 CREAR PARADA
# =================================================
def crear_parada(vehiculo, lat, lng, timestamp):
    return Parada.objects.create(
        vehiculo=vehiculo,
        lat=lat,
        lng=lng,
        inicio=timestamp
    )


# =================================================
# 🛑 PARADAS POR VEHÍCULO Y FECHA
# =================================================
def obtener_paradas_por_fecha(empresa, vehiculo_id, fecha_dt):
    return (
        Parada.objects.for_empresa(empresa)
        .filter(
            vehiculo_id=vehiculo_id,
            inicio__date=fecha_dt
        )
        .order_by("inicio")
    )


# =================================================
# 🛑 PARADAS PROLONGADAS EN RANGO
# =================================================
def obtener_paradas_prolongadas(empresa, vehiculo, inicio, fin):
    return Parada.objects.for_empresa(empresa).filter(
        vehiculo=vehiculo,
        es_prolongada=True,
        inicio__gte=inicio,
        inicio__lt=fin
    )