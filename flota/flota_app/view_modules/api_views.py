from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import authenticate
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import IntegrityError
from django.db.models import Max, Q, Sum
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from ..decorators import empresa_required
from ..models import (
    ConfiguracionDespacho,
    GPSRegistro,
    MarcacionPunto,
    MensajeGlobal,
    MovimientoCaja,
    Parada,
    PuntoControl,
    RegistroSalida,
    Ruta,
    SesionStaffApp,
    SesionUnidad,
    UbicacionVehiculo,
    Vehiculo,
)
from ..services import calcular_estado_sesion, validar_sesion, validar_sesion_staff
from ..utils import distancia_metros
from .despacho_views import (
    _construir_panel_despachador_contexto,
    _parse_fecha_panel,
    es_despachador,
)

__all__ = [
    "api_admin_limpiar_gps",
    "api_app_cola_contexto",
    "api_app_mapa_operativo",
    "api_app_gerencia_login",
    "api_app_gerencia_mapa",
    "api_app_ganancias",
    "api_app_ganancias_movimiento",
    "api_app_mensajes",
    "api_app_control_ruta",
    "api_app_control_marcar",
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
    "api_panel_despachador",
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
STAFF_SESSION_HOURS = 12


def _format_hora(dt):
    if not dt:
        return None
    return timezone.localtime(dt).strftime("%H:%M")


def _decimal_to_float(value):
    if value is None:
        return 0.0
    return float(value)


def _serializar_salida_panel(salida, ruta_actual_id, fecha_operativa_iso, hora_actual_hhmm):
    ruta_contexto = ruta_actual_id or str(salida.ruta_id)

    return {
        "id": salida.id,
        "unidad": salida.vehiculo.codigo,
        "ruta_nombre": salida.ruta.nombre if salida.ruta else "",
        "ruta_id": salida.ruta_id,
        "hora_llegada": timezone.localtime(salida.hora_llegada).strftime("%H:%M"),
        "hora_salida": _format_hora(salida.hora_salida),
        "estado_label": salida.estado_panel_label,
        "estado_class": salida.estado_panel_class,
        "urls": {
            "asignar_hora": reverse("asignar_hora_fija", args=[salida.id]),
            "control_ruta": reverse("control_ruta", args=[salida.id]),
            "detalle_salida": reverse("detalle_salida", args=[salida.id]),
            "ver_qr": reverse("ver_qr_unidad", args=[salida.vehiculo.id]),
            "desbloquear_hora": reverse("desbloquear_hora", args=[salida.id]),
        },
        "form": {
            "current_ruta_id": ruta_contexto,
            "current_fecha": fecha_operativa_iso,
            "hora_fija": _format_hora(salida.hora_salida) or hora_actual_hhmm,
        },
    }


def _serializar_panel_despachador(contexto):
    fecha_operativa_iso = contexto["fecha_operativa_iso"]
    ruta_actual_id = contexto["ruta_actual_id"]
    hora_actual_hhmm = contexto["hora_actual_hhmm"]
    reporte_vehiculo_id = contexto["reporte_vehiculo_id"]
    reporte_url = None
    if reporte_vehiculo_id:
        reporte_url = f"{reverse('reporte_salidas_diarias', args=[reporte_vehiculo_id])}?fecha={fecha_operativa_iso}"

    return {
        "ok": True,
        "stats": contexto["stats"],
        "ruta_actual_id": ruta_actual_id,
        "ruta_actual_nombre": contexto["ruta_actual_nombre"],
        "fecha_operativa_iso": fecha_operativa_iso,
        "hora_actual_hhmm": hora_actual_hhmm,
        "reporte_vehiculo_id": reporte_vehiculo_id,
        "reporte_url": reporte_url,
        "salidas": [
            _serializar_salida_panel(
                salida,
                ruta_actual_id=ruta_actual_id,
                fecha_operativa_iso=fecha_operativa_iso,
                hora_actual_hhmm=hora_actual_hhmm,
            )
            for salida in contexto["salidas"]
        ],
    }


@csrf_exempt
@require_GET
def api_admin_limpiar_gps(request):
    maintenance_key = getattr(settings, "MAINTENANCE_ACTION_KEY", "").strip()
    provided_key = str(request.GET.get("key") or "").strip()

    if not maintenance_key:
        return JsonResponse(
            {"ok": False, "mensaje": "La limpieza temporal no esta habilitada."},
            status=403,
        )

    if provided_key != maintenance_key:
        return JsonResponse(
            {"ok": False, "mensaje": "Clave de mantenimiento invalida."},
            status=403,
        )

    gps_count = GPSRegistro.objects.count()
    ubicaciones_count = UbicacionVehiculo.objects.count()

    GPSRegistro.objects.all().delete()
    UbicacionVehiculo.objects.all().delete()

    return JsonResponse(
        {
            "ok": True,
            "mensaje": "Historial GPS y ubicaciones actuales eliminados.",
            "gps_eliminados": gps_count,
            "ubicaciones_eliminadas": ubicaciones_count,
        }
    )


def _aggregate_movimientos(qs):
    ingresos = qs.filter(tipo=MovimientoCaja.TIPO_INGRESO).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    gastos = qs.filter(tipo=MovimientoCaja.TIPO_GASTO).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    neto = ingresos - gastos
    return {
        "ingresos": _decimal_to_float(ingresos),
        "gastos": _decimal_to_float(gastos),
        "neto": _decimal_to_float(neto),
    }


def _serializar_ganancias(sesion):
    hoy = timezone.localdate()
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    inicio_mes = hoy.replace(day=1)
    inicio_ano = date(hoy.year, 1, 1)

    movimientos_qs = (
        MovimientoCaja.objects.for_empresa(sesion.vehiculo.empresa)
        .filter(vehiculo=sesion.vehiculo)
        .order_by("-fecha_operacion", "-creado_en", "-id")
    )

    resumen_hoy = _aggregate_movimientos(movimientos_qs.filter(fecha_operacion=hoy))
    resumen_semana = _aggregate_movimientos(movimientos_qs.filter(fecha_operacion__gte=inicio_semana, fecha_operacion__lte=hoy))
    resumen_mes = _aggregate_movimientos(movimientos_qs.filter(fecha_operacion__gte=inicio_mes, fecha_operacion__lte=hoy))
    resumen_ano = _aggregate_movimientos(movimientos_qs.filter(fecha_operacion__gte=inicio_ano, fecha_operacion__lte=hoy))

    movimientos = []
    for movimiento in movimientos_qs[:8]:
        movimientos.append({
            "id": movimiento.id,
            "tipo": movimiento.tipo,
            "categoria": movimiento.categoria,
            "nota": movimiento.nota or "",
            "monto": _decimal_to_float(movimiento.monto),
            "fecha_operacion": movimiento.fecha_operacion.isoformat(),
            "creado_en": timezone.localtime(movimiento.creado_en).isoformat(),
        })

    meta_diaria = 180.0
    progreso_meta = 0.0
    if meta_diaria > 0:
        progreso_meta = min(max(resumen_hoy["neto"] / meta_diaria, 0.0), 1.0)

    return {
        "ok": True,
        "meta_diaria": meta_diaria,
        "progreso_meta": progreso_meta,
        "caja_dia": {
            "ingreso_bruto": resumen_hoy["ingresos"],
            "gasto_total": resumen_hoy["gastos"],
            "ganancia_neta": resumen_hoy["neto"],
            "movimientos": int(movimientos_qs.filter(fecha_operacion=hoy).count()),
        },
        "resumen_hoy": resumen_hoy,
        "resumen_semana": resumen_semana,
        "resumen_mes": resumen_mes,
        "resumen_ano": resumen_ano,
        "movimientos": movimientos,
    }


def _serializar_mensaje(item):
    return {
        "id": item.id,
        "texto": item.texto,
        "scope": "unidad" if item.vehiculo_id else ("empresa" if item.empresa_id else "global"),
        "unidad": item.vehiculo.codigo if item.vehiculo_id else None,
        "empresa": item.empresa.nombre if item.empresa_id else None,
        "fecha_inicio": item.fecha_inicio.isoformat(),
        "fecha_fin": item.fecha_fin.isoformat(),
        "actualizado_en": (
            item.updated_at.isoformat()
            if item.updated_at
            else item.creado_en.isoformat()
        ),
    }


def actualizar_heartbeat(sesion, ahora):
    sesion.last_heartbeat = ahora
    SesionUnidad.objects.filter(pk=sesion.pk).update(last_heartbeat=ahora)


def _user_puede_mapa_gerencial(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=["gerente", "gerencia", "despachador"]).exists()


def _resolver_empresa_staff(user):
    if user.is_superuser:
        perfil = getattr(user, "perfil", None)
        return getattr(perfil, "empresa", None)

    perfil = getattr(user, "perfil", None)
    return getattr(perfil, "empresa", None)


def _serializar_unidades_mapa_gerencial(empresa):
    ahora = timezone.now()
    hoy = timezone.localdate()

    salidas_activas_qs = (
        RegistroSalida.objects.for_empresa(empresa)
        .filter(fecha=hoy, activo=True)
        .values("vehiculo_id", "ruta_id", "ruta__nombre")
    )
    salidas_activas = {
        salida["vehiculo_id"]: {
            "ruta_id": salida["ruta_id"],
            "ruta_nombre": salida["ruta__nombre"] or "",
        }
        for salida in salidas_activas_qs
    }

    ubicaciones = list(
        UbicacionVehiculo.objects.for_empresa(empresa)
        .values(
            "vehiculo_id",
            "vehiculo__codigo",
            "vehiculo__placa",
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
            "placa": (ubicacion["vehiculo__placa"] or "").strip(),
            "ruta_id": salidas_activas.get(ubicacion["vehiculo_id"], {}).get("ruta_id"),
            "ruta_nombre": salidas_activas.get(ubicacion["vehiculo_id"], {}).get("ruta_nombre", ""),
            "lat": ubicacion["latitud"],
            "lng": ubicacion["longitud"],
            "velocidad": ubicacion["velocidad"],
            "precision": ubicacion["precision"],
            "estado": "ACTIVO" if ubicacion["vehiculo_id"] in salidas_activas else "INACTIVO",
            "estado_gps": estado_gps,
            "actualizado_en": actualizado_en.isoformat(),
        })

    return data


def _extraer_datos_qr(valor_qr):
    if valor_qr is None:
        return None, None, False

    if isinstance(valor_qr, (int, float)):
        return int(valor_qr), None, False

    contenido = str(valor_qr).strip()
    if not contenido:
        return None, None, False

    try:
        payload = signing.loads(contenido, salt="qr-unidad")
        return payload.get("vehiculo_id"), payload.get("empresa_id"), False
    except signing.BadSignature:
        if not getattr(settings, "ALLOW_LEGACY_QR", False):
            return None, None, True

    try:
        payload = json.loads(contenido)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        vehiculo_id = payload.get("vehiculo_id") or payload.get("id")
        empresa_id = payload.get("empresa_id")
        token_anidado = payload.get("token")
        if token_anidado and token_anidado != contenido:
            return _extraer_datos_qr(token_anidado)
        return vehiculo_id, empresa_id, False

    if contenido.isdigit():
        return int(contenido), None, False

    if "://" in contenido:
        parsed = urlparse(contenido)
        params = parse_qs(parsed.query)
        vehiculo_id = params.get("vehiculo_id", [None])[0] or params.get("id", [None])[0]
        empresa_id = params.get("empresa_id", [None])[0]
        token_url = params.get("token", [None])[0]
        if token_url:
            return _extraer_datos_qr(token_url)
        if vehiculo_id:
            return vehiculo_id, empresa_id, False

    return None, None, True


def _normalizar_codigo_unidad(valor):
    codigo = str(valor or "").strip()
    if not codigo:
        return ""
    if codigo.isdigit() and len(codigo) == 1:
        return codigo.zfill(2)
    return codigo.upper()


def _normalizar_placa(valor):
    return "".join(ch for ch in str(valor or "").upper() if ch.isalnum())


def _resolver_vehiculo_por_codigo_placa(codigo, placa=None):
    codigo_normalizado = _normalizar_codigo_unidad(codigo)
    if not codigo_normalizado:
        return None, "CODIGO_REQUERIDO"

    coincidencias = list(
        Vehiculo.objects.filter(codigo__iexact=codigo_normalizado, activo=True)
        .select_related("empresa")
    )
    if not coincidencias:
        return None, "NO_ENCONTRADO"

    placa_normalizada = _normalizar_placa(placa)
    if placa_normalizada:
        coincidencias = [
            vehiculo for vehiculo in coincidencias
            if _normalizar_placa(vehiculo.placa) == placa_normalizada
        ]
        if not coincidencias:
            return None, "PLACA_NO_COINCIDE"

    if len(coincidencias) > 1:
        return None, "AMBIGUO"

    return coincidencias[0], None


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


def _asegurar_marcaciones_salida(salida):
    if not salida.ruta or salida.marcaciones.exists():
        return

    puntos = (
        PuntoControl.objects
        .filter(ruta=salida.ruta, activo=True, requiere_marcacion=True)
        .order_by("orden")
    )
    for punto in puntos:
        MarcacionPunto.objects.get_or_create(registro_salida=salida, punto=punto)


def _resolver_marcacion_por_ubicacion(salida, lat, lng, ahora):
    pendientes = list(salida.marcaciones_pendientes())
    if not pendientes:
        return None, []

    coincidencia = None
    for marcacion in pendientes:
        punto = marcacion.punto
        distancia = distancia_metros(lat, lng, float(punto.latitud), float(punto.longitud))
        if distancia <= punto.radio_metros:
            coincidencia = marcacion
            break

    if not coincidencia:
        return pendientes[0], []

    omitidas = []
    for marcacion in pendientes:
        if marcacion.punto.orden >= coincidencia.punto.orden:
            break
        marcacion.marcar_omitida(hora=ahora)
        omitidas.append(marcacion)

    return coincidencia, omitidas


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

    _asegurar_marcaciones_salida(salida)

    if salida.finalizar_por_inactividad(ahora=ahora):
        return JsonResponse({
            "accion": "ninguna",
            "finalizada": True,
            "motivo": "inactividad_punto",
        })

    marcacion, omitidas = _resolver_marcacion_por_ubicacion(
        salida=salida,
        lat=lat,
        lng=lng,
        ahora=ahora,
    )
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
        "omitidos": [
            {
                "codigo": item.punto.codigo,
                "nombre": item.punto.nombre,
                "estado": item.estado.upper(),
            }
            for item in omitidas
        ],
        "visual": {
            "codigo": punto.codigo,
            "punto": punto.nombre,
            "estado": marcacion.estado.upper(),
            "diferencia_min": marcacion.diferencia_minutos,
            "hora_marcada": (
                timezone.localtime(marcacion.hora_marcada).strftime("%H:%M")
                if marcacion.hora_marcada else None
            ),
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
    empresa = request.empresa
    data = _serializar_unidades_mapa_gerencial(empresa)
    return JsonResponse(data, safe=False)


@csrf_exempt
@require_POST
def api_app_gerencia_login(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}

    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")

    if not username or not password:
        return JsonResponse(
            {"ok": False, "mensaje": "Usuario y contrasena son obligatorios."},
            status=400,
        )

    user = authenticate(username=username, password=password)
    if not user:
        return JsonResponse(
            {"ok": False, "mensaje": "Credenciales invalidas."},
            status=401,
        )

    if not _user_puede_mapa_gerencial(user):
        return JsonResponse(
            {"ok": False, "mensaje": "Tu usuario no tiene acceso al mapa gerencial premium."},
            status=403,
        )

    empresa = _resolver_empresa_staff(user)
    if not empresa:
        return JsonResponse(
            {"ok": False, "mensaje": "Tu usuario no tiene una empresa asignada."},
            status=403,
        )

    ahora = timezone.now()
    SesionStaffApp.objects.filter(user=user, activa=True).update(activa=False)
    sesion = SesionStaffApp.objects.create(
        user=user,
        empresa=empresa,
        activa=True,
        expira_en=ahora + timedelta(hours=STAFF_SESSION_HOURS),
        ultimo_acceso=ahora,
    )

    rol = "superuser" if user.is_superuser else (
        user.groups.filter(name__in=["gerente", "gerencia"]).exists() and "gerencia" or "despacho"
    )

    return JsonResponse({
        "ok": True,
        "token": str(sesion.token),
        "usuario": user.username,
        "empresa": empresa.nombre,
        "rol": rol,
        "expira_en": sesion.expira_en.isoformat() if sesion.expira_en else None,
    })


@require_GET
def api_app_gerencia_mapa(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"ok": False, "mensaje": "Token no enviado."}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion_staff(token)
    if not sesion:
        return JsonResponse({"ok": False, "mensaje": "Sesion gerencial invalida o expirada."}, status=403)

    ahora = timezone.now()
    SesionStaffApp.objects.filter(pk=sesion.pk).update(ultimo_acceso=ahora)
    empresa = sesion.empresa
    rutas = []
    for ruta in Ruta.objects.for_empresa(empresa).order_by("nombre"):
        geometria = _serializar_geometria_ruta(ruta)
        puntos = _serializar_puntos_ruta(ruta)
        if not geometria and puntos:
            geometria = [[punto["lat"], punto["lng"]] for punto in puntos]
        rutas.append({
            "id": ruta.id,
            "nombre": ruta.nombre,
            "geometria": geometria,
            "puntos": puntos,
        })

    unidades = _serializar_unidades_mapa_gerencial(empresa)
    online = sum(1 for unidad in unidades if unidad["estado_gps"] == "ONLINE")
    lentas = sum(1 for unidad in unidades if unidad["estado_gps"] == "LENTO")
    offline = sum(1 for unidad in unidades if unidad["estado_gps"] == "OFFLINE")

    return JsonResponse({
        "ok": True,
        "empresa": empresa.nombre,
        "actualizado_en": ahora.isoformat(),
        "stats": {
            "total_unidades": len(unidades),
            "online": online,
            "lentas": lentas,
            "offline": offline,
            "rutas": len(rutas),
        },
        "rutas": rutas,
        "unidades": unidades,
    })


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
            "requiere_marcacion": punto.requiere_marcacion,
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


def _serializar_geometria_ruta(ruta):
    if not ruta or not isinstance(ruta.geometria, list):
        return []

    coords_validas = []
    for punto in ruta.geometria:
        if not isinstance(punto, (list, tuple)) or len(punto) != 2:
            continue
        try:
            coords_validas.append([float(punto[0]), float(punto[1])])
        except (TypeError, ValueError):
            continue
    return coords_validas


def _serializar_puntos_ruta(ruta):
    if not ruta:
        return []

    puntos = (
        PuntoControl.objects
        .filter(ruta=ruta, activo=True)
        .order_by("orden")
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
            "lat": float(punto.latitud),
            "lng": float(punto.longitud),
            "radio": punto.radio_metros,
            "requiere_marcacion": punto.requiere_marcacion,
        })
    return data


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
        .filter(Q(empresa=sesion.vehiculo.empresa) | Q(empresa__isnull=True))
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

    token_qr = (
        data.get("token")
        or data.get("qr")
        or data.get("codigo_qr")
        or data.get("contenido")
        or data.get("rawValue")
    )
    vehiculo_id = data.get("vehiculo_id")
    codigo_manual = data.get("codigo")
    placa_manual = data.get("placa")
    empresa_id = None

    if token_qr:
        vehiculo_qr, empresa_qr, qr_invalido = _extraer_datos_qr(token_qr)
        if qr_invalido:
            return JsonResponse(
                {"ok": False, "error": "QR invalido o manipulado"},
                status=400,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        vehiculo_id = vehiculo_qr or vehiculo_id
        empresa_id = empresa_qr or empresa_id

    vehiculo = None
    if vehiculo_id:
        vehiculos = Vehiculo.objects.filter(id=vehiculo_id, activo=True).select_related("empresa")
        if empresa_id is not None:
            vehiculos = vehiculos.filter(empresa_id=empresa_id)
        vehiculo = vehiculos.first()
    elif codigo_manual:
        vehiculo, motivo_manual = _resolver_vehiculo_por_codigo_placa(
            codigo=codigo_manual,
            placa=placa_manual,
        )
        if motivo_manual == "AMBIGUO":
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Mas de una unidad coincide con ese codigo. Ingresa la placa.",
                },
                status=409,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        if motivo_manual == "PLACA_NO_COINCIDE":
            return JsonResponse(
                {
                    "ok": False,
                    "error": "La placa no coincide con la unidad ingresada.",
                },
                status=404,
                headers={"Access-Control-Allow-Origin": "*"},
            )
    else:
        return JsonResponse(
            {"ok": False, "error": "vehiculo_id, token o codigo requeridos"},
            status=400,
            headers={"Access-Control-Allow-Origin": "*"},
        )

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

    if salida and salida.finalizar_por_inactividad():
        salida = None

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
    if salida and salida.finalizar_por_inactividad():
        return JsonResponse({"ok": False})
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


def _serializar_control_ruta(salida):
    puntos = (
        PuntoControl.objects
        .filter(ruta=salida.ruta, activo=True, requiere_marcacion=True)
        .order_by("orden")
    )

    controles = []
    completados = 0

    for punto in puntos:
        hora_programada = (
            salida.hora_salida + timedelta(minutes=punto.offset_minutos)
            if salida.hora_salida
            else None
        )
        marcacion, _ = MarcacionPunto.objects.get_or_create(
            registro_salida=salida,
            punto=punto,
            defaults={"hora_programada": hora_programada},
        )
        if marcacion.hora_marcada:
            completados += 1

        controles.append({
            "punto_id": punto.id,
            "orden": punto.orden,
            "codigo": punto.codigo,
            "nombre": punto.nombre,
            "hora_programada": _format_hora(hora_programada or marcacion.hora_programada),
            "hora_marcada": _format_hora(marcacion.hora_marcada),
            "diferencia_minutos": marcacion.diferencia_minutos,
            "estado": marcacion.estado,
            "pendiente": marcacion.hora_marcada is None,
        })

    total = len(controles)
    siguiente = next((item for item in controles if item["pendiente"]), None)

    return {
        "ok": True,
        "salida": {
            "id": salida.id,
            "unidad": salida.vehiculo.codigo,
            "ruta": salida.ruta.nombre if salida.ruta else "",
            "hora_salida": _format_hora(salida.hora_salida),
        },
        "resumen": {
            "total": total,
            "completados": completados,
            "pendientes": max(total - completados, 0),
            "siguiente_codigo": siguiente["codigo"] if siguiente else None,
        },
        "controles": controles,
    }


@require_GET
def api_app_control_ruta(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"ok": False, "mensaje": "Token requerido"}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({"ok": False, "mensaje": "Sesion invalida"}, status=403)

    hoy = timezone.localdate()
    salida = (
        RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa)
        .select_related("vehiculo", "ruta")
        .filter(
            vehiculo=sesion.vehiculo,
            fecha=hoy,
            activo=True,
            ruta__isnull=False,
        )
        .order_by("-id")
        .first()
    )

    if not salida or not salida.ruta:
        return JsonResponse({
            "ok": False,
            "mensaje": "La unidad aun no tiene una ruta programada",
        })

    if salida.finalizar_por_inactividad():
        return JsonResponse({
            "ok": False,
            "mensaje": "La ruta fue finalizada por inactividad.",
        })

    return JsonResponse(_serializar_control_ruta(salida))


