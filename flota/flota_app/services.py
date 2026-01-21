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
# 📡 CALCULAR ESTADO GPS / SESIÓN  ✅
# =================================================
def calcular_estado_sesion(sesion):
    """
    Determina el estado GPS de la unidad.
    NO decide lógica de salida.
    SOLO estado técnico.
    """

    if not sesion:
        return "OFFLINE"

    # ⚠️ USAR SIEMPRE EL MISMO CAMPO
    ultimo_ping = getattr(sesion, "ultimo_gps", None)

    if not ultimo_ping:
        return "OFFLINE"

    ahora = timezone.now()
    diferencia = (ahora - ultimo_ping).total_seconds()

    if diferencia <= 30:
        return "ACTIVO"
    elif diferencia <= 120:
        return "INACTIVO"
    else:
        return "OFFLINE"


# =================================================
# 🚦 INICIAR SALIDA (BLINDADO)
# =================================================
def iniciar_salida_segura(salida):
    """
    Marca la salida como iniciada de forma segura.
    """
    if salida.hora_real_salida:
        return salida

    salida.hora_real_salida = timezone.now()
    salida.en_cola = False
    salida.activo = True

    salida.save(
        update_fields=[
            "hora_real_salida",
            "en_cola",
            "activo"
        ]
    )

    return salida


# =================================================
# 🚏 REGISTRAR LLEGADA AL PARADERO
# =================================================
def registrar_llegada_al_paradero(vehiculo, ruta):
    """
    Se ejecuta cuando la unidad llega al paradero.
    Crea un RegistroSalida limpio y controlado.
    """

    # 🔒 Cerrar cualquier salida activa previa
    RegistroSalida.objects.filter(
        vehiculo=vehiculo,
        activo=True
    ).update(
        activo=False,
        en_cola=False
    )

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
    - Intervalo fijo si existe
    - Automático si no
    """

    config = ConfiguracionDespacho.objects.filter(
        activa=True
    ).first()

    salidas = RegistroSalida.objects.filter(
        en_cola=True,
        activo=True
    ).order_by("hora_llegada")

    if not salidas.exists():
        return

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

        hora_base += timedelta(minutes=intervalo_minutos)
