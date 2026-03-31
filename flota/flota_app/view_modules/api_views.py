from datetime import datetime, timedelta
import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import IntegrityError
from django.db.models import Max, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from ..decorators import empresa_required
from ..models import (
    ConfiguracionDespacho,
    GPSRegistro,
    MarcacionPunto,
    MensajeGlobal,
    Parada,
    PuntoControl,
    RegistroSalida,
    SesionUnidad,
    UbicacionVehiculo,
    Vehiculo,
)
from ..services import calcular_estado_sesion, validar_sesion
from ..utils import distancia_metros

__all__ = [
    "api_app_cola_contexto",
    "api_app_estado",
    "api_app_referencia_tiempo",
    "api_buscar_vehiculo_por_codigo",
    "api_despachador_mapa",
    "api_escanear_qr",
    "api_gps",
    "api_gps_conductor",
    "api_heartbeat",
    "api_panel_frecuencia",
    "api_paradas_vehiculo",
    "api_puntos_control",
    "api_recorrido_vehiculo",
    "debug_gps",
]


TIEMPO_MIN_PARADA = 120
TIEMPO_PARADA_PROLONGADA = 300
VEL_DETENIDO = 1
RADIO_METROS = 20
GPS_SAVE_INTERVAL = timedelta(
    seconds=max(getattr(settings, "GPS_SAVE_INTERVAL_SECONDS", 5), 1)
)
GPS_MAX_PRECISION = getattr(settings, "GPS_MAX_PRECISION", 100.0)


def actualizar_heartbeat(sesion, ahora):
    sesion.last_heartbeat = ahora
    SesionUnidad.objects.filter(pk=sesion.pk).update(last_heartbeat=ahora)


def guardar_ubicacion_actual(sesion, ahora, lat, lng, velocidad=None, precision=None):
    defaults = {
        "latitud": lat,
        "longitud": lng,
        "updated_at": ahora,
    }
    if velocidad is not None:
        defaults["velocidad"] = velocidad
    if precision is not None:
        defaults["precision"] = precision

    actualizados = (
        UbicacionVehiculo.objects
        .filter(vehiculo=sesion.vehiculo)
        .update(**defaults)
    )
    if actualizados:
        return

    try:
        UbicacionVehiculo.objects.create(
            vehiculo=sesion.vehiculo,
            **defaults,
        )
    except IntegrityError:
        UbicacionVehiculo.objects.filter(vehiculo=sesion.vehiculo).update(**defaults)


def registrar_gps_historico_si_corresponde(
    sesion,
    ahora,
    lat,
    lng,
    velocidad=None,
    precision=None,
    bateria=None,
):
    ultimo_gps = (
        GPSRegistro.objects.filter(sesion=sesion)
        .only("timestamp")
        .order_by("-timestamp")
        .first()
    )
    if ultimo_gps and (ahora - ultimo_gps.timestamp) < GPS_SAVE_INTERVAL:
        return False

    payload = {
        "sesion": sesion,
        "lat": lat,
        "lng": lng,
    }
    if velocidad is not None:
        payload["velocidad"] = velocidad
    if precision is not None:
        payload["precision"] = precision
    if bateria is not None:
        payload["bateria"] = bateria

    GPSRegistro.objects.create(**payload)
    return True