@csrf_exempt
@require_POST
def api_app_control_marcar(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"ok": False, "mensaje": "Token requerido"}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({"ok": False, "mensaje": "Sesion invalida"}, status=403)

    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "mensaje": "JSON invalido"}, status=400)

    punto_id = data.get("punto_id")
    hoy = timezone.localdate()
    salida = (
        RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa)
        .select_related("vehiculo", "ruta")
        .filter(vehiculo=sesion.vehiculo, fecha=hoy, activo=True, ruta__isnull=False)
        .order_by("-id")
        .first()
    )
    if not salida or not salida.ruta:
        return JsonResponse({"ok": False, "mensaje": "No hay salida activa"}, status=404)

    if salida.finalizar_por_inactividad():
        return JsonResponse({
            "ok": False,
            "mensaje": "La ruta fue finalizada por inactividad.",
        })

    punto_qs = PuntoControl.objects.filter(
        ruta=salida.ruta,
        activo=True,
        requiere_marcacion=True,
    )
    punto = punto_qs.filter(id=punto_id).first() if punto_id else None
    if not punto:
        siguiente = salida.siguiente_marcacion()
        punto = siguiente.punto if siguiente else None
    if not punto:
        return JsonResponse({"ok": False, "mensaje": "No hay puntos pendientes"})

    marcacion, _ = MarcacionPunto.objects.get_or_create(
        registro_salida=salida,
        punto=punto,
    )
    marcacion.marcar()

    ultimo = punto_qs.order_by("-orden").first()
    if ultimo and punto.id == ultimo.id:
        salida.activo = False
        salida.en_cola = False
        salida.save(update_fields=["activo", "en_cola"])

    data = _serializar_control_ruta(salida)
    data["mensaje"] = f"Punto {punto.codigo} marcado"
    return JsonResponse(data)


