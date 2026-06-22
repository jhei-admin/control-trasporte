from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import base64
import hashlib
from pathlib import Path
import json
import math
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import authenticate
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import IntegrityError
from django.db.models import Max, Q, Sum
from django.http import FileResponse, Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from ..decorators import empresa_required
from ..models import (
    ComandoDispositivo,
    ConfiguracionDespacho,
    EstadoDispositivo,
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
    _calcular_detalle_salida,
    _construir_control_ruta_contexto,
    _construir_historial_salidas_contexto,
    _construir_panel_despachador_contexto,
    _parse_fecha_panel,
    es_despachador,
)
from .reportes_views import _construir_reporte_salidas_diarias_contexto

__all__ = [
    "api_admin_limpiar_gps",
    "api_app_cola_contexto",
    "api_app_command_ack",
    "api_app_command_pull",
    "api_app_device_status",
    "api_app_mapa_operativo",
    "api_app_gerencia_login",
    "api_app_gerencia_mapa",
    "api_app_gerencia_salidas",
    "app_gerencia_mapa_vivo",
    "api_app_ganancias",
    "api_app_ganancias_movimiento",
    "api_app_mensajes",
    "api_app_version",
    "api_app_update_apk",
    "api_admin_update_apk",
    "api_admin_version",
    "api_admin_provisioning_qr",
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
    "api_reporte_salidas_diarias",
    "api_historial_salidas",
    "api_control_ruta_web",
    "api_detalle_salida_web",
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
RECORRIDO_SAMPLE_SECONDS = max(
    getattr(settings, "RECORRIDO_SAMPLE_SECONDS", 15),
    1,
)
STAFF_SESSION_HOURS = 12
PUNTOS_BLOQUEADOS_AUDIO_CODES = {"MUNI", "LLAM", "PLAZ", "PESQ"}
PUNTOS_CONTEXTO_VUELTA_CODES = {"ZAMA_VTA"}


def _format_hora(dt):
    if not dt:
        return None
    return timezone.localtime(dt).strftime("%H:%M")


def _label_audio_punto(punto):
    if punto.nombre:
        return punto.nombre.strip().upper()
    return (punto.codigo or "PUNTO").strip().upper()


def _resolver_token_staff_request(request):
    auth = request.headers.get("Authorization", "").strip()
    if auth.startswith("Bearer "):
        return auth.replace("Bearer ", "").strip()
    return request.GET.get("token", "").strip()


def _formatear_diferencia_audio(diferencia_minutos):
    if diferencia_minutos in (None, 0):
        return None
    return str(int(diferencia_minutos))


def _construir_audio_texto_marcacion(marcacion, *, finalizada=False):
    partes = [_label_audio_punto(marcacion.punto)]

    if marcacion.hora_marcada:
        partes.append(_format_hora(marcacion.hora_marcada))

    diferencia = abs(int(marcacion.diferencia_minutos or 0))
    if marcacion.estado == "a_tiempo":
        partes.append("EN HORA")
    elif marcacion.estado == "adelantado":
        partes.append("ADELANTADO")
        if diferencia:
            partes.extend(["MENOS", str(diferencia)])
    elif marcacion.estado == "tarde":
        partes.append("TARDE")
        if diferencia:
            partes.extend(["MAS", str(diferencia)])
    else:
        diferencia_audio = _formatear_diferencia_audio(marcacion.diferencia_minutos)
        if diferencia_audio:
            partes.append(diferencia_audio)

    texto = ", ".join([partes[0], " ".join(partes[1:])]) if len(partes) > 1 else partes[0]
    if finalizada:
        return f"{texto}. RUTA FINALIZADA. BUEN TRABAJO"
    return texto


def _decimal_to_float(value):
    if value is None:
        return 0.0
    return float(value)


def _codigo_punto_normalizado(punto):
    if isinstance(punto, dict):
        return str(punto.get("codigo") or "").strip().upper()
    return str(getattr(punto, "codigo", "") or "").strip().upper()


def _fase_punto_normalizada(punto):
    if isinstance(punto, dict):
        fase = punto.get("fase")
    else:
        fase = getattr(punto, "fase", None)
    return str(fase or PuntoControl.FASE_IDA).strip().upper()


def _es_punto_evento_confirmado(punto):
    if _es_punto_contexto_vuelta(punto):
        return True
    if isinstance(punto, dict):
        confirma_avance = bool(punto.get("confirma_avance"))
    else:
        confirma_avance = bool(getattr(punto, "confirma_avance", False))
    return confirma_avance or _codigo_punto_normalizado(punto) in PUNTOS_BLOQUEADOS_AUDIO_CODES


def _es_punto_contexto_interno(punto):
    if isinstance(punto, dict):
        return bool(punto.get("es_contexto_interno"))
    return bool(getattr(punto, "es_contexto_interno", False))


def _es_punto_contexto_vuelta(punto):
    if _fase_punto_normalizada(punto) == PuntoControl.FASE_CONTEXTO:
        return True
    return _es_punto_contexto_interno(punto) and _codigo_punto_normalizado(punto) in PUNTOS_CONTEXTO_VUELTA_CODES


def _codigo_audio_punto(punto):
    codigo = _codigo_punto_normalizado(punto)
    if codigo in PUNTOS_CONTEXTO_VUELTA_CODES:
        return "ZAMA"
    return codigo


def _nombre_audio_punto(punto):
    nombre = str(getattr(punto, "nombre", "") or "").strip()
    if _codigo_punto_normalizado(punto) in PUNTOS_CONTEXTO_VUELTA_CODES:
        return "Zamacola"
    return nombre


def _resolve_app_update_url(request):
    external_url = getattr(settings, "APP_UPDATE_APK_URL", "").strip()
    if external_url:
        return external_url
    return request.build_absolute_uri(reverse("api_app_update_apk"))


def _resolve_admin_update_url(request):
    explicit_dpc_url = getattr(settings, "ADMIN_DPC_DOWNLOAD_URL", "").strip()
    if explicit_dpc_url:
        return explicit_dpc_url

    external_url = getattr(settings, "ADMIN_APP_UPDATE_APK_URL", "").strip()
    if external_url:
        return external_url

    return request.build_absolute_uri(reverse("api_admin_update_apk"))


def _resolve_admin_update_url_from_request(request):
    return _resolve_admin_update_url(request)


def _build_admin_package_checksum():
    configured_checksum = getattr(settings, "ADMIN_DPC_PACKAGE_CHECKSUM", "").strip()
    if configured_checksum:
        return configured_checksum

    apk_path = Path(getattr(settings, "ADMIN_APP_UPDATE_APK_PATH", ""))
    if not apk_path.is_file():
        return ""

    digest = hashlib.sha256(apk_path.read_bytes()).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@require_GET
def api_app_version(request):
    latest_version_code = int(getattr(settings, "APP_LATEST_VERSION_CODE", 0) or 0)
    latest_version_name = getattr(settings, "APP_LATEST_VERSION_NAME", "").strip()
    changelog = getattr(settings, "APP_UPDATE_CHANGELOG", "").strip()
    published_at = getattr(settings, "APP_UPDATE_PUBLISHED_AT", "").strip()
    apk_url = _resolve_app_update_url(request)
    local_apk_path = Path(getattr(settings, "APP_UPDATE_APK_PATH", ""))
    local_apk_exists = local_apk_path.is_file()
    external_url = getattr(settings, "APP_UPDATE_APK_URL", "").strip()
    update_ready = bool(latest_version_code > 0 and (external_url or local_apk_exists))

    return JsonResponse(
        {
            "ok": True,
            "update_ready": update_ready,
            "latest_version_code": latest_version_code,
            "latest_version_name": latest_version_name,
            "force_update": bool(getattr(settings, "APP_UPDATE_FORCE", False)),
            "changelog": changelog,
            "published_at": published_at,
            "apk_url": apk_url if update_ready else None,
        }
    )


@require_GET
def api_app_update_apk(request):
    external_url = getattr(settings, "APP_UPDATE_APK_URL", "").strip()
    if external_url:
        return HttpResponseRedirect(external_url)

    apk_path = Path(getattr(settings, "APP_UPDATE_APK_PATH", ""))
    if not apk_path.is_file():
        raise Http404("APK no disponible")

    response = FileResponse(
        apk_path.open("rb"),
        as_attachment=True,
        filename=apk_path.name,
        content_type="application/vnd.android.package-archive",
    )
    response["Cache-Control"] = "no-store, max-age=0"
    return response


@require_GET
def api_admin_update_apk(request):
    external_url = getattr(settings, "ADMIN_APP_UPDATE_APK_URL", "").strip()
    if external_url:
        return HttpResponseRedirect(external_url)

    apk_path = Path(getattr(settings, "ADMIN_APP_UPDATE_APK_PATH", ""))
    if not apk_path.is_file():
        raise Http404("APK admin no disponible")

    response = FileResponse(
        apk_path.open("rb"),
        as_attachment=True,
        filename=apk_path.name,
        content_type="application/vnd.android.package-archive",
    )
    response["Cache-Control"] = "no-store, max-age=0"
    return response


@require_GET
def api_admin_version(request):
    latest_version_code = int(getattr(settings, "ADMIN_LATEST_VERSION_CODE", 0) or 0)
    latest_version_name = getattr(settings, "ADMIN_LATEST_VERSION_NAME", "").strip()
    changelog = getattr(settings, "ADMIN_UPDATE_CHANGELOG", "").strip()
    published_at = getattr(settings, "ADMIN_UPDATE_PUBLISHED_AT", "").strip()
    apk_url = _resolve_admin_update_url(request)
    local_apk_path = Path(getattr(settings, "ADMIN_APP_UPDATE_APK_PATH", ""))
    local_apk_exists = local_apk_path.is_file()
    external_url = getattr(settings, "ADMIN_APP_UPDATE_APK_URL", "").strip()
    update_ready = bool(latest_version_code > 0 and (external_url or local_apk_exists))

    return JsonResponse(
        {
            "ok": True,
            "update_ready": update_ready,
            "latest_version_code": latest_version_code,
            "latest_version_name": latest_version_name,
            "force_update": bool(getattr(settings, "ADMIN_UPDATE_FORCE", False)),
            "changelog": changelog,
            "published_at": published_at,
            "apk_url": apk_url if update_ready else None,
        }
    )


@require_GET
def api_admin_provisioning_qr(request):
    component_name = getattr(settings, "ADMIN_DPC_COMPONENT_NAME", "").strip()
    package_download_url = _resolve_admin_update_url(request)
    package_checksum = _build_admin_package_checksum()
    server_url = request.build_absolute_uri("/sistema/")

    if not component_name or not package_download_url or not package_checksum:
        return JsonResponse(
            {
                "ok": False,
                "motivo": "PROVISIONING_INCOMPLETO",
                "component_name": component_name,
                "package_download_url": package_download_url,
                "package_checksum": package_checksum,
            },
            status=503,
        )

    payload = {
        "android.app.extra.PROVISIONING_DEVICE_ADMIN_COMPONENT_NAME": component_name,
        "android.app.extra.PROVISIONING_DEVICE_ADMIN_PACKAGE_DOWNLOAD_LOCATION": package_download_url,
        "android.app.extra.PROVISIONING_DEVICE_ADMIN_PACKAGE_CHECKSUM": package_checksum,
        "android.app.extra.PROVISIONING_SKIP_ENCRYPTION": bool(
            getattr(settings, "ADMIN_DPC_SKIP_ENCRYPTION", True)
        ),
        "android.app.extra.PROVISIONING_LEAVE_ALL_SYSTEM_APPS_ENABLED": bool(
            getattr(settings, "ADMIN_DPC_LEAVE_ALL_SYSTEM_APPS_ENABLED", True)
        ),
        "android.app.extra.PROVISIONING_ADMIN_EXTRAS_BUNDLE": {
            "server_url": server_url,
            "device_token": "",
            "auto_start": True,
        },
    }

    return JsonResponse(
        {
            "ok": True,
            "modo": "fully_managed_device",
            "component_name": component_name,
            "download_url": package_download_url,
            "package_checksum": package_checksum,
            "payload": payload,
            "instrucciones": [
                "Resetea el equipo y entra al asistente inicial.",
                "En la pantalla inicial toca varias veces para abrir el lector QR de Android Enterprise si el equipo lo soporta.",
                "Escanea el payload QR generado a partir de este JSON.",
                "Completa el enrolamiento y luego verifica GPS Flota Admin como Device Owner.",
            ],
        }
    )


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


def _serializar_historial_item(item):
    return {
        "unidad": item["salida"].vehiculo.codigo,
        "ruta": item["salida"].ruta.nombre if item["salida"].ruta else "SIN RUTA",
        "fecha": item["salida"].fecha.isoformat(),
        "programada": _format_hora(item["programada"]),
        "marcada": _format_hora(item["marcada"]),
        "falta": item["falta"],
        "estado": item["estado"],
    }


def _serializar_reporte_item(item):
    return {
        "hora": _format_hora(item["hora"]),
        "ruta": item["ruta"],
        "vuelta": item["vuelta"],
        "porcentaje": item["porcentaje"],
        "minutos": item["minutos"],
        "salida_id": item["salida_id"],
        "detalle_url": reverse("detalle_salida", args=[item["salida_id"]]),
    }


def _serializar_control_web(contexto):
    controles = []
    for control in contexto["controles"]:
        punto = control["punto"]
        marcacion = control["marcacion"]
        controles.append(
            {
                "orden": punto.orden,
                "codigo": punto.codigo,
                "nombre": punto.nombre,
                "hora_programada": _format_hora(
                    control["hora_programada_calculada"] or getattr(marcacion, "hora_programada", None)
                ),
                "hora_marcada": _format_hora(getattr(marcacion, "hora_marcada", None)),
                "diferencia": getattr(marcacion, "diferencia_minutos", None),
                "estado": getattr(marcacion, "estado", None) or "pendiente",
                "marcada": bool(getattr(marcacion, "hora_marcada", None)),
                "marcar_url": reverse("marcar_paso", args=[contexto["salida"].id, punto.id]),
            }
        )

    siguiente = contexto["resumen"]["siguiente"]
    return {
        "ok": True,
        "resumen": {
            **contexto["resumen"],
            "siguiente": {
                "codigo": siguiente.codigo,
                "nombre": siguiente.nombre,
            } if siguiente else None,
        },
        "controles": controles,
    }


def _serializar_detalle_web(contexto):
    return {
        "ok": True,
        "resumen": contexto["resumen"],
        "detalle": [
            {
                "orden": index + 1,
                "codigo": item["punto"].codigo,
                "nombre": item["punto"].nombre,
                "hora_programada": _format_hora(item["hora_programada"]),
                "hora_marcada": _format_hora(item["hora_marcada"]),
                "diferencia": item["diferencia"],
                "estado": item["estado"],
            }
            for index, item in enumerate(contexto["detalle"])
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
            "rumbo",
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
            "direccion": ubicacion["rumbo"] or 0,
            "rumbo": ubicacion["rumbo"] or 0,
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


def guardar_ubicacion_actual(sesion, ahora, lat, lng, velocidad=None, precision=None, rumbo=None):
    defaults = {
        "latitud": lat,
        "longitud": lng,
        "updated_at": ahora,
    }
    if velocidad is not None:
        defaults["velocidad"] = velocidad
    if precision is not None:
        defaults["precision"] = precision
    if rumbo is not None:
        defaults["rumbo"] = rumbo

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


def _get_ubicacion_actual(vehiculo):
    try:
        return UbicacionVehiculo.objects.get(vehiculo=vehiculo)
    except UbicacionVehiculo.DoesNotExist:
        return None


def registrar_punto_evento_confirmado(sesion, punto, ahora):
    if not sesion or not punto or not _es_punto_evento_confirmado(punto):
        return

    codigo = _codigo_audio_punto(punto)
    orden = punto["orden"] if isinstance(punto, dict) else punto.orden
    ubicacion = UbicacionVehiculo.objects.filter(vehiculo=sesion.vehiculo).first()
    if not ubicacion:
        return

    # En puntos de paso debemos conservar el primer instante en que la unidad
    # alcanzo esa referencia. Si se reescribe cada ping dentro del mismo radio,
    # el sistema olvida quien llego primero y se invierte el sobrepaso.
    if (
        (ubicacion.ultimo_punto_evento_codigo or "").strip().upper() == codigo
        and (ubicacion.ultimo_punto_evento_orden or 0) == orden
        and ubicacion.ultimo_punto_evento_at is not None
    ):
        return

    UbicacionVehiculo.objects.filter(pk=ubicacion.pk).update(
        ultimo_punto_evento_codigo=codigo,
        ultimo_punto_evento_orden=orden,
        ultimo_punto_evento_at=ahora,
    )


def resetear_contexto_inicio_ruta(sesion):
    UbicacionVehiculo.objects.filter(vehiculo=sesion.vehiculo).update(
        en_retorno=False,
        ultimo_punto_evento_codigo=None,
        ultimo_punto_evento_orden=None,
        ultimo_punto_evento_at=None,
    )


def _ruta_tiene_contexto_vuelta(ruta):
    if not ruta:
        return False
    return PuntoControl.objects.filter(
        ruta=ruta,
        activo=True,
    ).filter(
        Q(fase=PuntoControl.FASE_CONTEXTO)
        | Q(es_contexto_interno=True, codigo__in=PUNTOS_CONTEXTO_VUELTA_CODES)
    ).exists()


def _sincronizar_fase_retorno(salida, sesion, lat, lng):
    if not salida or not salida.ruta:
        return False, None

    ubicacion = _get_ubicacion_actual(sesion.vehiculo)
    if not ubicacion:
        return False, None

    siguiente = salida.siguiente_marcacion()
    if not siguiente or siguiente.punto.orden <= 4:
        if ubicacion.en_retorno:
            UbicacionVehiculo.objects.filter(pk=ubicacion.pk).update(en_retorno=False)
            ubicacion.en_retorno = False
        return False, None

    if ubicacion.en_retorno:
        return True, None

    if not _ruta_tiene_contexto_vuelta(salida.ruta):
        return True, None

    puntos_contexto = PuntoControl.objects.filter(
        ruta=salida.ruta,
        activo=True,
    ).filter(
        Q(fase=PuntoControl.FASE_CONTEXTO)
        | Q(es_contexto_interno=True, codigo__in=PUNTOS_CONTEXTO_VUELTA_CODES)
    )
    for punto in puntos_contexto:
        distancia = distancia_metros(lat, lng, float(punto.latitud), float(punto.longitud))
        if distancia <= punto.radio_metros:
            UbicacionVehiculo.objects.filter(pk=ubicacion.pk).update(en_retorno=True)
            ubicacion.en_retorno = True
            return True, punto

    return False, None


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


def _resolver_marcacion_por_ubicacion(salida, lat, lng, ahora, *, en_retorno=False):
    fase_objetivo = PuntoControl.FASE_RETORNO if en_retorno else PuntoControl.FASE_IDA
    pendientes = [
        marcacion
        for marcacion in salida.marcaciones_pendientes()
        if getattr(marcacion.punto, "fase", PuntoControl.FASE_IDA) == fase_objetivo
    ]
    if not pendientes:
        return None, [], None

    coincidencia = None
    for marcacion in pendientes:
        punto = marcacion.punto
        distancia = distancia_metros(lat, lng, float(punto.latitud), float(punto.longitud))
        if distancia <= punto.radio_metros:
            coincidencia = marcacion
            break

    if not coincidencia:
        return pendientes[0], [], None

    pendientes_previas = [
        marcacion
        for marcacion in pendientes
        if marcacion.punto.orden < coincidencia.punto.orden
    ]

    punto_esperado = pendientes[0]

    # En rutas con contexto de retorno solo deben bloquearse los puntos
    # realmente pertenecientes a la fase RET. Los puntos de ida pueden
    # coexistir con ordenes altos y no deben frenarse por compartir corredor.
    if (
        not en_retorno
        and _ruta_tiene_contexto_vuelta(salida.ruta)
        and getattr(punto_esperado.punto, "fase", PuntoControl.FASE_IDA) == PuntoControl.FASE_RETORNO
    ):
        return punto_esperado, [], coincidencia

    # En el tramo inicial la señal puede fallar y permitimos recuperar un solo
    # punto perdido. Desde ZAMA en adelante la ruta comparte radios entre
    # subida y bajada, asi que se bloquea cualquier salto automatico.
    fase_esperada = getattr(punto_esperado.punto, "fase", PuntoControl.FASE_IDA)

    if fase_esperada != PuntoControl.FASE_RETORNO:
        if punto_esperado.punto.orden >= 4 and pendientes_previas:
            return punto_esperado, [], coincidencia

        if len(pendientes_previas) > 1:
            return punto_esperado, [], coincidencia

    for punto_previo in pendientes_previas:
        hora_previa = punto_previo.hora_programada or punto_previo.calcular_hora_programada()
        if hora_previa and ahora < hora_previa:
            return punto_esperado, [], coincidencia

    omitidas = []
    for marcacion in pendientes_previas:
        marcacion.marcar_omitida(hora=ahora)
        omitidas.append(marcacion)

    return coincidencia, omitidas, None


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

    salida = _obtener_salida_operativa_sesion(
        sesion,
        hoy=hoy,
        require_hour=False,
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

    ubicacion_actual = _get_ubicacion_actual(sesion.vehiculo)
    en_retorno_actual = bool(ubicacion_actual.en_retorno) if ubicacion_actual else False

    marcacion, omitidas, punto_bloqueado = _resolver_marcacion_por_ubicacion(
        salida=salida,
        lat=lat,
        lng=lng,
        ahora=ahora,
        en_retorno=en_retorno_actual,
    )

    punto = marcacion.punto if marcacion else None
    distancia_marcacion = (
        distancia_metros(lat, lng, float(punto.latitud), float(punto.longitud))
        if punto is not None else None
    )
    esta_sobre_marcacion = (
        punto is not None
        and distancia_marcacion is not None
        and distancia_marcacion <= punto.radio_metros
    )

    if not esta_sobre_marcacion:
        en_retorno, punto_contexto_vuelta = _sincronizar_fase_retorno(salida, sesion, lat, lng)
        if punto_contexto_vuelta is not None:
            registrar_punto_evento_confirmado(sesion, punto_contexto_vuelta, ahora)
            return JsonResponse({
                "accion": "beep",
                "motivo": "punto_contexto_vuelta",
                "bloqueado": {
                    "codigo": _codigo_audio_punto(punto_contexto_vuelta),
                    "nombre": _nombre_audio_punto(punto_contexto_vuelta),
                },
                "cola_contexto": _construir_cola_contexto_payload(sesion, ahora=ahora),
            })

        fase_objetivo = PuntoControl.FASE_RETORNO if en_retorno else PuntoControl.FASE_IDA
        ubicacion_actual = _get_ubicacion_actual(sesion.vehiculo)
        orden_minimo_evento = 1
        if ubicacion_actual is not None:
            orden_minimo_evento = max(orden_minimo_evento, int(ubicacion_actual.ultimo_punto_evento_orden or 0))
        punto_evento_actual = _resolver_punto_evento_actual(
            ubicacion_actual,
            _serializar_puntos_ruta(salida.ruta),
            orden_minimo_evento,
            fase_objetivo=fase_objetivo,
        )
        if punto_evento_actual is not None:
            registrar_punto_evento_confirmado(sesion, punto_evento_actual, ahora)
    else:
        en_retorno = en_retorno_actual

    if punto_bloqueado is not None:
        registrar_punto_evento_confirmado(sesion, punto_bloqueado.punto, ahora)
        return JsonResponse({
            "accion": "beep",
            "motivo": "punto_bloqueado",
            "esperado": {
                "codigo": marcacion.punto.codigo,
                "nombre": marcacion.punto.nombre,
            },
            "bloqueado": {
                "codigo": punto_bloqueado.punto.codigo,
                "nombre": punto_bloqueado.punto.nombre,
            },
            "cola_contexto": _construir_cola_contexto_payload(sesion, ahora=ahora),
        })

    if not marcacion:
        marcacion_global_pendiente = salida.siguiente_marcacion()
        if marcacion_global_pendiente is not None:
            fase_global = getattr(
                marcacion_global_pendiente.punto,
                "fase",
                PuntoControl.FASE_IDA,
            )
            if (
                ubicacion_actual is not None
                and bool(getattr(ubicacion_actual, "en_retorno", False))
                and fase_global != PuntoControl.FASE_RETORNO
            ):
                UbicacionVehiculo.objects.filter(pk=ubicacion_actual.pk).update(
                    en_retorno=False
                )
            return JsonResponse({
                "accion": "ninguna",
                "motivo": "pendiente_en_otra_fase",
            })

        if not salida.hora_real_salida:
            return JsonResponse({"accion": "ninguna"})

        salida.activo = False
        salida.en_cola = False
        salida.save(update_fields=["activo", "en_cola"])
        return JsonResponse({
            "accion": "audio",
            "audio": "ruta_completada",
            "finalizada": True,
            "audio_texto": "RUTA FINALIZADA. BUEN TRABAJO",
        })

    punto = marcacion.punto
    distancia = distancia_marcacion if distancia_marcacion is not None else distancia_metros(
        lat, lng, float(punto.latitud), float(punto.longitud)
    )
    if distancia > punto.radio_metros:
        return JsonResponse({"accion": "ninguna"})

    if marcacion.hora_marcada:
        delta = ahora - marcacion.hora_marcada
        if delta.total_seconds() < 10:
            return JsonResponse({"accion": "ninguna"})

    if not salida.hora_real_salida:
        resetear_contexto_inicio_ruta(sesion)
        salida.hora_real_salida = ahora
        salida.en_cola = False
        salida.activo = True
        salida.save(update_fields=["hora_real_salida", "en_cola", "activo"])
        if sesion.salida_id != salida.id:
            sesion.salida = salida
            sesion.save(update_fields=["salida"])

    marcacion.marcar(hora=ahora)
    registrar_punto_evento_confirmado(sesion, punto, ahora)
    es_ultimo_punto = salida.siguiente_marcacion() is None

    return JsonResponse({
        "accion": "audio" if (marcacion.audio_flag or es_ultimo_punto) else "visual",
        "audio": "ruta_completada" if es_ultimo_punto else marcacion.audio_flag,
        "finalizada": es_ultimo_punto,
        "audio_texto": _construir_audio_texto_marcacion(
            marcacion,
            finalizada=es_ultimo_punto,
        ),
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
        "cola_contexto": _construir_cola_contexto_payload(sesion, ahora=ahora),
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
    rumbo = data.get("rumbo", data.get("direccion", data.get("bearing")))

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

    try:
        rumbo = float(rumbo) if rumbo is not None else None
    except (TypeError, ValueError):
        rumbo = None

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
        rumbo=rumbo,
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
    token = _resolver_token_staff_request(request)
    if not token:
        return JsonResponse({"ok": False, "mensaje": "Token no enviado."}, status=401)
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


@require_GET
def app_gerencia_mapa_vivo(request):
    token = _resolver_token_staff_request(request)
    if not token:
        return JsonResponse({"ok": False, "mensaje": "Token no enviado."}, status=401)

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

    return render(
        request,
        "flota_app/gerencia/mapa_vivo.html",
        {
            "MAPBOX_TOKEN": settings.MAPBOX_TOKEN,
            "empresa": empresa.nombre,
            "staff_token": token,
            "rutas_json": json.dumps(rutas),
        },
    )


@require_GET
def api_app_gerencia_salidas(request, vehiculo_id):
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

    vehiculo = get_object_or_404(
        Vehiculo.objects.for_empresa(empresa).filter(activo=True),
        id=vehiculo_id,
    )
    context = _construir_reporte_salidas_diarias_contexto(
        empresa=empresa,
        vehiculo_id=vehiculo.id,
        fecha_param=request.GET.get("fecha"),
    )

    return JsonResponse(
        {
            "ok": True,
            "fecha": context["fecha"].isoformat(),
            "vehiculo": {
                "id": vehiculo.id,
                "codigo": vehiculo.codigo,
                "placa": vehiculo.placa,
            },
            "resumen": {
                "total_vueltas": context["total_vueltas"],
                "promedio_marcacion": context["promedio_marcacion"],
                "minutos_totales": context["minutos_totales"],
                "alertas": context["alertas"],
            },
            "salidas": [_serializar_reporte_item(item) for item in context["salidas"]],
        }
    )


@login_required
@empresa_required
@require_GET
def api_puntos_control(request):
    empresa = request.empresa
    puntos = (
        PuntoControl.objects.for_empresa(empresa)
        .filter(activo=True)
        .exclude(es_contexto_interno=True)
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
    salida_id = request.GET.get("salida", "").strip()
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
        .select_related("ruta")
        .order_by("hora_real_salida", "hora_salida", "id")
    )
    ventanas = _construir_ventanas_recorrido(salidas, fecha_dt)
    salida_filtrada_id = None

    if salida_id:
        salida_filtrada = next(
            (ventana["salida"] for ventana in ventanas if str(ventana["salida"].id) == salida_id),
            None,
        )
        if not salida_filtrada:
            return JsonResponse({"error": "Salida invalida para esa fecha"}, status=400)
        salida_filtrada_id = salida_filtrada.id

    paradas_qs = (
        Parada.objects.for_empresa(empresa)
        .filter(vehiculo_id=vehiculo_id, inicio__date=fecha_dt)
        .order_by("inicio")
    )
    paradas_por_salida = {ventana["salida"].id: [] for ventana in ventanas}
    paradas_sin_ventana = []
    for parada in paradas_qs:
        ventana = _ventana_asociada_a_parada(parada, ventanas)
        if ventana:
            paradas_por_salida.setdefault(ventana["salida"].id, []).append(parada)
        elif not ventanas:
            paradas_sin_ventana.append(parada)

    if not salidas:
        return JsonResponse({
            "gps": [],
            "paradas": [],
            "rutas": [],
            "salidas": [],
            "meta": {
                "gps_total": 0,
                "gps_visible": 0,
                "gps_recortado": False,
                "salidas_con_gps": 0,
            },
        })

    rutas_payload = []
    rutas_vistas = set()
    data = []
    paradas_payload = []
    salidas_payload = []
    gps_total = 0
    salidas_con_gps = 0
    for indice, ventana in enumerate(ventanas, start=1):
        salida = ventana["salida"]
        ruta = salida.ruta
        if ruta and ruta.id not in rutas_vistas:
            rutas_payload.append({
                "id": ruta.id,
                "nombre": ruta.nombre,
                "geometria": _serializar_geometria_ruta(ruta),
                "puntos": _serializar_puntos_ruta(ruta, incluir_contexto_interno=True),
            })
            rutas_vistas.add(ruta.id)

        registros = list(
            GPSRegistro.objects.filter(
            sesion__vehiculo_id=vehiculo_id,
            timestamp__gte=ventana["inicio"],
            timestamp__lte=ventana["fin"],
        ).order_by("timestamp")
        )
        gps_total += len(registros)
        registros_visibles = _muestrear_registros_recorrido_por_intervalo(
            registros,
            RECORRIDO_SAMPLE_SECONDS,
        )
        paradas_salida = paradas_por_salida.get(salida.id, [])
        ultimo_gps = registros[-1].timestamp if registros else None
        fin_resumen = ultimo_gps or _ultimo_fin_paradas(paradas_salida) or ventana["fin"]
        salida_payload = {
            "id": salida.id,
            "indice": indice,
            "titulo": f"Vuelta {indice}",
            "ruta": ruta.nombre if ruta else "",
            "hora_programada": _format_hora(salida.hora_salida),
            "hora_inicio": _format_hora(salida.hora_real_salida),
            "hora_fin": _format_hora(fin_resumen),
            "gps_total": len(registros),
            "gps_visible": len(registros_visibles),
            "gps_recortado": len(registros) > len(registros_visibles),
            "paradas_total": len(paradas_salida),
            "activa": bool(salida.activo),
            "detalle_url": reverse("detalle_salida", args=[salida.id]),
            "control_url": reverse("control_ruta", args=[salida.id]),
        }
        salidas_payload.append(salida_payload)

        if salida_filtrada_id and salida.id != salida_filtrada_id:
            continue
        if registros_visibles:
            salidas_con_gps += 1

        for registro in registros_visibles:
            data.append({
                "lat": registro.lat,
                "lng": registro.lng,
                "hora": timezone.localtime(registro.timestamp).strftime("%H:%M:%S"),
                "timestamp": registro.timestamp.isoformat(),
                "velocidad": registro.velocidad or 0,
                "salida_id": salida.id,
                "ruta_id": ruta.id if ruta else None,
            })
        for parada in paradas_salida:
            paradas_payload.append({
                "lat": parada.lat,
                "lng": parada.lng,
                "inicio": timezone.localtime(parada.inicio).strftime("%H:%M:%S"),
                "fin": timezone.localtime(parada.fin).strftime("%H:%M:%S") if parada.fin else None,
                "duracion_min": int(parada.duracion_segundos / 60),
                "activa": parada.activa,
                "salida_id": salida.id,
            })

    return JsonResponse({
        "gps": data,
        "paradas": paradas_payload,
        "rutas": rutas_payload,
        "salidas": salidas_payload,
        "meta": {
            "gps_total": gps_total,
            "gps_visible": len(data),
            "gps_recortado": gps_total > len(data),
            "salidas_con_gps": salidas_con_gps,
            "salida_filtrada_id": salida_filtrada_id,
        },
    })


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
    salidas = list(
        RegistroSalida.objects.for_empresa(empresa)
        .filter(vehiculo_id=vehiculo_id, fecha=fecha_dt)
        .order_by("hora_real_salida", "hora_salida", "id")
    )
    ventanas = _construir_ventanas_recorrido(salidas, fecha_dt)

    paradas = (
        Parada.objects.for_empresa(empresa)
        .filter(vehiculo_id=vehiculo_id, inicio__date=fecha_dt)
        .order_by("inicio")
    )

    data = []
    for parada in paradas:
        ventana = _ventana_asociada_a_parada(parada, ventanas)
        if ventanas and not ventana:
            continue
        data.append({
            "lat": parada.lat,
            "lng": parada.lng,
            "inicio": parada.inicio.strftime("%H:%M:%S"),
            "fin": parada.fin.strftime("%H:%M:%S") if parada.fin else None,
            "duracion_min": int(parada.duracion_segundos / 60),
            "activa": parada.activa,
            "salida_id": ventana["salida"].id if ventana else None,
        })

    return JsonResponse(data, safe=False)


def _fin_operativo_para_fecha(fecha_dt):
    fin_dia_local = datetime.combine(fecha_dt + timedelta(days=1), time.min)
    fin_dia = timezone.make_aware(fin_dia_local, timezone.get_current_timezone())
    return min(fin_dia, timezone.now()) if fecha_dt == timezone.localdate() else fin_dia


def _construir_ventanas_recorrido(salidas, fecha_dt):
    ventanas = []
    salidas_validas = [salida for salida in salidas if salida.hora_real_salida]
    if not salidas_validas:
        return ventanas

    fin_operativo = _fin_operativo_para_fecha(fecha_dt)
    for index, salida in enumerate(salidas_validas):
        inicio = salida.hora_real_salida
        siguiente = (
            salidas_validas[index + 1].hora_real_salida
            if index + 1 < len(salidas_validas)
            else None
        )
        fin = siguiente or fin_operativo
        if fin <= inicio:
            fin = inicio + timedelta(seconds=1)
        ventanas.append({
            "salida": salida,
            "inicio": inicio,
            "fin": fin,
        })
    return ventanas


def _muestrear_registros_recorrido_por_intervalo(registros, interval_seconds):
    total = len(registros)
    if total <= 2:
        return registros
    intervalo = timedelta(seconds=max(interval_seconds, 1))
    muestreados = [registros[0]]
    ultimo_incluido = registros[0].timestamp

    for registro in registros[1:-1]:
        if registro.timestamp - ultimo_incluido >= intervalo:
            muestreados.append(registro)
            ultimo_incluido = registro.timestamp

    if muestreados[-1].pk != registros[-1].pk:
        muestreados.append(registros[-1])
    return muestreados


def _ventana_asociada_a_parada(parada, ventanas):
    if not ventanas:
        return None

    fin_parada = parada.fin or parada.inicio
    for ventana in ventanas:
        if parada.inicio < ventana["fin"] and fin_parada >= ventana["inicio"]:
            return ventana
    return None


def _ultimo_fin_paradas(paradas):
    if not paradas:
        return None
    return max((parada.fin or parada.inicio) for parada in paradas)


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


def _coords_geometria_para_progreso(ruta):
    geometria = _serializar_geometria_ruta(ruta)
    if geometria:
        return geometria

    puntos = _serializar_puntos_ruta(ruta)
    return [[punto["lat"], punto["lng"]] for punto in puntos]


def _project_route_progress(lat, lng, geometria):
    if len(geometria) < 2:
        return None

    ref_lat = math.radians(sum(coord[0] for coord in geometria) / len(geometria))
    scale_x = math.cos(ref_lat)

    def to_xy(coord_lat, coord_lng):
        return (coord_lng * scale_x, coord_lat)

    objetivo_x, objetivo_y = to_xy(lat, lng)
    recorrido = 0.0
    mejor_recorrido = None
    mejor_distancia = None

    for index in range(len(geometria) - 1):
        start_lat, start_lng = geometria[index]
        end_lat, end_lng = geometria[index + 1]
        start_x, start_y = to_xy(start_lat, start_lng)
        end_x, end_y = to_xy(end_lat, end_lng)
        seg_x = end_x - start_x
        seg_y = end_y - start_y
        seg_len_sq = (seg_x * seg_x) + (seg_y * seg_y)
        if seg_len_sq <= 0:
            continue

        rel_x = objetivo_x - start_x
        rel_y = objetivo_y - start_y
        t = max(0.0, min(1.0, ((rel_x * seg_x) + (rel_y * seg_y)) / seg_len_sq))
        proj_x = start_x + (seg_x * t)
        proj_y = start_y + (seg_y * t)
        distancia_sq = ((objetivo_x - proj_x) ** 2) + ((objetivo_y - proj_y) ** 2)
        seg_len = math.sqrt(seg_len_sq)
        progreso = recorrido + (seg_len * t)

        if mejor_distancia is None or distancia_sq < mejor_distancia:
            mejor_distancia = distancia_sq
            mejor_recorrido = progreso

        recorrido += seg_len

    return mejor_recorrido


def _obtener_punto_siguiente(puntos, orden_actual):
    for punto in puntos:
        if punto["orden"] > orden_actual:
            return punto
    return None


def _serializar_puntos_ruta(ruta, *, incluir_contexto_interno=False):
    if not ruta:
        return []

    puntos = PuntoControl.objects.filter(ruta=ruta, activo=True)
    if not incluir_contexto_interno:
        puntos = puntos.exclude(es_contexto_interno=True)
    puntos = puntos.order_by("orden")

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
            "fase": punto.fase,
            "requiere_marcacion": punto.requiere_marcacion,
            "confirma_avance": punto.confirma_avance,
            "es_contexto_interno": punto.es_contexto_interno,
        })
    return data


def _resolver_punto_evento_actual(ubicacion, puntos_ruta, orden_minimo, *, fase_objetivo=None):
    if not ubicacion:
        return None

    candidatos = []
    for punto in puntos_ruta:
        if punto["orden"] < max(orden_minimo, 1):
            continue
        if fase_objetivo and str(punto.get("fase") or PuntoControl.FASE_IDA).strip().upper() != str(fase_objetivo).strip().upper():
            continue
        if not _es_punto_evento_confirmado(punto):
            continue
        distancia = distancia_metros(
            ubicacion.latitud,
            ubicacion.longitud,
            punto["lat"],
            punto["lng"],
        )
        if distancia <= punto["radio"]:
            candidatos.append((punto["orden"], distancia, punto))

    if not candidatos:
        return None

    # Dentro de la fase actual la referencia viva debe avanzar al punto mas
    # alto alcanzado por GPS. Con la fase ya filtrada, esto evita que la
    # unidad se quede pegada en MUNI cuando ya esta entrando a LLAM.
    _, _, punto_evento = max(candidatos, key=lambda item: (item[0], -item[1]))
    return punto_evento


def _obtener_salida_operativa_sesion(sesion, *, hoy=None, require_hour=False):
    hoy = hoy or timezone.localdate()
    salidas_qs = RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa).select_related(
        "vehiculo",
        "ruta",
    )

    if sesion.salida_id:
        salida_sesion = salidas_qs.filter(
            id=sesion.salida_id,
            vehiculo=sesion.vehiculo,
            fecha=hoy,
            activo=True,
        ).first()
        if salida_sesion and (not require_hour or salida_sesion.hora_salida):
            return salida_sesion

    fallback_qs = salidas_qs.filter(
        vehiculo=sesion.vehiculo,
        fecha=hoy,
        activo=True,
    )
    if require_hour:
        fallback_qs = fallback_qs.filter(hora_salida__isnull=False)

    return fallback_qs.order_by("-id").first()


def _construir_cola_contexto_payload(sesion, ahora=None):
    ahora = ahora or timezone.now()
    hoy = timezone.localdate()
    salida_actual = _obtener_salida_operativa_sesion(
        sesion,
        hoy=hoy,
        require_hour=True,
    )
    if not salida_actual:
        return {"ok": False}

    if salida_actual.finalizar_por_inactividad(ahora=ahora):
        return {"ok": False}

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

    gps_max_delay = timedelta(seconds=60)
    velocidad_promedio = 25
    puntos_ruta = _serializar_puntos_ruta(salida_actual.ruta)

    try:
        ub_actual = UbicacionVehiculo.objects.get(vehiculo=salida_actual.vehiculo)
    except UbicacionVehiculo.DoesNotExist:
        ub_actual = None

    ubicaciones_map = {
        ubicacion.vehiculo_id: ubicacion
        for ubicacion in UbicacionVehiculo.objects.filter(
            vehiculo__in=[salida.vehiculo for salida in cola]
        )
    }
    ultimo_punto_map = {}
    ultimo_punto_orden_map = {}
    ultimo_punto_hora_map = {}
    for marcacion in (
        MarcacionPunto.objects.filter(
            registro_salida__in=cola,
            hora_marcada__isnull=False,
        )
        .select_related("punto")
        .order_by("registro_salida_id", "punto__orden")
    ):
        ultimo_punto_map[marcacion.registro_salida_id] = marcacion.punto.codigo
        ultimo_punto_orden_map[marcacion.registro_salida_id] = marcacion.punto.orden
        ultimo_punto_hora_map[marcacion.registro_salida_id] = marcacion.hora_marcada

    def salida_tiene_inicio_confirmado(salida):
        return (ultimo_punto_orden_map.get(salida.id) or 0) >= 1

    def ubicacion_fresca(salida):
        ubicacion = ubicaciones_map.get(salida.vehiculo.id)
        if not ubicacion or ahora - ubicacion.updated_at > gps_max_delay:
            return None
        return ubicacion

    def calcular_referencia_confirmada(salida):
        ultimo_codigo = ultimo_punto_map.get(salida.id)
        ultimo_orden = ultimo_punto_orden_map.get(salida.id) or 0
        ultimo_hora = ultimo_punto_hora_map.get(salida.id)
        ubicacion = ubicacion_fresca(salida)
        referencia_codigo = ultimo_codigo
        referencia_orden = ultimo_orden
        orden_confirmado = ultimo_orden
        instante_progreso = ultimo_hora

        if ubicacion:
            orden_evento = ubicacion.ultimo_punto_evento_orden or 0
            codigo_evento = (ubicacion.ultimo_punto_evento_codigo or "").strip().upper() or None
            fase_objetivo = PuntoControl.FASE_RETORNO if bool(getattr(ubicacion, "en_retorno", False)) else PuntoControl.FASE_IDA
            if orden_evento > orden_confirmado and codigo_evento:
                orden_confirmado = orden_evento
                instante_progreso = ubicacion.ultimo_punto_evento_at or instante_progreso
            if orden_evento > referencia_orden and codigo_evento:
                referencia_orden = orden_evento
                referencia_codigo = codigo_evento
                instante_progreso = ubicacion.ultimo_punto_evento_at or instante_progreso

            punto_evento_actual = _resolver_punto_evento_actual(
                ubicacion,
                puntos_ruta,
                max(ultimo_orden, orden_evento),
                fase_objetivo=fase_objetivo,
            )
            if punto_evento_actual and punto_evento_actual["orden"] > referencia_orden:
                referencia_orden = punto_evento_actual["orden"]
                referencia_codigo = punto_evento_actual["codigo"]
                if punto_evento_actual["orden"] > orden_confirmado:
                    orden_confirmado = punto_evento_actual["orden"]
                instante_progreso = ubicacion.updated_at or instante_progreso

        return {
            "codigo": referencia_codigo,
            "audio_codigo": referencia_codigo,
            "orden_marcado": ultimo_orden,
            "orden_confirmado": orden_confirmado,
            "orden_referencia": referencia_orden,
            "instante_progreso": instante_progreso,
        }

    referencias_map = {
        salida.id: calcular_referencia_confirmada(salida)
        for salida in cola
    }

    if not salida_tiene_inicio_confirmado(salida_actual):
        return {
            "ok": True,
            "actual": {
                "unidad": salida_actual.vehiculo.codigo,
                "minutos": 0,
                "punto_actual_codigo": ultimo_punto_map.get(salida_actual.id),
                "punto_referencia_codigo": referencias_map.get(salida_actual.id, {}).get("codigo"),
                "punto_audio_referencia_codigo": referencias_map.get(salida_actual.id, {}).get("audio_codigo"),
            },
            "adelante": [],
            "atras": [],
        }

    cola = [
        salida for salida in cola
        if salida.id == salida_actual.id or salida_tiene_inicio_confirmado(salida)
    ]

    def calcular_avance_confirmado(salida):
        referencia = referencias_map.get(salida.id, {})
        orden_marcado = referencia.get("orden_marcado") or 0
        orden_confirmado = referencia.get("orden_confirmado") or 0
        orden_referencia = referencia.get("orden_referencia") or 0
        instante_progreso = referencia.get("instante_progreso")
        hora_base = salida.hora_real_salida or salida.hora_salida or salida.hora_llegada
        hora_key = float(hora_base.timestamp()) if hora_base else 0.0
        progreso_key = float(instante_progreso.timestamp()) if instante_progreso else hora_key

        if not salida_tiene_inicio_confirmado(salida):
            hora_programada = salida.hora_salida or salida.hora_llegada
            programada_key = float(hora_programada.timestamp()) if hora_programada else 0.0
            return (1, 0.0, programada_key)

        # La jerarquia operativa debe respetar primero la ultima marcacion real
        # confirmada. Referencias/eventos internos ayudan a desempatar dentro de
        # la misma fase, pero no deben hacer que una unidad con solo SALI
        # adelantada por contexto opaque a otra que ya confirmo COLE/APIP/etc.
        # Pero si dos unidades comparten la misma ultima marcacion real, la que
        # ya confirmo una referencia mas avanzada dentro de la misma fase debe
        # quedar adelante para reflejar el sobrepaso real en puntos de paso.
        return (
            0,
            -float(orden_marcado),
            -float(orden_confirmado),
            -float(orden_referencia),
            progreso_key,
            hora_key,
        )

    cola_ordenada = sorted(cola, key=calcular_avance_confirmado)
    index_actual = cola_ordenada.index(salida_actual)

    adelante = cola_ordenada[:index_actual][-2:]
    atras = cola_ordenada[index_actual + 1:index_actual + 3]

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
        referencia = referencias_map.get(salida.id, {})
        return {
            "unidad": salida.vehiculo.codigo,
            "minutos": calcular_minutos(salida),
            "punto_actual_codigo": ultimo_punto_map.get(salida.id),
            "punto_referencia_codigo": referencia.get("codigo"),
            "punto_audio_referencia_codigo": referencia.get("audio_codigo"),
        }

    return {
        "ok": True,
        "actual": {
            "unidad": salida_actual.vehiculo.codigo,
            "minutos": 0,
            "punto_actual_codigo": ultimo_punto_map.get(salida_actual.id),
            "punto_referencia_codigo": referencias_map.get(salida_actual.id, {}).get("codigo"),
            "punto_audio_referencia_codigo": referencias_map.get(salida_actual.id, {}).get("audio_codigo"),
        },
        "adelante": [serializar(salida) for salida in reversed(adelante)],
        "atras": [serializar(salida) for salida in atras],
    }


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


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "si", "sí", "yes", "on"}


def _as_optional_int(value):
    if value in (None, "", "null"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_optional_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


@csrf_exempt
@require_POST
def api_app_device_status(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        response = JsonResponse(
            {"ok": False, "motivo": "TOKEN_REQUERIDO"},
            status=401,
        )
        return _disable_cache(response)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        response = JsonResponse(
            {"ok": False, "motivo": "SESION_INVALIDA"},
            status=403,
        )
        return _disable_cache(response)

    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        response = JsonResponse(
            {"ok": False, "motivo": "JSON_INVALIDO"},
            status=400,
        )
        return _disable_cache(response)

    device_role = str(request.headers.get("X-Device-Role") or "").strip().lower()
    defaults = {
        "wifi_conectado": _as_bool(data.get("wifi_conectado")),
        "wifi_ssid": str(data.get("wifi_ssid") or "").strip(),
        "internet_disponible": _as_bool(data.get("internet_disponible")),
        "gps_activo": _as_bool(data.get("gps_activo")),
        "bateria_porcentaje": _as_optional_int(data.get("bateria_porcentaje")),
        "ip_local": str(data.get("ip_local") or "").strip() or None,
        "android_version": str(data.get("android_version") or "").strip(),
        "device_model": str(data.get("device_model") or "").strip(),
        "ultimo_reinicio_en": _as_optional_datetime(data.get("ultimo_reinicio_en")),
    }
    if device_role == "admin":
        defaults.update({
            "device_owner_activo": _as_bool(data.get("device_owner_activo")),
            "admin_home_activo": _as_bool(data.get("admin_home_activo")),
            "admin_app_version": str(data.get("admin_app_version") or data.get("app_version") or "").strip(),
            "admin_app_version_code": str(data.get("admin_app_version_code") or data.get("app_version_code") or "").strip(),
            "admin_ultimo_estado": str(data.get("admin_ultimo_estado") or "").strip(),
            "admin_reportado_en": timezone.now(),
        })
    else:
        defaults.update({
            "kiosco_activo": _as_bool(data.get("kiosco_activo")),
            "pantalla_fija_activa": _as_bool(data.get("pantalla_fija_activa")),
            "app_version": str(data.get("app_version") or "").strip(),
            "app_version_code": str(data.get("app_version_code") or "").strip(),
        })

    estado, _ = EstadoDispositivo.objects.update_or_create(
        vehiculo=sesion.vehiculo,
        defaults=defaults,
    )

    respuesta = {
        "ok": True,
        "vehiculo": sesion.vehiculo.codigo,
        "reportado_en": estado.reportado_en.isoformat(),
    }
    return _disable_cache(JsonResponse(respuesta))


@csrf_exempt
@require_POST
def api_app_command_pull(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        response = JsonResponse({"ok": False, "motivo": "TOKEN_REQUERIDO"}, status=401)
        return _disable_cache(response)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        response = JsonResponse({"ok": False, "motivo": "SESION_INVALIDA"}, status=403)
        return _disable_cache(response)

    device_role = str(request.headers.get("X-Device-Role") or "").strip().lower()
    if device_role == "admin":
        allowed_types = [
            ComandoDispositivo.TIPO_REABRIR_APP,
            ComandoDispositivo.TIPO_ACTIVAR_KIOSCO,
            ComandoDispositivo.TIPO_SALIR_KIOSCO,
            ComandoDispositivo.TIPO_ABRIR_WIFI_TECNICO,
            ComandoDispositivo.TIPO_ACTUALIZAR_OPERATIVA,
            ComandoDispositivo.TIPO_ACTUALIZAR_ADMIN,
        ]
    else:
        allowed_types = [
            ComandoDispositivo.TIPO_REPORTAR_ESTADO,
            ComandoDispositivo.TIPO_FORZAR_SYNC,
        ]

    comando = (
        ComandoDispositivo.objects
        .filter(
            vehiculo=sesion.vehiculo,
            estado=ComandoDispositivo.ESTADO_PENDIENTE,
            tipo__in=allowed_types,
        )
        .order_by("solicitado_en", "id")
        .first()
    )

    if not comando:
        return _disable_cache(JsonResponse({"ok": True, "comando": None}))

    ahora = timezone.now()
    comando.estado = ComandoDispositivo.ESTADO_ENTREGADO
    comando.entregado_en = ahora
    comando.save(update_fields=["estado", "entregado_en", "actualizado_en"])

    return _disable_cache(JsonResponse({
        "ok": True,
        "comando": {
            "id": comando.id,
            "tipo": comando.tipo,
            "payload": comando.payload,
            "nota": comando.nota,
            "solicitado_en": comando.solicitado_en.isoformat(),
        }
    }))


@csrf_exempt
@require_POST
def api_app_command_ack(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        response = JsonResponse({"ok": False, "motivo": "TOKEN_REQUERIDO"}, status=401)
        return _disable_cache(response)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        response = JsonResponse({"ok": False, "motivo": "SESION_INVALIDA"}, status=403)
        return _disable_cache(response)

    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        response = JsonResponse({"ok": False, "motivo": "JSON_INVALIDO"}, status=400)
        return _disable_cache(response)

    comando_id = data.get("comando_id")
    resultado = str(data.get("resultado") or "").strip().upper()
    detalle_error = str(data.get("detalle_error") or "").strip()

    if not comando_id:
        response = JsonResponse({"ok": False, "motivo": "COMANDO_REQUERIDO"}, status=400)
        return _disable_cache(response)

    try:
        comando = ComandoDispositivo.objects.get(id=comando_id, vehiculo=sesion.vehiculo)
    except ComandoDispositivo.DoesNotExist:
        response = JsonResponse({"ok": False, "motivo": "COMANDO_NO_ENCONTRADO"}, status=404)
        return _disable_cache(response)

    if resultado == "APLICADO":
        comando.estado = ComandoDispositivo.ESTADO_APLICADO
        comando.aplicado_en = timezone.now()
        comando.detalle_error = ""
        comando.save(update_fields=["estado", "aplicado_en", "detalle_error", "actualizado_en"])
    elif resultado == "ERROR":
        comando.estado = ComandoDispositivo.ESTADO_ERROR
        comando.detalle_error = detalle_error
        comando.save(update_fields=["estado", "detalle_error", "actualizado_en"])
    else:
        response = JsonResponse({"ok": False, "motivo": "RESULTADO_INVALIDO"}, status=400)
        return _disable_cache(response)

    return _disable_cache(JsonResponse({"ok": True, "estado": comando.estado}))


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
    siguiente = salida.siguiente_marcacion()
    punto = punto_qs.filter(id=punto_id).first() if punto_id else None
    if punto and siguiente and punto.id != siguiente.punto_id:
        return JsonResponse(
            {
                "ok": False,
                "mensaje": "Punto fuera de secuencia",
                "esperado": {
                    "id": siguiente.punto_id,
                    "codigo": siguiente.punto.codigo,
                    "nombre": siguiente.punto.nombre,
                },
            },
            status=409,
        )
    if not punto:
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
    return JsonResponse(_construir_cola_contexto_payload(sesion))


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
def api_historial_salidas(request):
    context = _construir_historial_salidas_contexto(
        empresa=request.empresa,
        desde=request.GET.get("desde", "").strip(),
        hasta=request.GET.get("hasta", "").strip(),
        ruta_id=request.GET.get("ruta", "").strip(),
    )
    return JsonResponse(
        {
            "ok": True,
            "resumen": context["resumen"],
            "historial": [_serializar_historial_item(item) for item in context["historial"]],
            "errores": context["errores"],
        }
    )


@login_required
@empresa_required
@require_GET
def api_reporte_salidas_diarias(request, vehiculo_id):
    context = _construir_reporte_salidas_diarias_contexto(
        empresa=request.empresa,
        vehiculo_id=vehiculo_id,
        fecha_param=request.GET.get("fecha"),
    )
    return JsonResponse(
        {
            "ok": True,
            "fecha": context["fecha"].isoformat(),
            "resumen": {
                "total_vueltas": context["total_vueltas"],
                "promedio_marcacion": context["promedio_marcacion"],
                "minutos_totales": context["minutos_totales"],
                "alertas": context["alertas"],
            },
            "salidas": [_serializar_reporte_item(item) for item in context["salidas"]],
        }
    )


@login_required
@empresa_required
@require_GET
def api_control_ruta_web(request, salida_id):
    salida = get_object_or_404(
        RegistroSalida.objects.for_empresa(request.empresa).select_related("vehiculo", "ruta"),
        id=salida_id,
    )
    return JsonResponse(_serializar_control_web(_construir_control_ruta_contexto(salida)))


@login_required
@empresa_required
@require_GET
def api_detalle_salida_web(request, salida_id):
    salida = get_object_or_404(
        RegistroSalida.objects.for_empresa(request.empresa).select_related("vehiculo", "ruta"),
        id=salida_id,
    )
    return JsonResponse(_serializar_detalle_web(_calcular_detalle_salida(salida)))


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