@csrf_exempt
def api_gps_conductor(request):
    if request.method != "POST":
        return JsonResponse({"error": "Metodo no permitido"}, status=405)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"accion": "ignorar"})

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({
            "accion": "bloqueado",
            "mensaje": "Sesion invalida o reemplazada",
        })

    hoy = timezone.localdate()

    RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa).filter(
        vehiculo=sesion.vehiculo,
        activo=True,
        fecha__lt=hoy,
    ).update(activo=False, en_cola=False)

    try:
        data = json.loads(request.body or "{}")
    except Exception:
        return JsonResponse({"error": "JSON invalido"}, status=400)

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

    ahora = timezone.now()

    if precision is not None and precision > GPS_MAX_PRECISION:
        actualizar_heartbeat(sesion, ahora)
        return JsonResponse({"accion": "ninguna"})

    guardar_ubicacion_actual(
        sesion=sesion,
        ahora=ahora,
        lat=lat,
        lng=lng,
        precision=precision,
    )

    registrar_gps_historico_si_corresponde(
        sesion=sesion,
        ahora=ahora,
        lat=lat,
        lng=lng,
        precision=precision,
    )
    actualizar_heartbeat(sesion, ahora)

    salida = (
        RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa)
        .filter(vehiculo=sesion.vehiculo, fecha=hoy, activo=True)
        .order_by("-id")
        .first()
    )
    if not salida:
        return JsonResponse({"accion": "ninguna"})

    if salida.ruta and not salida.marcaciones.exists():
        puntos = PuntoControl.objects.filter(ruta=salida.ruta, activo=True).order_by("orden")
        for punto in puntos:
            MarcacionPunto.objects.get_or_create(registro_salida=salida, punto=punto)

    marcacion = salida.siguiente_marcacion()
    if not marcacion:
        if not salida.hora_real_salida:
            return JsonResponse({"accion": "ninguna"})

        salida.activo = False
        salida.en_cola = False
        salida.save(update_fields=["activo", "en_cola"])
        return JsonResponse({"accion": "audio", "audio": "ruta_completada"})

    punto = marcacion.punto
    distancia = distancia_metros(lat, lng, float(punto.latitud), float(punto.longitud))
    if distancia > punto.radio_metros:
        return JsonResponse({"accion": "ninguna"})

    if marcacion.hora_marcada:
        delta = ahora - marcacion.hora_marcada
        if delta.total_seconds() < 10:
            return JsonResponse({"accion": "ninguna"})

    if not salida.hora_real_salida:
        salida.hora_real_salida = ahora
        salida.en_cola = False
        salida.activo = True
        salida.save(update_fields=["hora_real_salida", "en_cola", "activo"])
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
            "diferencia_min": marcacion.diferencia_minutos,
        },
    })


def procesar_parada(vehiculo, lat, lng, velocidad, timestamp):
    parada = (
        Parada.objects.for_empresa(vehiculo.empresa)
        .filter(vehiculo=vehiculo, activa=True)
        .order_by("-inicio")
        .first()
    )

    if velocidad <= VEL_DETENIDO:
        if not parada:
            Parada.objects.create(vehiculo=vehiculo, lat=lat, lng=lng, inicio=timestamp)
            return

        distancia = distancia_metros(parada.lat, parada.lng, lat, lng)
        if distancia > RADIO_METROS:
            parada.cerrar(timestamp)
            Parada.objects.create(vehiculo=vehiculo, lat=lat, lng=lng, inicio=timestamp)
            return

        duracion = (timestamp - parada.inicio).total_seconds()
        if not parada.es_prolongada and duracion >= TIEMPO_PARADA_PROLONGADA:
            parada.es_prolongada = True
            parada.save(update_fields=["es_prolongada"])
    elif parada:
        duracion = (timestamp - parada.inicio).total_seconds()
        if duracion < TIEMPO_MIN_PARADA:
            parada.delete()
        else:
            parada.cerrar(timestamp)


@csrf_exempt
@require_POST
def api_gps(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"error": "Token no enviado"}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({"error": "Sesion invalida o reemplazada"}, status=401)

    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON invalido"}, status=400)

    lat = data.get("lat")
    lng = data.get("lng")
    if lat is None or lng is None:
        return JsonResponse({"error": "Latitud y longitud requeridas"}, status=400)

    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Latitud o longitud invalidas"}, status=400)

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

    procesar_parada(
        vehiculo=sesion.vehiculo,
        lat=lat,
        lng=lng,
        velocidad=velocidad,
        timestamp=ahora,
    )

    if precision is not None and precision > GPS_MAX_PRECISION:
        actualizar_heartbeat(sesion, ahora)
        return JsonResponse({
            "ok": True,
            "vehiculo": sesion.vehiculo.codigo,
            "lat": lat,
            "lng": lng,
            "precision": precision,
            "descartado": True,
            "motivo": "GPS con baja precision",
        })

    guardar_ubicacion_actual(
        sesion=sesion,
        ahora=ahora,
        lat=lat,
        lng=lng,
        velocidad=velocidad,
        precision=precision,
    )

    registrar_gps_historico_si_corresponde(
        sesion=sesion,
        ahora=ahora,
        lat=lat,
        lng=lng,
        velocidad=velocidad,
        precision=precision,
        bateria=bateria,
    )
    actualizar_heartbeat(sesion, ahora)

    return JsonResponse({
        "ok": True,
        "vehiculo": sesion.vehiculo.codigo,
        "lat": lat,
        "lng": lng,
        "precision": precision,
        "timestamp": ahora.isoformat(),
    })


