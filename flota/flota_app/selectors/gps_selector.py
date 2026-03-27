from datetime import timedelta

from ..models import GPSRegistro, UbicacionVehiculo


# =================================================
# 📍 CREAR GPS HISTÓRICO (COMPLETO)
# =================================================
def crear_gps(
    sesion,
    lat,
    lng,
    velocidad=None,
    precision=None,
    bateria=None
):
    return GPSRegistro.objects.create(
        sesion=sesion,
        lat=lat,
        lng=lng,
        velocidad=velocidad,
        precision=precision,
        bateria=bateria
    )


# =================================================
# 📍 CREAR GPS SIMPLE (APP CONDUCTOR)
# =================================================
def crear_gps_simple(sesion, lat, lng, precision=None):
    return GPSRegistro.objects.create(
        sesion=sesion,
        lat=lat,
        lng=lng,
        precision=precision
    )


# =================================================
# 📍 OBTENER ÚLTIMO GPS
# =================================================
def get_ultimo_gps(sesion):
    return (
        GPSRegistro.objects
        .filter(sesion=sesion)
        .order_by("-timestamp")
        .first()
    )


# =================================================
# 📍 ACTUALIZAR UBICACIÓN COMPLETA
# =================================================
def actualizar_ubicacion(
    vehiculo,
    lat,
    lng,
    velocidad=None,
    precision=None
):
    return UbicacionVehiculo.objects.update_or_create(
        vehiculo=vehiculo,
        defaults={
            "latitud": lat,
            "longitud": lng,
            "velocidad": velocidad,
            "precision": precision,
        }
    )


# =================================================
# 📍 ACTUALIZAR UBICACIÓN SIMPLE
# =================================================
def actualizar_ubicacion_simple(vehiculo, lat, lng):
    return UbicacionVehiculo.objects.update_or_create(
        vehiculo=vehiculo,
        defaults={
            "latitud": lat,
            "longitud": lng,
        }
    )


# =================================================
# 🗺️ UBICACIONES ACTIVAS DE EMPRESA
# =================================================
def get_ubicaciones_empresa(empresa, ahora):
    return (
        UbicacionVehiculo.objects.for_empresa(empresa)
        .filter(updated_at__gte=ahora - timedelta(minutes=10))
        .select_related("vehiculo")
    )


# =================================================
# 📍 UBICACIÓN ACTUAL DE UN VEHÍCULO
# =================================================
def get_ubicacion_actual(vehiculo):
    try:
        return UbicacionVehiculo.objects.get(vehiculo=vehiculo)
    except UbicacionVehiculo.DoesNotExist:
        return None


# =================================================
# 📍 MAPA DE UBICACIONES (OPTIMIZADO)
# =================================================
def get_mapa_ubicaciones(vehiculos):
    qs = UbicacionVehiculo.objects.filter(vehiculo__in=vehiculos)

    return {u.vehiculo_id: u for u in qs}