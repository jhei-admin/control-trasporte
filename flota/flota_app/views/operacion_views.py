from datetime import datetime, timedelta
import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from ..decorators import empresa_required
from ..services.gps_service import procesar_parada



from ..models import (
    RegistroSalida,
    GPSRegistro,
    UbicacionVehiculo,
    MensajeGlobal,
    PuntoControl,
    MarcacionPunto,
)

from ..services.sesion_service import obtener_sesion_valida
from ..utils import distancia_metros
from ..services import calcular_estado_sesion


# =================================================
# 📡 API GPS CONDUCTOR
# =================================================
@csrf_exempt
def api_gps_conductor(request):

    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido"}, status=405)

    # =============================================
    # 🔑 TOKEN
    # =============================================
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"accion": "ignorar"})

    token = auth.replace("Bearer ", "").strip()

    sesion = obtener_sesion_valida(token)
    if not sesion:
        return JsonResponse({
            "accion": "bloqueado",
            "mensaje": "Sesión inválida o reemplazada"
        })

    hoy = timezone.localdate()

    # =============================================
    # 🔥 CIERRE AUTOMÁTICO DE SALIDAS ANTIGUAS
    # =============================================
    RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa).filter(
        vehiculo=sesion.vehiculo,
        activo=True,
        fecha__lt=hoy
    ).update(
        activo=False,
        en_cola=False
    )

    # =============================================
    # 📦 LEER JSON
    # =============================================
    try:
        data = json.loads(request.body or "{}")
    except Exception:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    lat = data.get("lat")
    lng = data.get("lng")
    precision = data.get("precision")

    if lat is None or lng is None:
        return JsonResponse({"accion": "ninguna"})

    try:
        lat = float(lat)
        lng = float(lng)
        precision = float(precision) if precision is not None else None
    except (TypeError, ValueError):
        return JsonResponse({"accion": "ninguna"})

    # =================================================
    # 🔴 FILTRO GPS BASURA (PROFESIONAL)
    # =================================================
    if precision is not None and precision > 100:
        return JsonResponse({"accion": "ninguna"})

    # =================================================
    # 📍 UBICACIÓN ACTUAL
    # =================================================
    UbicacionVehiculo.objects.update_or_create(
        vehiculo=sesion.vehiculo,
        defaults={
            "latitud": lat,
            "longitud": lng,
        }
    )

    # =================================================
    # 🛰️ GPS HISTÓRICO (ANTI-SPAM)
    # =================================================
    ultimo_gps = (
        GPSRegistro.objects
        .filter(sesion=sesion)
        .order_by("-timestamp")
        .first()
    )

    ahora = timezone.now()

    if not ultimo_gps or (ahora - ultimo_gps.timestamp) >= timedelta(seconds=5):
        GPSRegistro.objects.create(
            sesion=sesion,
            lat=lat,
            lng=lng,
            precision=precision
        )

    # =================================================
    # 🚍 SALIDA ACTIVA HOY
    # =================================================
    salida = (
        RegistroSalida.objects
        .for_empresa(sesion.vehiculo.empresa)
        .filter(
            vehiculo=sesion.vehiculo,
            fecha=hoy,
            activo=True
        )
        .order_by("-id")
        .first()
    )

    if not salida:
        return JsonResponse({"accion": "ninguna"})

    # =================================================
    # 🧱 ASEGURAR MARCACIONES
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

    # =================================================
    # 📍 SIGUIENTE MARCACIÓN
    # =================================================
    marcacion = salida.siguiente_marcacion()

    if not marcacion:

        if not salida.hora_real_salida:
            return JsonResponse({"accion": "ninguna"})

        salida.activo = False
        salida.en_cola = False
        salida.save(update_fields=["activo", "en_cola"])

        return JsonResponse({
            "accion": "audio",
            "audio": "ruta_completada"
        })

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
        return JsonResponse({"accion": "ninguna"})

    # =================================================
    # 🔒 ANTI DOBLE MARCACIÓN (DEBOUNCE 10 SEG)
    # =================================================
    if marcacion.hora_marcada:
        delta = ahora - marcacion.hora_marcada
        if delta.total_seconds() < 10:
            return JsonResponse({"accion": "ninguna"})

    # =================================================
    # 🟢 INICIAR SALIDA REAL (PRIMER SALI)
    # =================================================
    if not salida.hora_real_salida:
        salida.hora_real_salida = ahora
        salida.en_cola = False
        salida.activo = True
        salida.save(update_fields=[
            "hora_real_salida",
            "en_cola",
            "activo"
        ])

        if sesion.salida_id != salida.id:
            sesion.salida = salida
            sesion.save(update_fields=["salida"])

    # =================================================
    # ✅ MARCAR PUNTO
    # =================================================
    marcacion.marcar(hora=ahora)

    # =================================================
    # 🔊 RESPUESTA FINAL
    # =================================================
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
# 📡 API GPS GENERAL
# =================================================
@csrf_exempt
@require_POST
def api_gps(request):
    """
    API exclusiva para:
    - Guardar ubicación GPS (histórico)
    - Mantener ubicación actual por vehículo
    - Alimentar mapa en tiempo real
    - Reforzar heartbeat
    ❌ NO marca puntos de control
    """

    # ---------------------------------------------
    # 🔐 LEER TOKEN DESDE HEADER
    # ---------------------------------------------
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse(
            {"error": "Token no enviado"},
            status=401
        )

    token = auth.replace("Bearer ", "").strip()

    sesion = obtener_sesion_valida(token)
    if not sesion:
        return JsonResponse(
            {"error": "Sesión inválida o reemplazada"},
            status=401
        )

    # ---------------------------------------------
    # 📥 LEER JSON
    # ---------------------------------------------
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse(
            {"error": "JSON inválido"},
            status=400
        )

    lat = data.get("lat")
    lng = data.get("lng")

    if lat is None or lng is None:
        return JsonResponse(
            {"error": "Latitud y longitud requeridas"},
            status=400
        )

    # ---------------------------------------------
    # 🧪 NORMALIZAR DATOS
    # ---------------------------------------------
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "Latitud o longitud inválidas"},
            status=400
        )

    velocidad = data.get("velocidad")
    precision = data.get("precision")
    bateria = data.get("bateria")

    try:
        velocidad = float(velocidad) if velocidad is not None else 0
    except (TypeError, ValueError):
        velocidad = 0

    try:
        precision = float(precision) if precision is not None else None
    except (TypeError, ValueError):
        precision = None

    try:
        bateria = int(bateria) if bateria is not None else None
    except (TypeError, ValueError):
        bateria = None

    # ---------------------------------------------
    # 📜 HISTÓRICO GPS (AUDITORÍA)
    # ---------------------------------------------
    GPSRegistro.objects.create(
        sesion=sesion,
        lat=lat,
        lng=lng,
        velocidad=velocidad,
        precision=precision,
        bateria=bateria
    )

    # ---------------------------------------------
    # 🛑 DETECTOR DE PARADAS
    # ---------------------------------------------
    procesar_parada(
        vehiculo=sesion.vehiculo,
        lat=lat,
        lng=lng,
        velocidad=velocidad,
        timestamp=timezone.now()
    )

    # =====================================================
    # 🔴 AGREGADO — FILTRO GPS BASURA (CLAVE REAL)
    # =====================================================
    # Si la precisión es mala (>100m), NO actualizamos mapa
    # Esto evita ubicaciones falsas (Lima, capital, red)
    if precision is not None and precision > 100:
        # Aún así reforzamos heartbeat
        sesion.last_heartbeat = timezone.now()
        sesion.save(update_fields=["last_heartbeat"])

        return JsonResponse({
            "ok": True,
            "vehiculo": sesion.vehiculo.codigo,
            "lat": lat,
            "lng": lng,
            "precision": precision,
            "descartado": True,
            "motivo": "GPS con baja precisión"
        })

    # ---------------------------------------------
    # 📍 UBICACIÓN ACTUAL (MAPA EN TIEMPO REAL)
    # ---------------------------------------------
    UbicacionVehiculo.objects.update_or_create(
        vehiculo=sesion.vehiculo,
        defaults={
            "latitud": lat,
            "longitud": lng,
            "velocidad": velocidad,
            "precision": precision,
        }
    )

    # ---------------------------------------------
    # ❌ MARCACIÓN DE PUNTOS DESACTIVADA
    # ---------------------------------------------
    # 👉 La marcación SOLO se hace en api_gps_conductor

    # ---------------------------------------------
    # 🫀 HEARTBEAT (SESIÓN ACTIVA)
    # ---------------------------------------------
    sesion.last_heartbeat = timezone.now()
    sesion.save(update_fields=["last_heartbeat"])

    # ---------------------------------------------
    # ✅ RESPUESTA FINAL
    # ---------------------------------------------
    return JsonResponse({
        "ok": True,
        "vehiculo": sesion.vehiculo.codigo,
        "lat": lat,
        "lng": lng,
        "precision": precision,
        "timestamp": timezone.now().isoformat()
    })