@require_GET
def api_app_ganancias(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"ok": False, "mensaje": "Token requerido"}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({"ok": False, "mensaje": "Sesion invalida"}, status=403)

    data = _serializar_ganancias(sesion)
    if not data["movimientos"]:
        data["mensaje"] = "Aun no hay movimientos registrados en caja."
    return JsonResponse(data)


@csrf_exempt
@require_POST
def api_app_ganancias_movimiento(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"ok": False, "mensaje": "Token requerido"}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({"ok": False, "mensaje": "Sesion invalida"}, status=403)

    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "mensaje": "JSON invalido"}, status=400)

    tipo = str(data.get("tipo") or "").strip().lower()
    categoria = str(data.get("categoria") or "Otros").strip() or "Otros"
    nota = str(data.get("nota") or "").strip()

    if tipo not in {MovimientoCaja.TIPO_INGRESO, MovimientoCaja.TIPO_GASTO}:
        return JsonResponse({"ok": False, "mensaje": "Tipo invalido"}, status=400)

    try:
        monto = Decimal(str(data.get("monto")))
    except (InvalidOperation, TypeError, ValueError):
        return JsonResponse({"ok": False, "mensaje": "Monto invalido"}, status=400)

    if monto <= 0:
        return JsonResponse({"ok": False, "mensaje": "El monto debe ser mayor a cero"}, status=400)

    fecha_operacion_raw = str(data.get("fecha_operacion") or "").strip()
    fecha_operacion = timezone.localdate()
    if fecha_operacion_raw:
        try:
            fecha_operacion = date.fromisoformat(fecha_operacion_raw)
        except ValueError:
            return JsonResponse({"ok": False, "mensaje": "Fecha invalida"}, status=400)

    salida = (
        RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa)
        .filter(vehiculo=sesion.vehiculo, fecha=fecha_operacion)
        .order_by("-id")
        .first()
    )

    MovimientoCaja.objects.create(
        empresa=sesion.vehiculo.empresa,
        vehiculo=sesion.vehiculo,
        sesion=sesion,
        salida=salida,
        tipo=tipo,
        categoria=categoria,
        nota=nota,
        monto=monto,
        fecha_operacion=fecha_operacion,
    )

    payload = _serializar_ganancias(sesion)
    payload["mensaje"] = (
        f"{'Ingreso' if tipo == MovimientoCaja.TIPO_INGRESO else 'Gasto'} registrado correctamente"
    )
    return JsonResponse(payload)


