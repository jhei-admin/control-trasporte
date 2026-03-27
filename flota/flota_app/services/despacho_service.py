# services/despacho_service.py

from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import (
    RegistroSalida,
    Vehiculo,
    Ruta,
    PuntoControl,
    MarcacionPunto,
)

from . import recalcular_cola


# =================================================
# 🚍 CREAR SALIDA DESDE PANEL
# =================================================
def crear_salida_desde_codigo(empresa, codigo):
    hoy = timezone.localdate()

    vehiculo = Vehiculo.objects.for_empresa(empresa).filter(
        codigo=codigo,
        activo=True
    ).first()

    if not vehiculo:
        raise ValidationError("Vehículo no encontrado")

    if RegistroSalida.objects.for_empresa(empresa).filter(
        vehiculo=vehiculo,
        fecha=hoy,
        activo=True
    ).exists():
        raise ValidationError("La unidad ya está registrada hoy")

    ruta = Ruta.objects.for_empresa(empresa).first()
    if not ruta:
        raise ValidationError("No hay rutas disponibles")

    salida = RegistroSalida.objects.create(
        vehiculo=vehiculo,
        ruta=ruta,
        fecha=hoy,
        hora_llegada=timezone.now(),
        activo=True,
        en_cola=False,
        bloqueado=False
    )

    return salida


# =================================================
# 🚦 PONER EN COLA (CORE)
# =================================================
def poner_salida_en_cola(salida, empresa):
    hoy = timezone.localdate()

    if salida.fecha != hoy:
        salida.activo = False
        salida.en_cola = False
        salida.save(update_fields=["activo", "en_cola"])

        salida = RegistroSalida.objects.create(
            vehiculo=salida.vehiculo,
            ruta=salida.ruta,
            fecha=hoy,
            hora_llegada=timezone.now(),
            activo=True,
            en_cola=False,
            bloqueado=False
        )

    if not salida.hora_salida:
        raise ValidationError("Debe asignar hora antes de poner en cola")

    if salida.en_cola:
        return salida

    with transaction.atomic():

        cola = (
            RegistroSalida.objects
            .select_for_update()
            .filter(
                ruta=salida.ruta,
                fecha=hoy,
                en_cola=True,
                activo=True
            )
            .order_by("orden_cola")
        )

        ultimo = cola.last()

        salida.en_cola = True
        salida.orden_cola = (ultimo.orden_cola + 1) if ultimo else 1
        salida.save(update_fields=["en_cola", "orden_cola"])

        if not salida.bloqueado:
            recalcular_cola(empresa=empresa)

    return salida


# =================================================
# ❌ QUITAR DE COLA
# =================================================
def quitar_salida_de_cola(salida, empresa):

    if not salida.en_cola:
        return salida

    salida.en_cola = False
    salida.orden_cola = None
    salida.activo = False

    salida.save(update_fields=["en_cola", "orden_cola", "activo"])

    recalcular_cola(empresa=empresa)

    return salida


# =================================================
# ⏱️ ASIGNAR HORA FIJA
# =================================================
def asignar_hora_fija_salida(salida, hora_dt, empresa):

    if salida.hora_real_salida:
        raise ValidationError("La unidad ya inició la ruta")

    salida.fecha = timezone.localdate()
    salida.hora_fija = hora_dt
    salida.hora_salida = hora_dt
    salida.bloqueado = True

    salida.save(update_fields=[
        "fecha",
        "hora_fija",
        "hora_salida",
        "bloqueado"
    ])

    puntos = PuntoControl.objects.for_empresa(empresa).filter(
        ruta=salida.ruta
    ).order_by("orden")

    for punto in puntos:
        MarcacionPunto.objects.get_or_create(
            registro_salida=salida,
            punto=punto
        )

    for m in salida.marcaciones.all():
        m.hora_programada = m.calcular_hora_programada()
        m.save(update_fields=["hora_programada"])

    return salida


# =================================================
# 🔓 DESBLOQUEAR HORA
# =================================================
def desbloquear_hora_salida(salida):

    marco_sali = MarcacionPunto.objects.filter(
        registro_salida=salida,
        punto__codigo="SALI",
        hora_marcada__isnull=False
    ).exists()

    if marco_sali:
        raise ValidationError("La unidad ya inició la ruta")

    salida.hora_salida = None
    salida.bloqueado = False
    salida.save(update_fields=["hora_salida", "bloqueado"])

    return salida