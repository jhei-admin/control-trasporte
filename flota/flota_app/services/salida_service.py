from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from ..models import (
    RegistroSalida,
    Vehiculo,
    Ruta,
    PuntoControl,
    MarcacionPunto,
    ConfiguracionDespacho,
)


# =================================================
# 🔧 NORMALIZAR CÓDIGO
# =================================================
def normalizar_codigo(codigo_raw):
    codigo_raw = codigo_raw.strip()

    if codigo_raw.isdigit() and len(codigo_raw) == 1:
        return codigo_raw.zfill(2)

    return codigo_raw


# =================================================
# 🚍 OBTENER VEHÍCULO
# =================================================
def obtener_vehiculo(empresa, codigo):
    return Vehiculo.objects.for_empresa(empresa).filter(
        codigo=codigo,
        activo=True
    ).first()


# =================================================
# 🔒 VALIDAR DUPLICADO
# =================================================
def salida_ya_registrada(empresa, vehiculo, fecha):
    return RegistroSalida.objects.for_empresa(empresa).filter(
        vehiculo=vehiculo,
        fecha=fecha,
        activo=True
    ).exists()


# =================================================
# 🧭 OBTENER RUTA
# =================================================
def obtener_ruta_default(empresa):
    return Ruta.objects.for_empresa(empresa).first()


# =================================================
# 🟢 CREAR SALIDA
# =================================================
def crear_salida(vehiculo, ruta):
    salida = RegistroSalida(
        vehiculo=vehiculo,
        ruta=ruta,
        fecha=timezone.localdate(),
        hora_llegada=timezone.now(),
        activo=True,
        en_cola=False,
        bloqueado=False
    )

    salida.full_clean()
    salida.save()

    return salida


# =================================================
# 🚦 PONER EN COLA (CORE)
# =================================================
def poner_en_cola(salida, empresa):

    hoy = timezone.localdate()

    with transaction.atomic():

        # 🔁 recrear si es de otro día
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
            raise ValueError("Debe asignar hora antes de poner en cola")

        if salida.en_cola:
            return salida

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

    return salida


# =================================================
# 🚦 QUITAR DE COLA
# =================================================
def quitar_de_cola(salida):

    if not salida.en_cola:
        return salida

    salida.en_cola = False
    salida.orden_cola = None
    salida.activo = False

    salida.save(update_fields=[
        "en_cola",
        "orden_cola",
        "activo"
    ])

    return salida


# =================================================
# ⏱️ ASIGNAR HORA
# =================================================
def asignar_hora_fija(salida, hora_dt, empresa):

    if salida.hora_real_salida:
        raise ValueError("La unidad ya inició la ruta")

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

    # 🔧 crear marcaciones
    puntos = PuntoControl.objects.for_empresa(empresa).filter(
        ruta=salida.ruta
    ).order_by("orden")

    for punto in puntos:
        MarcacionPunto.objects.get_or_create(
            registro_salida=salida,
            punto=punto
        )

    # ⏱ recalcular horas
    for m in salida.marcaciones.all():
        m.hora_programada = m.calcular_hora_programada()
        m.save(update_fields=["hora_programada"])

    return salida


# =================================================
# 🔓 DESBLOQUEAR HORA
# =================================================
def desbloquear_hora(salida):

    salida.hora_salida = None
    salida.bloqueado = False

    salida.save(update_fields=[
        "hora_salida",
        "bloqueado"
    ])

    return salida


# =================================================
# 🔁 RE-CALCULAR COLA
# =================================================
def recalcular_cola(empresa):

    config = ConfiguracionDespacho.objects.filter(
        empresa=empresa,
        activa=True
    ).first()

    intervalo = config.intervalo_fijo if config and config.intervalo_fijo else 6

    hoy = timezone.localdate()

    salidas = (
        RegistroSalida.objects.for_empresa(empresa)
        .filter(
            fecha=hoy,
            en_cola=True,
            activo=True,
            hora_salida__isnull=False
        )
        .order_by("orden_cola")
    )

    base_time = None

    for i, salida in enumerate(salidas):

        if i == 0:
            base_time = salida.hora_salida
            continue

        nueva_hora = base_time + timezone.timedelta(minutes=intervalo * i)

        salida.hora_salida = nueva_hora
        salida.save(update_fields=["hora_salida"])

    return True