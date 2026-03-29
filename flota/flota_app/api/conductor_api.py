from datetime import timedelta
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone

# ✅ Apunta explícitamente al módulo correcto
from ..services.sesion_service import validar_sesion, calcular_estado_sesion
from ..utils import distancia_metros

# 🔥 SELECTORS GPS
from ..selectors.gps_selector import (
    obtener_ultimo_gps,
    crear_gps_simple,
    actualizar_ubicacion_simple,
)

# 🔥 SELECTORS SALIDA
from ..selectors.salida_selector import (
    get_salida_activa,
)

# 🔥 SELECTORS VEHICULO
from ..selectors.vehiculo_selector import (
    get_vehiculo_activo,
)

from ..models import (
    PuntoControl,
    MarcacionPunto,
    SesionUnidad,
    MensajeGlobal,
)


# =================================================
# 📡 GPS CONDUCTOR
# =================================================
@csrf_exempt
@require_POST
def api_gps_conductor(request):

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"accion": "ignorar"})

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)

    if not sesion:
        return JsonResponse({
            "accion": "bloqueado",
            "mensaje": "Sesión inválida"
        })

    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    lat = data.get("lat")
    lng = data.get("lng")
    precision = data.get("precision")

    if lat is None or lng is None:
        return JsonResponse({"accion": "ninguna"})

    try:
        lat = float(lat)
        lng = float(lng)
        precision = float(precision) if precision else None
    except (TypeError, ValueError):
        return JsonResponse({"accion": "ninguna"})

    if precision and precision > 100:
        return JsonResponse({"accion": "ninguna"})

    ahora = timezone.now()
    hoy = timezone.localdate()

    # 📍 UBICACIÓN
    actualizar_ubicacion_simple(sesion.vehiculo, lat, lng)

    # 🛰️ GPS HISTÓRICO
    ultimo = obtener_ultimo_gps(sesion)

    if not ultimo or (ahora - ultimo.timestamp) >= timedelta(seconds=5):
        crear_gps_simple(sesion, lat, lng, precision)

    # 🚍 SALIDA
    salida = get_salida_activa(sesion.vehiculo, hoy)

    if not salida:
        return JsonResponse({"accion": "ninguna"})

    # 🧱 MARCACIONES
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

    if not marcacion:
        if not salida.hora_real_salida:
            return JsonResponse({"accion": "ninguna"})

        salida.cerrar()
        return JsonResponse({"accion": "audio", "audio": "ruta_completada"})

    punto = marcacion.punto

    distancia = distancia_metros(
        lat, lng,
        float(punto.latitud),
        float(punto.longitud)
    )

    if distancia > punto.radio_metros:
        return JsonResponse({"accion": "ninguna"})

    if marcacion.hora_marcada:
        if (ahora - marcacion.hora_marcada).total_seconds() < 10:
            return JsonResponse({"accion": "ninguna"})

    if not salida.hora_real_salida:
        salida.iniciar(ahora)

        if sesion.salida_id != salida.id:
            sesion.salida = salida
            sesion.save(update_fields=["salida"])

    marcacion.marcar(hora=ahora)

    return JsonResponse({
        "accion": "audio" if marcacion.audio_flag else "visual",
        "audio": marcacion.audio_flag,
        "visual": {
            "codigo": punto.codigo,
            "punto": punto.nombre,
            "estado": marcacion.estado.upper(),
            "diferencia_min": marcacion.diferencia_minutos
        }
    })


# =================================================
# 🫀 HEARTBEAT
# =================================================
@csrf_exempt
@require_POST
def api_heartbeat(request):

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"ok": False}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)

    if not sesion:
        return JsonResponse({"ok": False}, status=403)

    ahora = timezone.now()
    sesion.last_heartbeat = ahora
    sesion.save(update_fields=["last_heartbeat"])

    hoy = timezone.localdate()

    mensaje = (
        MensajeGlobal.objects
        .filter(
            activo=True,
            fecha_inicio__lte=hoy,
            fecha_fin__gte=hoy
        )
        .order_by("-updated_at")
        .first()
    )

    return JsonResponse({
        "ok": True,
        "estado": "ACTIVO",
        "timestamp": ahora.isoformat(),
        "mensaje": mensaje.texto if mensaje else None
    })


# =================================================
# 📱 ESTADO
# =================================================
@csrf_exempt
@require_POST
def api_app_estado(request):

    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return JsonResponse({"estado": "BLOQUEADO"})

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)

    if not sesion:
        return JsonResponse({"estado": "BLOQUEADO"})

    hoy = timezone.localdate()
    salida = get_salida_activa(sesion.vehiculo, hoy)

    if not salida:
        return JsonResponse({"estado": "SIN_SALIDA"})

    if not salida.hora_salida:
        return JsonResponse({"estado": "SIN_HORA"})

    if salida.en_cola:
        return JsonResponse({"estado": "EN_COLA"})

    return JsonResponse({"estado": "SALIDA_ACTIVA"})


# =================================================
# 🔐 QR
# =================================================
@csrf_exempt
def api_escanear_qr(request):

    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)

    data = json.loads(request.body or "{}")
    vehiculo_id = data.get("vehiculo_id")

    vehiculo = get_vehiculo_activo(vehiculo_id)

    if not vehiculo:
        return JsonResponse({"ok": False})

    SesionUnidad.objects.filter(
        vehiculo=vehiculo,
        activa=True
    ).update(activa=False)

    sesion = SesionUnidad.objects.create(
        vehiculo=vehiculo,
        activa=True
    )

    return JsonResponse({
        "ok": True,
        "token": str(sesion.token),
        "unidad": vehiculo.codigo,
    })