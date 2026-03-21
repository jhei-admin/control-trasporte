from datetime import timedelta
from django.utils import timezone
from django.db import transaction

from ..models import (
    GPSRegistro,
    UbicacionVehiculo,
    RegistroSalida,
    PuntoControl,
    MarcacionPunto
)

from ..utils import distancia_metros


def procesar_gps_conductor(sesion, lat, lng, precision):
    ahora = timezone.now()
    hoy = timezone.localdate()

    vehiculo = sesion.vehiculo
    empresa = vehiculo.empresa

    # =================================================
    # 🔴 FILTRO GPS BASURA
    # =================================================
    if precision and precision > 100:
        return {"accion": "ninguna"}

    # =================================================
    # 📍 UBICACIÓN ACTUAL (SIN update_or_create ❌)
    # =================================================
    UbicacionVehiculo.objects.filter(
        vehiculo=vehiculo
    ).update(
        latitud=lat,
        longitud=lng
    )

    # Si no existe → crear (1 vez)
    if not UbicacionVehiculo.objects.filter(vehiculo=vehiculo).exists():
        UbicacionVehiculo.objects.create(
            vehiculo=vehiculo,
            latitud=lat,
            longitud=lng
        )

    # =================================================
    # 🛰️ GPS HISTÓRICO (ANTI-SPAM)
    # =================================================
    ultimo = (
        GPSRegistro.objects
        .filter(sesion=sesion)
        .only("timestamp")
        .order_by("-timestamp")
        .first()
    )

    if not ultimo or (ahora - ultimo.timestamp) >= timedelta(seconds=5):
        GPSRegistro.objects.create(
            sesion=sesion,
            lat=lat,
            lng=lng,
            precision=precision
        )

    # =================================================
    # 🚍 SALIDA ACTIVA
    # =================================================
    salida = (
        RegistroSalida.objects
        .filter(
            vehiculo=vehiculo,
            fecha=hoy,
            activo=True
        )
        .select_related("ruta")
        .prefetch_related("marcaciones__punto")
        .order_by("-id")
        .first()
    )

    if not salida:
        return {"accion": "ninguna"}

    # =================================================
    # 🔁 ASEGURAR MARCACIONES (OPTIMIZADO)
    # =================================================
    if salida.ruta and not salida.marcaciones.exists():

        puntos = list(
            PuntoControl.objects.filter(
                ruta=salida.ruta,
                activo=True
            ).order_by("orden")
        )

        MarcacionPunto.objects.bulk_create([
            MarcacionPunto(
                registro_salida=salida,
                punto=p
            )
            for p in puntos
        ], ignore_conflicts=True)

    # =================================================
    # 📍 SIGUIENTE MARCACIÓN
    # =================================================
    marcacion = salida.siguiente_marcacion()

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
    # 📏 DISTANCIA
    # =================================================
    distancia = distancia_metros(
        lat,
        lng,
        float(punto.latitud),
        float(punto.longitud)
    )

    if distancia > punto.radio_metros:
        return {"accion": "ninguna"}

    # =================================================
    # 🔒 DEBOUNCE
    # =================================================
    if marcacion.hora_marcada:
        if (ahora - marcacion.hora_marcada).total_seconds() < 10:
            return {"accion": "ninguna"}

    # =================================================
    # 🔥 TRANSACCIÓN (CRÍTICO)
    # =================================================
    with transaction.atomic():

        if not salida.hora_real_salida:
            salida.hora_real_salida = ahora
            salida.en_cola = False
            salida.save(update_fields=[
                "hora_real_salida",
                "en_cola"
            ])

        marcacion.marcar(hora=ahora)

    return {
        "accion": "audio" if marcacion.audio_flag else "visual",
        "audio": marcacion.audio_flag,
        "visual": {
            "codigo": punto.codigo,
            "punto": punto.nombre,
            "estado": marcacion.estado.upper(),
            "diferencia_min": marcacion.diferencia_minutos
        }
    }