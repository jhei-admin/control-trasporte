from datetime import timedelta
import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache

from ..services.sesion_service import validar_sesion

# 🔥 SELECTORS GPS
from ..selectors.gps_selector import (
    get_ultimo_gps,
    crear_gps,
    actualizar_ubicacion,
)

# 🔥 SELECTOR PARADAS
from ..selectors.parada_selector import (
    obtener_parada_activa
)


# =================================================
# 📡 API GPS (LIMPIO 🔥)
# =================================================
@csrf_exempt
@require_POST
@never_cache
def api_gps(request):

    # ================= TOKEN =================
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"error": "Token no enviado"}, status=401)

    token = auth.replace("Bearer ", "").strip()

    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({"error": "Sesión inválida"}, status=401)

    # ================= JSON =================
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    lat = data.get("lat")
    lng = data.get("lng")

    if lat is None or lng is None:
        return JsonResponse({"error": "Lat/Lng requeridos"}, status=400)

    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Lat/Lng inválidos"}, status=400)

    # ================= OPCIONALES =================
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

    ahora = timezone.now()

    # =================================================
    # 📜 GPS HISTÓRICO (SELECTOR 🔥)
    # =================================================
    ultimo = get_ultimo_gps(sesion)

    if not ultimo or (ahora - ultimo.timestamp) >= timedelta(seconds=5):
        crear_gps(
            sesion=sesion,
            lat=lat,
            lng=lng,
            velocidad=velocidad,
            precision=precision,
            bateria=bateria
        )

    # =================================================
    # 🔴 FILTRO GPS BASURA
    # =================================================
    if precision is not None and precision > 100:
        sesion.last_heartbeat = ahora
        sesion.save(update_fields=["last_heartbeat"])

        return JsonResponse({
            "ok": True,
            "vehiculo": sesion.vehiculo.codigo,
            "descartado": True,
            "motivo": "GPS baja precisión"
        })
    
    # =================================================
    # 🛑 PARADAS (SELECTOR 🔥)
    # =================================================
    obtener_parada_activa(
        vehiculo=sesion.vehiculo,
        lat=lat,
        lng=lng,
        velocidad=velocidad,
        timestamp=ahora
    )

    # =================================================
    # 📍 UBICACIÓN ACTUAL (SELECTOR 🔥)
    # =================================================
    actualizar_ubicacion(
        vehiculo=sesion.vehiculo,
        lat=lat,
        lng=lng,
        velocidad=velocidad,
        precision=precision
    )

    # =================================================
    # 🫀 HEARTBEAT
    # =================================================
    sesion.last_heartbeat = ahora
    sesion.save(update_fields=["last_heartbeat"])

    # =================================================
    # ✅ RESPUESTA FINAL
    # =================================================
    return JsonResponse({
        "ok": True,
        "vehiculo": sesion.vehiculo.codigo,
        "lat": lat,
        "lng": lng,
        "precision": precision,
        "timestamp": ahora.isoformat()
    })