# =================================================
# 🫀 HEARTBEAT
# =================================================
@csrf_exempt
@require_POST
def api_heartbeat(request):
    """
    Heartbeat oficial de la App Conductor.

    ✔ Autenticación unificada por Authorization: Bearer
    ✔ Actualiza last_heartbeat
    ✔ Devuelve mensaje global activo (si existe)
    ✔ Sin cache
    """

    # =============================================
    # 🔐 TOKEN DESDE HEADER (UNIFICADO)
    # =============================================
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        response = JsonResponse(
            {
                "ok": False,
                "estado": "BLOQUEADO",
                "motivo": "TOKEN_REQUERIDO"
            },
            status=401
        )
        return _disable_cache(response)

    token = auth.replace("Bearer ", "").strip()

    # =============================================
    # 🔐 VALIDACIÓN CENTRAL DE SESIÓN
    # =============================================
    sesion = obtener_sesion_valida(token)

    if not sesion:
        response = JsonResponse(
            {
                "ok": False,
                "estado": "BLOQUEADO",
                "motivo": "SESION_INVALIDA"
            },
            status=403
        )
        return _disable_cache(response)

    # =============================================
    # 🫀 REGISTRAR HEARTBEAT
    # =============================================
    ahora = timezone.now()
    sesion.last_heartbeat = ahora
    sesion.save(update_fields=["last_heartbeat"])

    # =============================================
    # 📢 MENSAJE GLOBAL ACTIVO
    # =============================================
    hoy = timezone.localdate()

    mensaje = (
        MensajeGlobal.objects
        .filter(
            activo=True,
            fecha_inicio__lte=hoy,
            fecha_fin__gte=hoy
        )
        .order_by("-updated_at", "-id")
        .only("id", "texto", "updated_at", "creado_en")
        .first()
    )

    # =============================================
    # 📤 RESPUESTA FINAL (CONTRATO FIJO)
    # =============================================
    respuesta = {
        "ok": True,
        "estado": "ACTIVO",
        "timestamp": ahora.isoformat(),
        "mensaje": None
    }

    if mensaje:
        respuesta["mensaje"] = {
            "id": mensaje.id,
            "texto": mensaje.texto,
            "actualizado_en": (
                mensaje.updated_at.isoformat()
                if mensaje.updated_at
                else mensaje.creado_en.isoformat()
            )
        }

    response = JsonResponse(respuesta)
    return _disable_cache(response)

