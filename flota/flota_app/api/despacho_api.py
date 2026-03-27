from datetime import datetime, timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.contrib.auth.decorators import login_required

from ..decorators import empresa_required

# 🔥 SELECTORS GPS
from ..selectors.gps_selector import (
    get_ubicaciones_empresa,
)

# 🔥 SELECTORS VEHICULO
from ..selectors.vehiculo_selector import (
    obtener_vehiculo_por_codigo,
)

# 🔥 SELECTORS SALIDA
from ..selectors.salida_selector import (
    get_salidas_por_fecha,
    get_salidas_activas,
)

# 🔥 SELECTORS PARADAS
from ..selectors.parada_selector import (
    obtener_paradas_por_fecha,
)

from ..models import (
    PuntoControl,
    ConfiguracionDespacho,
)


# =================================================
# 🗺️ MAPA TIEMPO REAL
# =================================================
@login_required
@empresa_required
@require_GET
def api_despachador_mapa(request):

    ahora = timezone.now()
    hoy = timezone.localdate()
    empresa = request.empresa

    salidas_activas = set(
        get_salidas_activas(empresa, hoy)
        .values_list("vehiculo_id", flat=True)
    )

    ubicaciones = get_ubicaciones_empresa(empresa, ahora)

    data = []
    t30 = timedelta(seconds=30)
    t120 = timedelta(seconds=120)

    for ub in ubicaciones:

        delta = ahora - ub.updated_at

        if delta <= t30:
            estado_gps = "ONLINE"
        elif delta <= t120:
            estado_gps = "LENTO"
        else:
            estado_gps = "OFFLINE"

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


# =================================================
# 📍 PUNTOS DE CONTROL
# =================================================
@login_required
@empresa_required
@require_GET
def api_puntos_control(request):

    empresa = request.empresa

    puntos = (
        PuntoControl.objects.for_empresa(empresa)
        .filter(activo=True)
        .order_by("orden")
    )

    data = [
        {
            "id": p.id,
            "codigo": p.codigo,
            "nombre": p.nombre,
            "orden": p.orden,
            "lat": float(p.latitud),
            "lng": float(p.longitud),
            "radio": p.radio_metros,
        }
        for p in puntos
        if p.latitud is not None and p.longitud is not None
    ]

    return JsonResponse(data, safe=False)


# =================================================
# 🔎 BUSCAR VEHÍCULO
# =================================================
@login_required
@empresa_required
@require_GET
def api_buscar_vehiculo_por_codigo(request):

    codigo = request.GET.get("codigo", "").strip()

    if not codigo:
        return JsonResponse({"error": "codigo requerido"}, status=400)

    empresa = request.empresa

    vehiculo = obtener_vehiculo_por_codigo(empresa, codigo)

    if not vehiculo:
        return JsonResponse({"error": "No encontrado"}, status=404)

    return JsonResponse({
        "vehiculo_id": vehiculo.id,
        "codigo": vehiculo.codigo,
        "placa": vehiculo.placa,
    })


# =================================================
# 🧭 RECORRIDO HISTÓRICO
# =================================================
@login_required
@empresa_required
@require_GET
def api_recorrido_vehiculo(request):

    vehiculo_id = request.GET.get("vehiculo")
    fecha = request.GET.get("fecha")

    if not vehiculo_id or not fecha:
        return JsonResponse({"error": "Parámetros incompletos"}, status=400)

    try:
        vehiculo_id = int(vehiculo_id)
        fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return JsonResponse({"error": "Parámetros inválidos"}, status=400)

    empresa = request.empresa

    salidas = list(
        get_salidas_por_fecha(empresa, vehiculo_id, fecha_dt)
    )

    if not salidas:
        return JsonResponse([], safe=False)

    data = []

    for i, salida in enumerate(salidas):

        if not salida.hora_real_salida:
            continue

        inicio = salida.hora_real_salida

        if i + 1 < len(salidas) and salidas[i + 1].hora_real_salida:
            fin = salidas[i + 1].hora_real_salida
        else:
            fin = timezone.now()

        registros = salida.get_gps_rango(inicio, fin)

        for r in registros:
            data.append({
                "lat": r.lat,
                "lng": r.lng,
                "hora": r.timestamp.strftime("%H:%M:%S"),
            })

    return JsonResponse(data, safe=False)


# =================================================
# 🛑 PARADAS
# =================================================
@login_required
@empresa_required
@require_GET
def api_paradas_vehiculo(request):

    vehiculo_id = request.GET.get("vehiculo")
    fecha = request.GET.get("fecha")

    if not vehiculo_id or not fecha:
        return JsonResponse({"error": "Parámetros incompletos"}, status=400)

    try:
        vehiculo_id = int(vehiculo_id)
        fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return JsonResponse({"error": "Parámetros inválidos"}, status=400)

    empresa = request.empresa

    paradas = obtener_paradas_por_fecha(empresa, vehiculo_id, fecha_dt)

    data = [{
        "lat": p.lat,
        "lng": p.lng,
        "inicio": p.inicio.strftime("%H:%M:%S"),
        "duracion_min": int(p.duracion_segundos / 60),
    } for p in paradas]

    return JsonResponse(data, safe=False)


# =================================================
# 📊 PANEL FRECUENCIA
# =================================================
@login_required
@empresa_required
@require_GET
def api_panel_frecuencia(request):

    hoy = timezone.localdate()
    empresa = request.empresa

    puntos = list(
        PuntoControl.objects.for_empresa(empresa)
        .filter(activo=True)
        .order_by("orden")
    )

    if not puntos:
        return JsonResponse({"puntos": [], "data": []})

    max_orden = max(p.orden for p in puntos)

    config = ConfiguracionDespacho.objects.filter(
        activa=True,
        empresa=empresa
    ).first()

    intervalo = config.intervalo_fijo if config and config.intervalo_fijo else 6

    salidas = get_salidas_activas(empresa, hoy)

    unidades = []

    for salida in salidas:

        if salida.ultimo_punto_orden == max_orden:
            salida.cerrar()
            continue

        unidades.append(salida.to_panel_dict(puntos))

    unidades.sort(key=lambda x: x["avance"], reverse=True)

    for i in range(1, len(unidades)):
        actual = unidades[i]
        anterior = unidades[i - 1]

        if actual["ultimo_tiempo"] and anterior["ultimo_tiempo"]:
            diff = (
                actual["ultimo_tiempo"] - anterior["ultimo_tiempo"]
            ).total_seconds() / 60

            actual["frecuencia"] = int(diff)

            if diff > intervalo * 1.5:
                actual["hueco"] = True

            if diff < intervalo * 0.5:
                actual["pegado"] = True

    if unidades:
        unidades[0]["lider"] = True
        for u in unidades[1:]:
            u["lider"] = False

    return JsonResponse({
        "puntos": [p.codigo for p in puntos],
        "data": unidades
    })


# =================================================
# 🧪 DEBUG GPS
# =================================================
@login_required
@empresa_required
def debug_gps(request):

    empresa = request.empresa

    ubicaciones = get_ubicaciones_empresa(empresa, timezone.now())

    data = [
        {
            "vehiculo": u.vehiculo.codigo,
            "lat": u.latitud,
            "lng": u.longitud,
            "updated_at": u.updated_at
        }
        for u in ubicaciones
    ]

    return JsonResponse(data, safe=False)