@require_GET
def api_app_mensajes(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"ok": False, "mensaje": "Token requerido"}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({"ok": False, "mensaje": "Sesion invalida"}, status=403)

    hoy = timezone.localdate()
    mensajes_qs = (
        MensajeGlobal.objects.filter(
            activo=True,
            fecha_inicio__lte=hoy,
            fecha_fin__gte=hoy,
        )
        .filter(
            Q(vehiculo=sesion.vehiculo)
            | Q(vehiculo__isnull=True, empresa=sesion.vehiculo.empresa)
            | Q(vehiculo__isnull=True, empresa__isnull=True)
        )
        .select_related("empresa", "vehiculo")
        .order_by("-updated_at", "-id")
    )

    mensajes = [_serializar_mensaje(item) for item in mensajes_qs]
    return JsonResponse({
        "ok": True,
        "cantidad": len(mensajes),
        "mensajes": mensajes,
        "mensaje": "No hay comunicados activos para esta unidad." if not mensajes else None,
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

    if salida_actual.finalizar_por_inactividad(ahora=ahora):
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

    adelante = cola[:index_actual][-2:]
    atras = cola[index_actual + 1:index_actual + 3]

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
    ultimo_punto_map = {}
    for marcacion in (
        MarcacionPunto.objects.filter(
            registro_salida__in=cola,
            hora_marcada__isnull=False,
        )
        .select_related("punto")
        .order_by("registro_salida_id", "punto__orden")
    ):
        ultimo_punto_map[marcacion.registro_salida_id] = marcacion.punto.codigo

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
        return {
            "unidad": salida.vehiculo.codigo,
            "minutos": calcular_minutos(salida),
            "punto_actual_codigo": ultimo_punto_map.get(salida.id),
        }

    return JsonResponse({
        "ok": True,
        "actual": {
            "unidad": salida_actual.vehiculo.codigo,
            "minutos": 0,
            "punto_actual_codigo": ultimo_punto_map.get(salida_actual.id),
        },
        "adelante": [serializar(salida) for salida in adelante],
        "atras": [serializar(salida) for salida in atras],
    })


@require_GET
def api_app_mapa_operativo(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"ok": False, "mensaje": "Token no enviado"}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({"ok": False, "mensaje": "Sesion invalida"}, status=403)

    hoy = timezone.localdate()
    salida = (
        RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa)
        .select_related("vehiculo", "ruta")
        .filter(
            vehiculo=sesion.vehiculo,
            fecha=hoy,
            activo=True,
            ruta__isnull=False,
        )
        .order_by("-id")
        .first()
    )

    if not salida or not salida.ruta:
        return JsonResponse({
            "ok": False,
            "mensaje": "La unidad aun no tiene una ruta operativa asignada",
        })

    if salida.finalizar_por_inactividad():
        return JsonResponse({
            "ok": False,
            "mensaje": "La ruta fue finalizada por inactividad.",
        })

    ruta = salida.ruta
    geometria = _serializar_geometria_ruta(ruta)
    puntos = _serializar_puntos_ruta(ruta)

    if not geometria and puntos:
        geometria = [[punto["lat"], punto["lng"]] for punto in puntos]

    ubicacion = (
        UbicacionVehiculo.objects
        .filter(vehiculo=sesion.vehiculo)
        .only("latitud", "longitud", "precision", "updated_at")
        .first()
    )

    return JsonResponse({
        "ok": True,
        "ruta": {
            "id": ruta.id,
            "nombre": ruta.nombre,
            "geometria": geometria,
        },
        "puntos": puntos,
        "unidad": {
            "codigo": sesion.vehiculo.codigo,
            "lat": ubicacion.latitud if ubicacion else None,
            "lng": ubicacion.longitud if ubicacion else None,
            "precision": ubicacion.precision if ubicacion else None,
            "actualizado_en": (
                ubicacion.updated_at.isoformat()
                if ubicacion and ubicacion.updated_at
                else None
            ),
        },
    })


@login_required
@empresa_required
@require_GET
def api_panel_despachador(request):
    if not es_despachador(request.user):
        return JsonResponse(
            {"ok": False, "mensaje": "No tienes permisos para ver este panel."},
            status=403,
        )

    fecha_str = request.GET.get("fecha", "").strip()
    ruta_id = request.GET.get("ruta", "").strip()

    try:
        fecha_operativa = _parse_fecha_panel(fecha_str)
    except ValueError as error:
        return JsonResponse({"ok": False, "mensaje": str(error)}, status=400)

    contexto = _construir_panel_despachador_contexto(
        empresa=request.empresa,
        fecha_operativa=fecha_operativa,
        ruta_id=ruta_id,
    )
    return JsonResponse(_serializar_panel_despachador(contexto))


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

    puntos_qs = PuntoControl.objects.for_empresa(empresa).filter(
        activo=True,
        requiere_marcacion=True,
    )
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