@login_required
@empresa_required
@require_GET
def api_despachador_mapa(request):
    ahora = timezone.now()
    hoy = timezone.localdate()
    empresa = request.empresa

    salidas_activas = set(
        RegistroSalida.objects.for_empresa(empresa)
        .filter(fecha=hoy, activo=True)
        .values_list("vehiculo_id", flat=True)
    )

    ubicaciones = list(
        UbicacionVehiculo.objects.for_empresa(empresa)
        .filter(updated_at__gte=ahora - timedelta(minutes=10))
        .values(
            "vehiculo_id",
            "vehiculo__codigo",
            "latitud",
            "longitud",
            "velocidad",
            "precision",
            "updated_at",
        )
    )

    data = []
    for ubicacion in ubicaciones:
        actualizado_en = ubicacion["updated_at"]
        delta = ahora - actualizado_en
        if delta <= timedelta(seconds=30):
            estado_gps = "ONLINE"
        elif delta <= timedelta(seconds=120):
            estado_gps = "LENTO"
        else:
            estado_gps = "OFFLINE"

        data.append({
            "vehiculo_id": ubicacion["vehiculo_id"],
            "vehiculo": str(ubicacion["vehiculo__codigo"]),
            "lat": ubicacion["latitud"],
            "lng": ubicacion["longitud"],
            "velocidad": ubicacion["velocidad"],
            "precision": ubicacion["precision"],
            "estado": "ACTIVO" if ubicacion["vehiculo_id"] in salidas_activas else "INACTIVO",
            "estado_gps": estado_gps,
            "fecha": actualizado_en.isoformat(),
            "actualizado_en": actualizado_en.isoformat(),
        })

    return JsonResponse(data, safe=False)


@login_required
@empresa_required
@require_GET
def api_puntos_control(request):
    empresa = request.empresa
    puntos = (
        PuntoControl.objects.for_empresa(empresa)
        .filter(activo=True)
        .select_related("ruta")
        .order_by("ruta_id", "orden")
    )
    data = []

    for punto in puntos:
        if punto.latitud is None or punto.longitud is None:
            continue
        data.append({
            "id": punto.id,
            "codigo": punto.codigo,
            "nombre": punto.nombre,
            "orden": punto.orden,
            "ruta_id": punto.ruta_id,
            "ruta": punto.ruta.nombre if punto.ruta else "",
            "lat": float(punto.latitud),
            "lng": float(punto.longitud),
            "radio": punto.radio_metros,
        })

    return JsonResponse(data, safe=False)


@login_required
@empresa_required
@require_GET
def api_buscar_vehiculo_por_codigo(request):
    codigo = request.GET.get("codigo", "").strip()
    if not codigo:
        return JsonResponse({"error": "codigo requerido"}, status=400)

    empresa = request.empresa
    qs = Vehiculo.objects.for_empresa(empresa).filter(codigo=codigo, activo=True)
    if not qs.exists():
        return JsonResponse({"error": f"No existe unidad activa con codigo {codigo}"}, status=404)
    if qs.count() > 1:
        return JsonResponse({"error": f"Conflicto: mas de una unidad activa con codigo {codigo}"}, status=409)

    vehiculo = qs.first()
    return JsonResponse({
        "vehiculo_id": vehiculo.id,
        "codigo": vehiculo.codigo,
        "placa": vehiculo.placa,
        "activo": vehiculo.activo,
    })