# =================================================
# 🚫 DESACTIVAR CACHE HTTP (FUNCIÓN CENTRAL)
# =================================================
def _disable_cache(response):
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


# =================================================
# 📱 ESTADO APP
# =================================================
@csrf_exempt
def api_app_estado(request):
    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido"}, status=405)

    # =============================================
    # 🔑 LEER TOKEN
    # =============================================
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return JsonResponse({
            "autorizado": False,
            "estado": "BLOQUEADO",
            "estado_gps": "BLOQUEADO",
            "bloqueado": True,
            "mensaje": "Token no enviado"
        })

    token = auth.replace("Bearer ", "").strip()

    # =============================================
    # 🔐 VALIDAR SESIÓN
    # =============================================
    sesion = obtener_sesion_valida(token)
    if not sesion:
        return JsonResponse({
            "autorizado": False,
            "estado": "BLOQUEADO",
            "estado_gps": "BLOQUEADO",
            "bloqueado": True,
            "mensaje": "Sesión inválida"
        })

    estado_gps = calcular_estado_sesion(sesion)

    # =============================================
    # 📅 FECHA OPERATIVA
    # =============================================
    hoy = timezone.localdate()

    # =============================================
    # 🚍 SALIDA ACTIVA CORRECTA (ORDEN CLAVE)
    # =============================================
    salida = (
        RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa)
        .filter(
            vehiculo=sesion.vehiculo,
            fecha=hoy,
            activo=True
        )
        .order_by(
            "-en_cola",      # 🔥 PRIORIDAD REAL
            "orden_cola",
            "hora_salida"
        )
        .first()
    )

    # =============================================
    # 🚫 SIN SALIDA
    # =============================================
    if not salida:
        return JsonResponse({
            "autorizado": True,
            "estado": "SIN_SALIDA",
            "estado_gps": estado_gps,
            "bloqueado": False,
            "hora_salida": None,
            "mensaje": "Espere orden de salida"
        })

    # =============================================
    # ⏳ SIN HORA
    # =============================================
    if not salida.hora_salida:
        return JsonResponse({
            "autorizado": True,
            "estado": "SIN_HORA",
            "estado_gps": estado_gps,
            "bloqueado": False,
            "hora_salida": None,
            "mensaje": "Esperando asignación de hora"
        })

    # =============================================
    # ⏱️ TIEMPOS
    # =============================================
    tz = timezone.get_current_timezone()
    ahora = timezone.localtime(timezone.now(), tz)
    hora_salida = timezone.localtime(salida.hora_salida, tz)

    # 🔒 BLINDAJE DE FECHA
    if hora_salida.date() != hoy:
        return JsonResponse({
            "autorizado": True,
            "estado": "EN_COLA",
            "estado_gps": estado_gps,
            "bloqueado": False,
            "hora_salida": hora_salida.strftime("%H:%M"),
            "mensaje": "Salida programada para otro día"
        })

    segundos = (hora_salida - ahora).total_seconds()
    minutos = max(int(segundos // 60), 0)

    # =============================================
    # 🔴 EN COLA
    # =============================================
    if salida.en_cola:
        if segundos <= 0:
            return JsonResponse({
                "autorizado": True,
                "estado": "SALIDA_ACTIVA",
                "estado_gps": estado_gps,
                "bloqueado": False,
                "hora_salida": hora_salida.strftime("%H:%M"),
                "mensaje": "Salida activa"
            })

        return JsonResponse({
            "autorizado": True,
            "estado": "EN_COLA",
            "estado_gps": estado_gps,
            "bloqueado": False,
            "hora_salida": hora_salida.strftime("%H:%M"),
            "minutos": minutos,
            "mensaje": "Unidad en cola"
        })

    # =============================================
    # 🟢 SALIDA YA INICIADA
    # =============================================
    if salida.hora_real_salida:

        if sesion.salida_id != salida.id:
            sesion.salida = salida
            sesion.save(update_fields=["salida"])

        return JsonResponse({
            "autorizado": True,
            "estado": "SALIDA_ACTIVA",
            "estado_gps": estado_gps,
            "bloqueado": False,
            "hora_salida": hora_salida.strftime("%H:%M"),
            "mensaje": "Salida activa"
        })

    # =============================================
    # 🟡 FALLBACK SEGURO
    # =============================================
    return JsonResponse({
        "autorizado": True,
        "estado": "EN_COLA",
        "estado_gps": estado_gps,
        "bloqueado": False,
        "hora_salida": hora_salida.strftime("%H:%M"),
        "mensaje": "Unidad en cola"
    })


# =================================================
# 🚍 MAPA DESPACHADOR
# =================================================
@login_required
@empresa_required
@require_GET
def api_despachador_mapa(request):
    """
    API optimizada:
    - Evita N+1 queries
    - Solo 2 consultas a DB
    - Escalable para muchas unidades
    """

    ahora = timezone.now()
    hoy = timezone.localdate()

    # 🏢 EMPRESA DESDE MIDDLEWARE
    empresa = request.empresa

    data = []

    # =================================================
    # 🔥 1. OBTENER VEHÍCULOS CON SALIDA ACTIVA (1 QUERY)
    # =================================================
    salidas_activas = set(
        RegistroSalida.objects.for_empresa(empresa).filter(
            fecha=hoy,
            activo=True,
        ).values_list("vehiculo_id", flat=True)
    )

    # =================================================
    # 🔥 2. OBTENER UBICACIONES (1 QUERY)
    # =================================================
    ubicaciones = (
        UbicacionVehiculo.objects.for_empresa(empresa)
        .filter(updated_at__gte=ahora - timedelta(minutes=10))
        .select_related("vehiculo")
    )

    # =================================================
    # 🚍 RECORRER UBICACIONES (SIN MÁS QUERIES)
    # =================================================
    for ub in ubicaciones:

        delta = ahora - ub.updated_at

        # 📡 ESTADO GPS
        if delta <= timedelta(seconds=30):
            estado_gps = "ONLINE"
        elif delta <= timedelta(seconds=120):
            estado_gps = "LENTO"
        else:
            estado_gps = "OFFLINE"

        # 🚍 ESTADO OPERATIVO
        estado = "ACTIVO" if ub.vehiculo_id in salidas_activas else "INACTIVO"

        data.append({
            "vehiculo": str(ub.vehiculo.codigo),

            "lat": ub.latitud,
            "lng": ub.longitud,
            "velocidad": ub.velocidad,
            "precision": ub.precision,

            "estado": estado,
            "estado_gps": estado_gps,

            "actualizado_en": ub.updated_at.isoformat(),
        })

    return JsonResponse(data, safe=False)