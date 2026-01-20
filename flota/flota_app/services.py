from django.utils import timezone
from datetime import timedelta

from .models import (
    RegistroSalida,
    ConfiguracionDespacho,
    SesionUnidad
)

# =================================================
# 🔐 VALIDAR SESIÓN ACTIVA (CLAVE DEL SISTEMA)
# =================================================
def validar_sesion(token):
    """
    Valida una sesión activa a partir del token.
    Retorna la sesión si es válida o None si no lo es.
    """

    try:
        sesion = SesionUnidad.objects.select_related(
            "vehiculo"
        ).get(
            token=token,
            activa=True
        )
    except SesionUnidad.DoesNotExist:
        return None

    if not sesion.esta_valida():
        return None

    return sesion


# =================================================
# 🚏 REGISTRAR LLEGADA AL PARADERO
# =================================================
def registrar_llegada_al_paradero(vehiculo, ruta):
    """
    Se ejecuta cuando la unidad llega al paradero.
    Crea un RegistroSalida limpio y controlado.
    """

    # 🔒 Cerrar cualquier salida activa previa del vehículo
    RegistroSalida.objects.filter(
        vehiculo=vehiculo,
        activo=True
    ).update(
        activo=False,
        en_cola=False
    )

    # 🆕 Crear nuevo registro de salida
    nueva_salida = RegistroSalida.objects.create(
        vehiculo=vehiculo,
        ruta=ruta,
        hora_llegada=timezone.now(),
        activo=True,
        en_cola=False
    )

    return nueva_salida


# =================================================
# 🔁 RECALCULAR COLA DE SALIDAS
# =================================================
def recalcular_cola():
    """
    Recalcula horas de salida de las unidades en cola.
    - Si hay intervalo fijo → se usa siempre
    - Si no → modo automático (5 / 7 / 10 min)
    """

    # 🧠 Obtener configuración de despacho (solo 1 activa)
    config = ConfiguracionDespacho.objects.filter(
        activa=True
    ).first()

    # 🚍 Obtener salidas en cola, activas y ordenadas
    salidas = RegistroSalida.objects.filter(
        en_cola=True,
        activo=True
    ).order_by("hora_llegada")

    if not salidas.exists():
        return

    # =================================================
    # 🧠 DETERMINAR INTERVALO
    # =================================================
    if config and config.intervalo_fijo:
        intervalo_minutos = config.intervalo_fijo
    else:
        cantidad = salidas.count()

        if cantidad <= 2:
            intervalo_minutos = 5
        elif cantidad <= 5:
            intervalo_minutos = 7
        else:
            intervalo_minutos = 10

    # =================================================
    # ⏱️ ASIGNAR HORAS DE SALIDA
    # =================================================
    hora_base = timezone.now().replace(
        second=0,
        microsecond=0
    )

    for salida in salidas:
        salida.hora_salida = hora_base
        salida.intervalo_minutos = intervalo_minutos
        salida.save(
            update_fields=[
                "hora_salida",
                "intervalo_minutos"
            ]
        )

        hora_base += timedelta(
            minutes=intervalo_minutos
        )