@login_required
@empresa_required
@require_GET
def api_recorrido_vehiculo(request):
    vehiculo_id = request.GET.get("vehiculo")
    fecha = request.GET.get("fecha")
    if not vehiculo_id or not fecha:
        return JsonResponse({"error": "Parametros incompletos"}, status=400)

    try:
        fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"error": "Fecha invalida"}, status=400)

    empresa = request.empresa
    salidas = list(
        RegistroSalida.objects.for_empresa(empresa)
        .filter(vehiculo_id=vehiculo_id, fecha=fecha_dt)
        .order_by("hora_real_salida")
    )

    if not salidas:
        return JsonResponse([], safe=False)

    data = []
    for index, salida in enumerate(salidas):
        if not salida.hora_real_salida:
            continue

        inicio = salida.hora_real_salida
        fin = salidas[index + 1].hora_real_salida if index + 1 < len(salidas) and salidas[index + 1].hora_real_salida else timezone.now()
        registros = GPSRegistro.objects.filter(
            sesion__vehiculo_id=vehiculo_id,
            timestamp__gte=inicio,
            timestamp__lte=fin,
        ).order_by("timestamp")

        for registro in registros:
            data.append({
                "lat": registro.lat,
                "lng": registro.lng,
                "hora": registro.timestamp.strftime("%H:%M:%S"),
                "velocidad": registro.velocidad or 0,
            })

    return JsonResponse(data, safe=False)


@login_required
@empresa_required
@require_GET
def api_paradas_vehiculo(request):
    vehiculo_id = request.GET.get("vehiculo")
    fecha = request.GET.get("fecha")
    if not vehiculo_id or not fecha:
        return JsonResponse({"error": "Parametros incompletos"}, status=400)

    try:
        fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"error": "Fecha invalida"}, status=400)

    empresa = request.empresa
    paradas = (
        Parada.objects.for_empresa(empresa)
        .filter(vehiculo_id=vehiculo_id, inicio__date=fecha_dt)
        .order_by("inicio")
    )

    data = []
    for parada in paradas:
        data.append({
            "lat": parada.lat,
            "lng": parada.lng,
            "inicio": parada.inicio.strftime("%H:%M:%S"),
            "fin": parada.fin.strftime("%H:%M:%S") if parada.fin else None,
            "duracion_min": int(parada.duracion_segundos / 60),
            "activa": parada.activa,
        })

    return JsonResponse(data, safe=False)


def _disable_cache(response):
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


