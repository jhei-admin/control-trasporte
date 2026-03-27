from datetime import timedelta
from django.utils import timezone

from ..models import (
    Parada,
    GPSRegistro,
    UbicacionVehiculo,
    RegistroSalida,
    PuntoControl,
    MarcacionPunto,
)

from ..utils import distancia_metros

TIEMPO_MIN_PARADA = 120
TIEMPO_PARADA_PROLONGADA = 300
VEL_DETENIDO = 1
RADIO_METROS = 20


# =================================================
# 🛑 DETECTOR DE PARADAS
# =================================================
def procesar_parada(vehiculo, lat, lng, velocidad, timestamp):

    parada = Parada.objects.for_empresa(vehiculo.empresa).filter(
        vehiculo=vehiculo,
        activa=True
    ).order_by("-inicio").first()

    if velocidad <= VEL_DETENIDO:

        if not parada:
            Parada.objects.create(
                vehiculo=vehiculo,
                lat=lat,
                lng=lng,
                inicio=timestamp
            )
            return

        distancia = distancia_metros(
            parada.lat, parada.lng,
            lat, lng
        )

        if distancia > RADIO_METROS:
            parada.cerrar(timestamp)
            Parada.objects.create(
                vehiculo=vehiculo,
                lat=lat,
                lng=lng,
                inicio=timestamp
            )
            return

        duracion = (timestamp - parada.inicio).total_seconds()

        if not parada.es_prolongada and duracion >= TIEMPO_PARADA_PROLONGADA:
            parada.es_prolongada = True
            parada.save(update_fields=["es_prolongada"])

    else:
        if parada:
            duracion = (timestamp - parada.inicio).total_seconds()

            if duracion < TIEMPO_MIN_PARADA:
                parada.delete()
            else:
                parada.cerrar(timestamp)


# =================================================
# 📡 GPS GENERAL (MAPA + HISTÓRICO)
# =================================================
def procesar_gps_general(sesion, lat, lng, velocidad, precision, bateria):

    ahora = timezone.now()

    # 📜 histórico
    GPSRegistro.objects.create(
        sesion=sesion,
        lat=lat,
        lng=lng,
        velocidad=velocidad,
        precision=precision,
        bateria=bateria
    )

    # 🛑 paradas
    procesar_parada(
        vehiculo=sesion.vehiculo,
        lat=lat,
        lng=lng,
        velocidad=velocidad,
        timestamp=ahora
    )

    # 🔴 filtro GPS basura
    if precision is not None and precision > 100:
        sesion.last_heartbeat = ahora
        sesion.save(update_fields=["last_heartbeat"])

        return {
            "ok": True,
            "descartado": True
        }

    # 📍 ubicación actual
    UbicacionVehiculo.objects.update_or_create(
        vehiculo=sesion.vehiculo,
        defaults={
            "latitud": lat,
            "longitud": lng,
            "velocidad": velocidad,
            "precision": precision,
        }
    )

    # 🫀 heartbeat
    sesion.last_heartbeat = ahora
    sesion.save(update_fields=["last_heartbeat"])

    return {
        "ok": True,
        "vehiculo": sesion.vehiculo.codigo,
        "lat": lat,
        "lng": lng
    }


# =================================================
# 📡 GPS CONDUCTOR (MARCACIÓN INTELIGENTE)
# =================================================
def procesar_gps_conductor(sesion, lat, lng, precision):

    hoy = timezone.localdate()
    ahora = timezone.now()

    # =================================================
    # 🔴 FILTRO GPS BASURA (🔥 FALTABA)
    # =================================================
    if precision is not None and precision > 100:
        return {"accion": "ninguna"}

    # =================================================
    # 🔥 cerrar salidas antiguas
    # =================================================
    RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa).filter(
        vehiculo=sesion.vehiculo,
        activo=True,
        fecha__lt=hoy
    ).update(activo=False, en_cola=False)

    # =================================================
    # 📍 ubicación actual
    # =================================================
    UbicacionVehiculo.objects.update_or_create(
        vehiculo=sesion.vehiculo,
        defaults={
            "latitud": lat,
            "longitud": lng,
        }
    )

    # =================================================
    # 🛰️ anti spam GPS
    # =================================================
    ultimo = GPSRegistro.objects.filter(
        sesion=sesion
    ).order_by("-timestamp").first()

    if not ultimo or (ahora - ultimo.timestamp) >= timedelta(seconds=5):
        GPSRegistro.objects.create(
            sesion=sesion,
            lat=lat,
            lng=lng,
            precision=precision
        )

    # =================================================
    # 🚍 salida activa
    # =================================================
    salida = RegistroSalida.objects.for_empresa(
        sesion.vehiculo.empresa
    ).filter(
        vehiculo=sesion.vehiculo,
        fecha=hoy,
        activo=True
    ).order_by("-id").first()

    if not salida:
        return {"accion": "ninguna"}

    # =================================================
    # 🔧 asegurar marcaciones
    # =================================================
    if salida.ruta and not salida.marcaciones.exists():
        puntos = PuntoControl.objects.filter(
            ruta=salida.ruta,
            activo=True
        ).order_by("orden")

        for punto in puntos:
            MarcacionPunto.objects.get_or_create(
                registro_salida=salida,
                punto=punto
            )

    marcacion = salida.siguiente_marcacion()

    # =================================================
    # 🏁 FIN DE RUTA
    # =================================================
    if not marcacion:
        salida.activo = False
        salida.en_cola = False
        salida.save(update_fields=["activo", "en_cola"])

        return {
            "accion": "audio",
            "audio": "ruta_completada"
        }

    punto = marcacion.punto

    # =================================================
    # 📏 distancia
    # =================================================
    distancia = distancia_metros(
        lat, lng,
        float(punto.latitud),
        float(punto.longitud)
    )

    if distancia > punto.radio_metros:
        return {"accion": "ninguna"}

    # =================================================
    # 🔒 ANTI DOBLE MARCACIÓN (🔥 FALTABA)
    # =================================================
    if marcacion.hora_marcada:
        delta = ahora - marcacion.hora_marcada
        if delta.total_seconds() < 10:
            return {"accion": "ninguna"}

    # =================================================
    # ✅ marcar
    # =================================================
    marcacion.marcar(hora=ahora)

    return {
        "accion": "audio" if marcacion.audio_flag else "visual",
        "audio": marcacion.audio_flag,
        "visual": {
            "codigo": punto.codigo,
            "punto": punto.nombre,
            "estado": marcacion.estado.upper(),
        }
    }