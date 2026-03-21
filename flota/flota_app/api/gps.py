import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

from ..models import RegistroSalida
from ..services import validar_sesion
from ..services.gps_service import procesar_gps_conductor


@csrf_exempt
def api_gps_conductor(request):

    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido"}, status=405)

    # 🔑 TOKEN
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

    hoy = timezone.localdate()

    # 🔥 ESTO SE QUEDA AQUÍ (IMPORTANTE)
    RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa).filter(
        vehiculo=sesion.vehiculo,
        activo=True,
        fecha__lt=hoy
    ).update(
        activo=False,
        en_cola=False
    )

    # 📦 JSON
    try:
        data = json.loads(request.body or "{}")
    except:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    lat = data.get("lat")
    lng = data.get("lng")
    precision = data.get("precision")

    if lat is None or lng is None:
        return JsonResponse({"accion": "ninguna"})

    try:
        lat = float(lat)
        lng = float(lng)
    except:
        return JsonResponse({"accion": "ninguna"})

    # 👉 AQUÍ LLAMAMOS AL SERVICE
    result = procesar_gps_conductor(
        sesion=sesion,
        lat=lat,
        lng=lng,
        precision=precision
    )

    return JsonResponse(result)