@csrf_exempt
@require_POST
def api_heartbeat(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        response = JsonResponse(
            {"ok": False, "estado": "BLOQUEADO", "motivo": "TOKEN_REQUERIDO"},
            status=401,
        )
        return _disable_cache(response)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        response = JsonResponse(
            {"ok": False, "estado": "BLOQUEADO", "motivo": "SESION_INVALIDA"},
            status=403,
        )
        return _disable_cache(response)

    ahora = timezone.now()
    sesion.last_heartbeat = ahora
    sesion.save(update_fields=["last_heartbeat"])
    hoy = timezone.localdate()

    mensaje = (
        MensajeGlobal.objects.filter(
            activo=True,
            fecha_inicio__lte=hoy,
            fecha_fin__gte=hoy,
        )
        .order_by("-updated_at", "-id")
        .only("id", "texto", "updated_at", "creado_en")
        .first()
    )

    respuesta = {
        "ok": True,
        "estado": "ACTIVO",
        "timestamp": ahora.isoformat(),
        "mensaje": None,
    }
    if mensaje:
        respuesta["mensaje"] = {
            "id": mensaje.id,
            "texto": mensaje.texto,
            "actualizado_en": (
                mensaje.updated_at.isoformat()
                if mensaje.updated_at
                else mensaje.creado_en.isoformat()
            ),
        }

    return _disable_cache(JsonResponse(respuesta))


@csrf_exempt
def api_escanear_qr(request):
    if request.method == "OPTIONS":
        return JsonResponse(
            {},
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Credentials": "true",
            },
        )

    if request.method != "POST":
        return JsonResponse(
            {"ok": False, "error": "Metodo no permitido"},
            status=405,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Credentials": "true",
            },
        )

    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse(
            {"ok": False, "error": "JSON invalido"},
            status=400,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    token_qr = data.get("token")
    vehiculo_id = data.get("vehiculo_id")
    empresa_id = None

    if token_qr:
        try:
            payload = signing.loads(token_qr, salt="qr-unidad")
            vehiculo_id = payload.get("vehiculo_id")
            empresa_id = payload.get("empresa_id")
        except signing.BadSignature:
            return JsonResponse(
                {"ok": False, "error": "QR invalido o manipulado"},
                status=400,
                headers={"Access-Control-Allow-Origin": "*"},
            )

    if not vehiculo_id:
        return JsonResponse(
            {"ok": False, "error": "vehiculo_id o token requeridos"},
            status=400,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    vehiculos = Vehiculo.objects.filter(id=vehiculo_id, activo=True).select_related("empresa")
    if empresa_id is not None:
        vehiculos = vehiculos.filter(empresa_id=empresa_id)

    vehiculo = vehiculos.first()
    if not vehiculo:
        return JsonResponse(
            {"ok": False, "error": "Unidad no registrada"},
            status=200,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    SesionUnidad.objects.filter(vehiculo=vehiculo, activa=True).update(activa=False)
    sesion = SesionUnidad.objects.create(vehiculo=vehiculo, activa=True)

    return JsonResponse(
        {
            "ok": True,
            "token": str(sesion.token),
            "vehiculo_id": vehiculo.id,
            "unidad": vehiculo.codigo,
        },
        status=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Credentials": "true",
        },
    )


@csrf_exempt
def api_app_estado(request):
    if request.method != "POST":
        return JsonResponse({"error": "Metodo no permitido"}, status=405)

    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return JsonResponse({
            "autorizado": False,
            "estado": "BLOQUEADO",
            "estado_gps": "BLOQUEADO",
            "bloqueado": True,
            "mensaje": "Token no enviado",
        })

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({
            "autorizado": False,
            "estado": "BLOQUEADO",
            "estado_gps": "BLOQUEADO",
            "bloqueado": True,
            "mensaje": "Sesion invalida",
        })

    estado_gps = calcular_estado_sesion(sesion)
    hoy = timezone.localdate()
    salida = (
        RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa)
        .filter(vehiculo=sesion.vehiculo, fecha=hoy, activo=True)
        .order_by("-en_cola", "orden_cola", "hora_salida")
        .first()
    )

    if not salida:
        return JsonResponse({
            "autorizado": True,
            "estado": "SIN_SALIDA",
            "estado_gps": estado_gps,
            "bloqueado": False,
            "hora_salida": None,
            "mensaje": "Espere orden de salida",
        })

    if not salida.hora_salida:
        return JsonResponse({
            "autorizado": True,
            "estado": "SIN_HORA",
            "estado_gps": estado_gps,
            "bloqueado": False,
            "hora_salida": None,
            "mensaje": "Esperando asignacion de hora",
        })

    tz = timezone.get_current_timezone()
    ahora = timezone.localtime(timezone.now(), tz)
    hora_salida = timezone.localtime(salida.hora_salida, tz)

    if hora_salida.date() != hoy:
        return JsonResponse({
            "autorizado": True,
            "estado": "EN_COLA",
            "estado_gps": estado_gps,
            "bloqueado": False,
            "hora_salida": hora_salida.strftime("%H:%M"),
            "mensaje": "Salida programada para otro dia",
        })

    segundos = (hora_salida - ahora).total_seconds()
    minutos = max(int(segundos // 60), 0)

    if salida.en_cola:
        if segundos <= 0:
            return JsonResponse({
                "autorizado": True,
                "estado": "SALIDA_ACTIVA",
                "estado_gps": estado_gps,
                "bloqueado": False,
                "hora_salida": hora_salida.strftime("%H:%M"),
                "mensaje": "Salida activa",
            })

        return JsonResponse({
            "autorizado": True,
            "estado": "EN_COLA",
            "estado_gps": estado_gps,
            "bloqueado": False,
            "hora_salida": hora_salida.strftime("%H:%M"),
            "minutos": minutos,
            "mensaje": "Unidad en cola",
        })

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
            "mensaje": "Salida activa",
        })

    return JsonResponse({
        "autorizado": True,
        "estado": "EN_COLA",
        "estado_gps": estado_gps,
        "bloqueado": False,
        "hora_salida": hora_salida.strftime("%H:%M"),
        "mensaje": "Unidad en cola",
    })


@require_GET
def api_app_referencia_tiempo(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"ok": False}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({"ok": False}, status=403)

    hoy = timezone.localdate()
    salida = (
        RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa)
        .filter(vehiculo=sesion.vehiculo, fecha=hoy, activo=True)
        .order_by("-id")
        .first()
    )
    if not salida or not salida.hora_salida:
        return JsonResponse({"ok": False})

    tz = timezone.get_current_timezone()
    hora_salida_local = timezone.localtime(salida.hora_salida, tz)
    ultimo_marcado = (
        MarcacionPunto.objects.filter(registro_salida=salida, hora_marcada__isnull=False)
        .select_related("punto")
        .order_by("-punto__orden")
        .first()
    )

    if not ultimo_marcado:
        primer_pendiente = salida.siguiente_marcacion()
        return JsonResponse({
            "ok": True,
            "salida": hora_salida_local.strftime("%H:%M"),
            "actual": {
                "codigo": primer_pendiente.punto.codigo if primer_pendiente else None,
                "diferencia": 0,
                "estado": None,
            },
            "siguiente": None,
        })

    siguiente = (
        MarcacionPunto.objects.filter(
            registro_salida=salida,
            punto__orden__gt=ultimo_marcado.punto.orden,
        )
        .select_related("punto")
        .order_by("punto__orden")
        .first()
    )

    hora_siguiente_local = None
    if siguiente and siguiente.hora_programada:
        hora_siguiente_local = timezone.localtime(siguiente.hora_programada, tz)

    return JsonResponse({
        "ok": True,
        "salida": hora_salida_local.strftime("%H:%M"),
        "actual": {
            "codigo": ultimo_marcado.punto.codigo,
            "diferencia": ultimo_marcado.diferencia_minutos or 0,
            "estado": ultimo_marcado.estado,
        },
        "siguiente": {
            "codigo": siguiente.punto.codigo if siguiente else None,
            "hora": hora_siguiente_local.strftime("%H:%M") if hora_siguiente_local else None,
        },
    })


