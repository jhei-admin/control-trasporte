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
        sesion = (
            SesionUnidad.objects
            .select_related("vehiculo")
            .get(token=token, activa=True)
        )
    except SesionUnidad.DoesNotExist:
        return None

    if not sesion.esta_valida():
        return None

    return sesion


# =================================================
# 📡 ESTADO GPS / SESIÓN (TÉCNICO)
# =================================================
def calcular_estado_sesion(sesion):
    """
    Estado técnico de comunicación GPS.
    NO decide lógica de salida.
    """

    if not sesion or not sesion.last_heartbeat:
        return "OFFLINE"

    ahora = timezone.now()
    delta = (ahora - sesion.last_heartbeat).total_seconds()

    if delta <= 30:
        return "ACTIVO"
    elif delta <= 120:
        return "INACTIVO"
    return "OFFLINE"


# =================================================
# 🚦 INICIAR SALIDA (BLINDADO)
# =================================================
def iniciar_salida_segura(salida):
    """
    Inicia una salida solo una vez.
    """

    if salida.hora_real_salida:
        return salida

    salida.hora_real_salida = timezone.now()
    salida.en_cola = False
    salida.activo = True

    salida.save(update_fields=[
        "hora_real_salida",
        "en_cola",
        "activo"
    ])

    return salida


# =================================================
# 🚏 REGISTRAR LLEGADA AL PARADERO
# =================================================
def registrar_llegada_al_paradero(vehiculo, ruta):
    """
    Crea una salida nueva y cierra las anteriores.
    """

    hoy = timezone.localdate()

    RegistroSalida.objects.filter(
        vehiculo=vehiculo,
        activo=True
    ).update(
        activo=False,
        en_cola=False
    )

    return RegistroSalida.objects.create(
        vehiculo=vehiculo,
        ruta=ruta,
        fecha=hoy,
        hora_llegada=timezone.now(),
        activo=True,
        en_cola=False
    )


# =================================================
# 🔁 RECALCULAR COLA (RESPETA HORA FIJA)
# =================================================
def recalcular_cola():
    """
    Recalcula horas de salida SOLO para:
    - salidas de HOY
    - activas
    - en cola

    ✔ Respeta hora fija (bloqueado)
    """

    hoy = timezone.localdate()

    salidas = (
        RegistroSalida.objects
        .filter(
            fecha=hoy,
            activo=True,
            en_cola=True
        )
        .order_by("orden_cola", "hora_llegada")
    )

    if not salidas.exists():
        return

    config = ConfiguracionDespacho.objects.filter(
        activa=True
    ).first()

    if config and config.intervalo_fijo:
        intervalo = config.intervalo_fijo
    else:
        cantidad = salidas.count()
        if cantidad <= 2:
            intervalo = 5
        elif cantidad <= 5:
            intervalo = 7
        else:
            intervalo = 10

    tz = timezone.get_current_timezone()
    ahora = timezone.localtime(timezone.now(), tz)

    hora_actual = None

    for salida in salidas:

        # 🔒 RESPETAR HORA FIJA
        if salida.bloqueado and salida.hora_fija:
            hora_actual = salida.hora_fija
            salida.hora_salida = salida.hora_fija
            salida.intervalo_minutos = intervalo
            salida.save(update_fields=[
                "hora_salida",
                "intervalo_minutos"
            ])
            continue

        # 🔄 AUTOMÁTICO
        if hora_actual:
            nueva_hora = hora_actual + timedelta(minutes=intervalo)
        else:
            nueva_hora = ahora.replace(second=0, microsecond=0)

        if nueva_hora < ahora:
            nueva_hora = ahora.replace(second=0, microsecond=0)

        salida.hora_salida = nueva_hora
        salida.intervalo_minutos = intervalo
        salida.save(update_fields=[
            "hora_salida",
            "intervalo_minutos"
        ])

        hora_actual = nueva_hora
