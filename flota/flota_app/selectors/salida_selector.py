from ..models import RegistroSalida, MarcacionPunto


def obtener_salida_activa(vehiculo, hoy):
    return (
        RegistroSalida.objects.for_empresa(vehiculo.empresa)
        .filter(
            vehiculo=vehiculo,
            fecha=hoy,
            activo=True
        )
        .order_by("-en_cola", "orden_cola", "hora_salida")
        .first()
    )


def obtener_salida_simple(vehiculo, hoy):
    return (
        RegistroSalida.objects.for_empresa(vehiculo.empresa)
        .filter(
            vehiculo=vehiculo,
            fecha=hoy,
            activo=True
        )
        .order_by("-id")
        .first()
    )


def obtener_ultimo_marcado(salida):
    return (
        MarcacionPunto.objects
        .filter(
            registro_salida=salida,
            hora_marcada__isnull=False
        )
        .select_related("punto")
        .order_by("-punto__orden")
        .first()
    )


def obtener_siguiente_marcacion(salida, orden_actual):
    return (
        MarcacionPunto.objects
        .filter(
            registro_salida=salida,
            punto__orden__gt=orden_actual
        )
        .select_related("punto")
        .order_by("punto__orden")
        .first()
    )


def obtener_salida_actual_con_hora(vehiculo, hoy):
    return (
        RegistroSalida.objects
        .for_empresa(vehiculo.empresa)
        .select_related("vehiculo", "ruta")
        .filter(
            vehiculo=vehiculo,
            fecha=hoy,
            activo=True,
            hora_salida__isnull=False
        )
        .order_by("hora_salida")
        .first()
    )


def obtener_cola(empresa, ruta, hoy):
    return list(
        RegistroSalida.objects.for_empresa(empresa)
        .select_related("vehiculo")
        .filter(
            fecha=hoy,
            activo=True,
            ruta=ruta,
            vehiculo__empresa=empresa,
            hora_salida__isnull=False
        )
        .order_by("hora_salida")
    )


def obtener_salidas_panel(empresa, hoy):
    from django.db.models import Case, When, IntegerField
    from django.db import models

    return (
        RegistroSalida.objects
        .for_empresa(empresa)
        .select_related("vehiculo", "ruta")
        .filter(
            ruta__isnull=False,
            activo=True,
            fecha=hoy
        )
        .order_by(
            Case(
                When(hora_salida__isnull=False, then=0),
                When(hora_salida__isnull=True, then=1),
                output_field=IntegerField(),
            ),
            models.F("hora_salida").asc(nulls_last=True),
            "hora_llegada",
        )
    )


def obtener_salidas_con_marcaciones(empresa, hoy):
    from django.db.models import Max, Q

    return (
        RegistroSalida.objects.for_empresa(empresa)
        .filter(
            activo=True,
            fecha=hoy,
            ruta__isnull=False,
        )
        .annotate(
            ultimo_punto_orden=Max(
                "marcaciones__punto__orden",
                filter=Q(marcaciones__hora_marcada__isnull=False)
            ),
            ultimo_tiempo=Max("marcaciones__hora_marcada")
        )
        .prefetch_related("marcaciones__punto")
    )