@require_GET
def api_app_cola_contexto(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"ok": False}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({"ok": False}, status=403)

    ahora = timezone.now()
    hoy = timezone.localdate()
    salida_actual = (
        RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa)
        .select_related("vehiculo", "ruta")
        .filter(
            vehiculo=sesion.vehiculo,
            fecha=hoy,
            activo=True,
            hora_salida__isnull=False,
        )
        .order_by("hora_salida")
        .first()
    )
    if not salida_actual:
        return JsonResponse({"ok": False})

    cola = list(
        RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa)
        .select_related("vehiculo")
        .filter(
            fecha=hoy,
            activo=True,
            ruta=salida_actual.ruta,
            vehiculo__empresa=sesion.vehiculo.empresa,
            hora_salida__isnull=False,
        )
        .order_by("hora_salida")
    )

    index_actual = cola.index(salida_actual)
    atras = cola[max(0, index_actual - 2):index_actual]
    adelante = cola[index_actual + 1:index_actual + 3]

    gps_max_delay = timedelta(seconds=60)
    velocidad_promedio = 25

    try:
        ub_actual = UbicacionVehiculo.objects.get(vehiculo=salida_actual.vehiculo)
    except UbicacionVehiculo.DoesNotExist:
        ub_actual = None

    ubicaciones_map = {
        ubicacion.vehiculo_id: ubicacion
        for ubicacion in UbicacionVehiculo.objects.filter(vehiculo__in=[salida.vehiculo for salida in cola])
    }

    def calcular_minutos(salida):
        if not ub_actual:
            return None
        ubicacion = ubicaciones_map.get(salida.vehiculo.id)
        if not ubicacion or ahora - ubicacion.updated_at > gps_max_delay:
            return None
        distancia = distancia_metros(
            ub_actual.latitud,
            ub_actual.longitud,
            ubicacion.latitud,
            ubicacion.longitud,
        )
        velocidad = ubicacion.velocidad or velocidad_promedio
        metros_min = (velocidad * 1000) / 60
        if metros_min <= 0:
            return None
        return max(int(round(distancia / metros_min)), 0)

    def serializar(salida):
        return {"unidad": salida.vehiculo.codigo, "minutos": calcular_minutos(salida)}

    return JsonResponse({
        "ok": True,
        "actual": {"unidad": salida_actual.vehiculo.codigo, "minutos": 0},
        "atras": [serializar(salida) for salida in atras],
        "adelante": [serializar(salida) for salida in adelante],
    })


@login_required
@empresa_required
@require_GET
def api_panel_frecuencia(request):
    hoy = timezone.localdate()
    empresa = request.empresa
    ruta_id = request.GET.get("ruta", "").strip()
    ruta = None
    if ruta_id:
        ruta = PuntoControl.objects.for_empresa(empresa).filter(ruta_id=ruta_id).values_list("ruta_id", flat=True).first()

    puntos_qs = PuntoControl.objects.for_empresa(empresa).filter(activo=True)
    if ruta:
        puntos_qs = puntos_qs.filter(ruta_id=ruta)
    else:
        puntos_qs = puntos_qs.none()

    puntos = list(puntos_qs.order_by("orden"))
    if not puntos:
        return JsonResponse({"puntos": [], "data": []})

    max_orden = max(punto.orden for punto in puntos)
    config = ConfiguracionDespacho.objects.filter(activa=True, empresa=empresa).first()
    intervalo = config.intervalo_fijo if config and config.intervalo_fijo else 6

    salidas_qs = (
        RegistroSalida.objects.for_empresa(empresa)
        .filter(activo=True, fecha=hoy, ruta_id=puntos[0].ruta_id)
        .select_related("vehiculo", "ruta")
        .annotate(
            ultimo_punto_orden=Max(
                "marcaciones__punto__orden",
                filter=Q(marcaciones__hora_marcada__isnull=False),
            ),
            ultimo_tiempo=Max("marcaciones__hora_marcada"),
        )
    )

    salidas = list(salidas_qs)
    marcaciones_por_salida = {}
    if salidas:
        for marcacion in (
            MarcacionPunto.objects
            .filter(registro_salida__in=salidas, punto__in=puntos)
            .values(
                "registro_salida_id",
                "punto_id",
                "diferencia_minutos",
                "hora_marcada",
            )
        ):
            if not marcacion["hora_marcada"]:
                continue
            marcaciones_por_salida.setdefault(marcacion["registro_salida_id"], {})[
                marcacion["punto_id"]
            ] = marcacion["diferencia_minutos"]

    unidades_panel = []
    for salida in salidas:
        if salida.ultimo_punto_orden == max_orden:
            continue

        marcaciones = marcaciones_por_salida.get(salida.id, {})
        controles = []
        for punto in puntos:
            controles.append(marcaciones.get(punto.id))

        unidades_panel.append({
            "unidad": salida.vehiculo.codigo,
            "salida_id": salida.id,
            "avance": salida.ultimo_punto_orden or 0,
            "ultimo_tiempo": salida.ultimo_tiempo,
            "controles": controles,
            "frecuencia": None,
            "hueco": False,
            "pegado": False,
        })

    unidades_panel.sort(key=lambda unidad: unidad["avance"], reverse=True)
    for index in range(1, len(unidades_panel)):
        actual = unidades_panel[index]
        anterior = unidades_panel[index - 1]
        if actual["ultimo_tiempo"] and anterior["ultimo_tiempo"]:
            diff = (actual["ultimo_tiempo"] - anterior["ultimo_tiempo"]).total_seconds() / 60
            actual["frecuencia"] = int(diff)
            if diff > intervalo * 1.5:
                actual["hueco"] = True
            if diff < intervalo * 0.5:
                actual["pegado"] = True

    if unidades_panel:
        unidades_panel[0]["lider"] = True
        for unidad in unidades_panel[1:]:
            unidad["lider"] = False

    return JsonResponse({
        "puntos": [punto.codigo for punto in puntos],
        "data": unidades_panel,
    })


@login_required
@empresa_required
def debug_gps(request):
    empresa = request.empresa
    data = []

    for ubicacion in UbicacionVehiculo.objects.for_empresa(empresa).select_related("vehiculo"):
        data.append({
            "vehiculo": ubicacion.vehiculo.codigo,
            "lat": ubicacion.latitud,
            "lng": ubicacion.longitud,
            "updated_at": ubicacion.updated_at,
        })

    return JsonResponse(data, safe=False)
