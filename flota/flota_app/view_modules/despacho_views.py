from datetime import datetime, timedelta
from io import BytesIO
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Case, IntegerField, Max, Prefetch, Q, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

import qrcode

from ..decorators import empresa_required
from ..models import (
    ConfiguracionDespacho,
    MarcacionPunto,
    PuntoControl,
    RegistroSalida,
    Ruta,
    UbicacionVehiculo,
    Vehiculo,
)
from ..services import recalcular_cola

__all__ = [
    "asignar_hora_fija",
    "auditoria_horas",
    "buscar_unidad_panel",
    "cambiar_intervalo_global",
    "control_ruta",
    "despachador_mapa",
    "detalle_salida",
    "es_despachador",
    "exportar_excel",
    "historial_salidas",
    "historial_vehiculo",
    "marcar_paso",
    "marcar_siguiente_punto",
    "marcar_siguiente_punto_auto",
    "panel_despachador",
    "panel_frecuencia",
    "poner_en_cola",
    "quitar_de_cola",
    "recorrido_vehiculo",
    "reporte_control",
    "ver_qr_unidad",
    "desbloquear_hora",
]


def es_despachador(user):
    return user.groups.filter(name="despachador").exists() or user.is_superuser


def redirect_panel_despachador(request, ruta_id=None):
    ruta_id = (
        ruta_id
        or request.POST.get("current_ruta_id")
        or request.GET.get("ruta")
        or ""
    )
    fecha = (
        request.POST.get("current_fecha")
        or request.POST.get("fecha_operativa")
        or request.GET.get("fecha")
        or ""
    )
    url = reverse("panel_despachador")
    query_params = []
    if ruta_id:
        query_params.append(f"ruta={ruta_id}")
    if fecha:
        query_params.append(f"fecha={fecha}")
    if query_params:
        url = f"{url}?{'&'.join(query_params)}"
    return redirect(url)


def _parse_fecha_panel(fecha_str):
    if not fecha_str:
        return timezone.localdate()
    try:
        return datetime.strptime(fecha_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Fecha operativa invalida.") from exc


def _parse_hora_panel(hora_str, fecha_base=None):
    if not hora_str:
        raise ValueError("Hora invalida.")

    try:
        hora_time = datetime.strptime(hora_str, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("Formato de hora invalido.") from exc

    fecha_base = fecha_base or timezone.localdate()
    return timezone.make_aware(
        datetime.combine(fecha_base, hora_time),
        timezone.get_current_timezone(),
    )


def _resolver_ruta_panel(empresa, ruta_id):
    rutas = Ruta.objects.for_empresa(empresa).order_by("nombre")
    if ruta_id:
        return rutas.filter(id=ruta_id).first()
    if rutas.count() == 1:
        return rutas.first()
    return None


def _programar_salida(salida, empresa, hora_fija_dt, fecha_operativa=None):
    if salida.hora_real_salida:
        raise ValidationError("No se puede reprogramar la hora: la unidad ya inicio la ruta.")

    salida.fecha = fecha_operativa or salida.fecha or timezone.localdate()
    salida.hora_fija = hora_fija_dt
    salida.hora_salida = hora_fija_dt
    salida.bloqueado = True
    salida.save(update_fields=["fecha", "hora_fija", "hora_salida", "bloqueado"])

    puntos = (
        PuntoControl.objects.for_empresa(empresa)
        .filter(ruta=salida.ruta, requiere_marcacion=True)
        .order_by("orden")
    )
    for punto in puntos:
        MarcacionPunto.objects.get_or_create(registro_salida=salida, punto=punto)

    for marcacion in salida.marcaciones.all():
        marcacion.hora_programada = marcacion.calcular_hora_programada()
        marcacion.save(update_fields=["hora_programada"])


def _construir_panel_despachador_contexto(empresa, fecha_operativa, ruta_id="", ahora=None):
    hoy = timezone.localdate()
    ahora = ahora or timezone.now()
    rutas = list(Ruta.objects.for_empresa(empresa).order_by("nombre"))
    ruta_actual = None

    if ruta_id:
        ruta_actual = next((ruta for ruta in rutas if str(ruta.id) == str(ruta_id)), None)
    elif len(rutas) == 1:
        ruta_actual = rutas[0]

    salidas_qs = (
        RegistroSalida.objects.for_empresa(empresa)
        .select_related("vehiculo", "ruta")
        .filter(
            ruta__isnull=False,
            activo=True,
            fecha=fecha_operativa,
        )
    )

    if ruta_actual:
        salidas_qs = salidas_qs.filter(ruta=ruta_actual)

    salidas_revision = list(salidas_qs)
    finalizadas_por_inactividad = 0
    for salida in salidas_revision:
        if salida.finalizar_por_inactividad(ahora=ahora):
            finalizadas_por_inactividad += 1

    if finalizadas_por_inactividad:
        salidas_qs = salidas_qs.filter(activo=True)

    es_fecha_futura = fecha_operativa > hoy
    salidas = list(
        salidas_qs.order_by(
            Case(
                When(hora_salida__isnull=False, then=0),
                When(hora_salida__isnull=True, then=1),
                output_field=IntegerField(),
            ),
            models.F("hora_salida").asc(nulls_last=True),
            "hora_llegada",
        )
    )

    stats = {
        "activas": len(salidas),
        "programadas": 0,
        "atrasadas": 0,
        "sin_hora": 0,
    }

    for salida in salidas:
        salida.estado_panel = "sin_hora"
        salida.estado_panel_label = "Sin hora"
        salida.estado_panel_class = "sin-hora"
        salida.permite_confirmar = True

        if not salida.hora_salida:
            stats["sin_hora"] += 1
            continue

        if es_fecha_futura or salida.hora_salida > ahora:
            salida.estado_panel = "programado"
            salida.estado_panel_label = "Programada"
            salida.estado_panel_class = "programada"
            stats["programadas"] += 1
        else:
            salida.estado_panel = "atrasado"
            salida.estado_panel_label = "Pendiente de salida"
            salida.estado_panel_class = "atrasada"
            stats["atrasadas"] += 1

    reporte_vehiculo_id = None
    codigos_unidad = list(
        Vehiculo.objects.for_empresa(empresa)
        .filter(activo=True)
        .order_by("codigo")
        .values_list("codigo", flat=True)
    )
    if salidas:
        reporte_vehiculo_id = salidas[0].vehiculo_id
    else:
        vehiculo = (
            Vehiculo.objects.for_empresa(empresa)
            .filter(activo=True)
            .order_by("codigo")
            .first()
        )
        if vehiculo:
            reporte_vehiculo_id = vehiculo.id

    return {
        "salidas": salidas,
        "reporte_vehiculo_id": reporte_vehiculo_id,
        "rutas": rutas,
        "ruta_actual_id": str(ruta_actual.id) if ruta_actual else "",
        "ruta_actual_nombre": ruta_actual.nombre if ruta_actual else "",
        "stats": stats,
        "hora_actual_hhmm": timezone.localtime(ahora).strftime("%H:%M"),
        "fecha_operativa": fecha_operativa,
        "fecha_operativa_iso": fecha_operativa.isoformat(),
        "fecha_es_futura": es_fecha_futura,
        "codigos_unidad": codigos_unidad,
        "finalizadas_por_inactividad": finalizadas_por_inactividad,
    }


def _calcular_detalle_salida(salida):
    marcaciones_qs = (
        MarcacionPunto.objects.filter(registro_salida=salida)
        .select_related("punto")
        .order_by("punto__orden")
    )

    detalle = []
    completados = 0
    pendiente_count = 0

    for marcacion in marcaciones_qs:
        punto = marcacion.punto

        if salida.hora_salida:
            hora_programada = salida.hora_salida + timedelta(minutes=punto.offset_minutos)
        else:
            hora_programada = None

        estado = marcacion.estado or "pendiente"
        diferencia = None

        if marcacion.estado == "omitido":
            diferencia = marcacion.diferencia_minutos
        elif marcacion.hora_marcada and hora_programada:
            diferencia = int((marcacion.hora_marcada - hora_programada).total_seconds() / 60)
            if diferencia < 0:
                estado = "adelantado"
            elif diferencia == 0:
                estado = "a_tiempo"
            else:
                estado = "tarde"

        if marcacion.hora_marcada:
            completados += 1
        else:
            pendiente_count += 1

        detalle.append(
            {
                "punto": punto,
                "hora_programada": hora_programada,
                "hora_marcada": marcacion.hora_marcada,
                "diferencia": diferencia,
                "estado": estado,
            }
        )

    total = len(detalle)
    return {
        "detalle": detalle,
        "resumen": {
            "total": total,
            "completados": completados,
            "pendientes": pendiente_count,
            "porcentaje": int((completados / total) * 100) if total else 0,
        },
    }


def _construir_control_ruta_contexto(salida):
    puntos = (
        PuntoControl.objects
        .filter(ruta=salida.ruta, activo=True, requiere_marcacion=True)
        .order_by("orden")
    )
    controles = []
    completados = 0

    for punto in puntos:
        if salida.hora_salida:
            hora_programada_calculada = salida.hora_salida + timedelta(minutes=punto.offset_minutos)
        else:
            hora_programada_calculada = None

        marcacion, _ = MarcacionPunto.objects.get_or_create(
            registro_salida=salida,
            punto=punto,
            defaults={"hora_programada": hora_programada_calculada},
        )
        if marcacion.hora_marcada:
            completados += 1

        controles.append(
            {
                "punto": punto,
                "marcacion": marcacion,
                "hora_programada_calculada": hora_programada_calculada,
            }
        )

    total = len(controles)
    siguiente = next(
        (
            control for control in controles
            if not control["marcacion"] or not control["marcacion"].hora_marcada
        ),
        None,
    )
    return {
        "salida": salida,
        "controles": controles,
        "resumen": {
            "total": total,
            "completados": completados,
            "pendientes": max(total - completados, 0),
            "porcentaje": int((completados / total) * 100) if total else 0,
            "siguiente": siguiente["punto"] if siguiente else None,
        },
    }


def _construir_historial_salidas_contexto(empresa, desde="", hasta="", ruta_id=""):
    salidas = (
        RegistroSalida.objects.for_empresa(empresa)
        .select_related("vehiculo", "ruta")
        .prefetch_related(
            Prefetch(
                "marcaciones",
                queryset=MarcacionPunto.objects.filter(
                    hora_marcada__isnull=False,
                ).select_related("punto").order_by("hora_marcada"),
                to_attr="marcaciones_registradas",
            )
        )
        .order_by("-fecha", "-hora_salida")
    )
    rutas = Ruta.objects.for_empresa(empresa).order_by("nombre")
    errores = []

    if desde:
        try:
            salidas = salidas.filter(
                fecha__gte=datetime.strptime(desde, "%Y-%m-%d").date()
            )
        except ValueError:
            errores.append("La fecha 'desde' no es valida.")

    if hasta:
        try:
            salidas = salidas.filter(
                fecha__lte=datetime.strptime(hasta, "%Y-%m-%d").date()
            )
        except ValueError:
            errores.append("La fecha 'hasta' no es valida.")

    if ruta_id:
        salidas = salidas.filter(ruta_id=ruta_id)

    historial = []
    estados = {
        "a_tiempo": 0,
        "tarde": 0,
        "adelantado": 0,
        "pendiente": 0,
    }

    for salida in salidas:
        hora_programada = salida.hora_salida
        primera_marcacion = (
            salida.marcaciones_registradas[0]
            if getattr(salida, "marcaciones_registradas", None)
            else None
        )

        hora_marcada = primera_marcacion.hora_marcada if primera_marcacion else None
        estado = "pendiente"
        diferencia = None

        if hora_programada and hora_marcada:
            diferencia = int((hora_marcada - hora_programada).total_seconds() / 60)
            if diferencia < 0:
                estado = "adelantado"
            elif diferencia == 0:
                estado = "a_tiempo"
            else:
                estado = "tarde"

        estados[estado] += 1
        historial.append(
            {
                "salida": salida,
                "programada": hora_programada,
                "marcada": hora_marcada,
                "falta": diferencia,
                "estado": estado,
            }
        )

    return {
        "historial": historial,
        "rutas": rutas,
        "filtros": {
            "desde": desde,
            "hasta": hasta,
            "ruta": ruta_id,
        },
        "resumen": {
            "total": len(historial),
            "a_tiempo": estados["a_tiempo"],
            "tarde": estados["tarde"],
            "adelantado": estados["adelantado"],
            "pendiente": estados["pendiente"],
        },
        "errores": errores,
    }

@login_required
@user_passes_test(es_despachador)
@empresa_required
def panel_despachador(request):
    empresa = request.empresa
    fecha_str = request.GET.get("fecha", "").strip()
    try:
        fecha_operativa = _parse_fecha_panel(fecha_str)
    except ValueError as error:
        messages.error(request, str(error))
        fecha_operativa = timezone.localdate()
    ruta_id = request.GET.get("ruta", "").strip()

    context = _construir_panel_despachador_contexto(
        empresa=empresa,
        fecha_operativa=fecha_operativa,
        ruta_id=ruta_id,
    )

    if context["finalizadas_por_inactividad"]:
        messages.info(
            request,
            f"{context['finalizadas_por_inactividad']} ruta(s) finalizada(s) por inactividad.",
        )

    return render(
        request,
        "flota_app/despachador/panel_despachador_ruta.html",
        context,
    )


@login_required
@empresa_required
def buscar_unidad_panel(request):
    if request.method != "POST":
        return redirect_panel_despachador(request)

    codigo_raw = request.POST.get("codigo", "").strip()
    hora_str = request.POST.get("hora_fija", "").strip()
    fecha_str = request.POST.get("fecha_operativa", "").strip()
    if not codigo_raw:
        messages.error(request, "Ingrese un codigo de unidad.")
        return redirect_panel_despachador(request)

    codigo = codigo_raw.zfill(2) if codigo_raw.isdigit() and len(codigo_raw) == 1 else codigo_raw
    try:
        fecha_operativa = _parse_fecha_panel(fecha_str)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect_panel_despachador(request)
    empresa = request.empresa

    vehiculo = Vehiculo.objects.for_empresa(empresa).filter(
        codigo=codigo,
        activo=True,
    ).first()

    if not vehiculo:
        messages.error(request, f"No existe unidad activa con codigo {codigo_raw}.")
        return redirect_panel_despachador(request)

    salida_existente = RegistroSalida.objects.for_empresa(empresa).filter(
        vehiculo=vehiculo,
        fecha=fecha_operativa,
        activo=True,
    ).select_related("ruta").first()

    if salida_existente:
        if not hora_str:
            messages.info(request, f"La unidad {vehiculo.codigo} ya esta registrada para la fecha operativa.")
            return redirect_panel_despachador(request, ruta_id=salida_existente.ruta_id)

        try:
            hora_fija_dt = _parse_hora_panel(hora_str, fecha_base=fecha_operativa)
            _programar_salida(salida_existente, empresa, hora_fija_dt, fecha_operativa=fecha_operativa)
        except (ValueError, ValidationError) as error:
            mensaje = error.messages[0] if isinstance(error, ValidationError) else str(error)
            messages.error(request, mensaje)
            return redirect_panel_despachador(request, ruta_id=salida_existente.ruta_id)

        messages.success(
            request,
            f"Unidad {vehiculo.codigo} actualizada con salida {hora_str}.",
        )
        return redirect_panel_despachador(request, ruta_id=salida_existente.ruta_id)

    ruta_id = request.POST.get("ruta_id", "").strip()
    rutas = Ruta.objects.for_empresa(empresa).order_by("nombre")
    ruta = _resolver_ruta_panel(empresa, ruta_id)

    if not ruta:
        if rutas.exists():
            messages.error(request, "Seleccione la ruta antes de agregar la unidad.")
        else:
            messages.error(request, "No existe ninguna ruta registrada en el sistema.")
        return redirect_panel_despachador(request, ruta_id=ruta_id)

    try:
        salida = RegistroSalida(
            vehiculo=vehiculo,
            ruta=ruta,
            fecha=fecha_operativa,
            hora_llegada=timezone.now(),
            activo=True,
            en_cola=False,
            bloqueado=False,
        )
        salida.full_clean()
        salida.save()
    except ValidationError as error:
        messages.error(request, f"No se pudo crear la salida: {error.messages[0]}")
        return redirect_panel_despachador(request, ruta_id=ruta.id)

    if hora_str:
        try:
            hora_fija_dt = _parse_hora_panel(hora_str, fecha_base=fecha_operativa)
            _programar_salida(salida, empresa, hora_fija_dt, fecha_operativa=fecha_operativa)
            messages.success(
                request,
                f"Unidad {vehiculo.codigo} agregada y programada para las {hora_str}.",
            )
            return redirect_panel_despachador(request, ruta_id=ruta.id)
        except (ValueError, ValidationError) as error:
            mensaje = error.messages[0] if isinstance(error, ValidationError) else str(error)
            messages.warning(
                request,
                f"Unidad {vehiculo.codigo} agregada, pero la hora no se programo: {mensaje}",
            )
            return redirect_panel_despachador(request, ruta_id=ruta.id)

    messages.success(request, f"Unidad {vehiculo.codigo} agregada correctamente al panel.")
    return redirect_panel_despachador(request, ruta_id=ruta.id)


@login_required
@empresa_required
def despachador_mapa(request):
    empresa = request.empresa
    rutas = Ruta.objects.for_empresa(empresa).order_by("nombre")
    rutas_geometria = []

    for ruta in rutas:
        geometria = ruta.geometria if isinstance(ruta.geometria, list) else []
        coords_validas = []
        for punto in geometria:
            if (
                isinstance(punto, (list, tuple))
                and len(punto) == 2
            ):
                try:
                    coords_validas.append([float(punto[0]), float(punto[1])])
                except (TypeError, ValueError):
                    continue

        rutas_geometria.append(
            {
                "id": ruta.id,
                "nombre": ruta.nombre,
                "geometria": coords_validas,
            }
        )

    return render(
        request,
        "flota_app/despachador/mapa.html",
        {
            "MAPBOX_TOKEN": settings.MAPBOX_TOKEN,
            "rutas_geometria_json": json.dumps(rutas_geometria),
        },
    )


@login_required
@empresa_required
def recorrido_vehiculo(request):
    empresa = request.empresa
    vehiculos = Vehiculo.objects.for_empresa(empresa).order_by("codigo")
    vehiculo_preseleccionado = request.GET.get("vehiculo", "").strip()
    fecha_preseleccionada = request.GET.get("fecha", "").strip()

    if vehiculo_preseleccionado and not vehiculos.filter(
        id=vehiculo_preseleccionado
    ).exists():
        vehiculo_preseleccionado = ""

    return render(
        request,
        "flota_app/despachador/recorrido.html",
        {
            "vehiculos": vehiculos,
            "vehiculo_preseleccionado": vehiculo_preseleccionado,
            "fecha_preseleccionada": fecha_preseleccionada,
        },
    )


@login_required
@empresa_required
def ver_qr_unidad(request, vehiculo_id):
    empresa = request.empresa
    vehiculo = get_object_or_404(
        Vehiculo,
        id=vehiculo_id,
        empresa=empresa,
    )
    qr_data = signing.dumps(
        {
            "vehiculo_id": vehiculo.id,
            "empresa_id": empresa.id,
        },
        salt="qr-unidad",
    )
    qr = qrcode.make(qr_data)
    buffer = BytesIO()
    qr.save(buffer, format="PNG")
    buffer.seek(0)
    return HttpResponse(buffer.getvalue(), content_type="image/png")


@login_required
@empresa_required
@require_POST
def poner_en_cola(request, salida_id):
    empresa = request.empresa

    with transaction.atomic():
        salida = get_object_or_404(
            RegistroSalida.objects.select_for_update(),
            id=salida_id,
            vehiculo__empresa=empresa,
        )

        hoy = timezone.localdate()

        if salida.fecha != hoy:
            salida.activo = False
            salida.en_cola = False
            salida.save(update_fields=["activo", "en_cola"])

            salida = RegistroSalida.objects.create(
                vehiculo=salida.vehiculo,
                ruta=salida.ruta,
                fecha=hoy,
                hora_llegada=timezone.now(),
                activo=True,
                en_cola=False,
                bloqueado=False,
            )

        if not salida.hora_salida:
            messages.error(request, "Primero debe fijar la hora de salida antes de poner en cola.")
            return redirect_panel_despachador(request, ruta_id=salida.ruta_id)

        if salida.en_cola:
            messages.info(request, "La unidad ya esta en la cola.")
            return redirect_panel_despachador(request, ruta_id=salida.ruta_id)

        cola = (
            RegistroSalida.objects.select_for_update()
            .filter(
                ruta=salida.ruta,
                fecha=hoy,
                en_cola=True,
                activo=True,
            )
            .order_by("orden_cola")
        )

        ultimo = cola.last()
        salida.en_cola = True
        salida.orden_cola = (ultimo.orden_cola + 1) if ultimo else 1
        salida.save(update_fields=["en_cola", "orden_cola"])

        if not salida.bloqueado:
            recalcular_cola(empresa=empresa)

    messages.success(request, "Unidad puesta en cola correctamente.")
    return redirect_panel_despachador(request, ruta_id=salida.ruta_id)


@login_required
@empresa_required
@require_POST
def quitar_de_cola(request, salida_id):
    empresa = request.empresa
    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa,
    )

    if not salida.en_cola:
        messages.info(request, "La unidad no esta en la cola.")
        return redirect_panel_despachador(request, ruta_id=salida.ruta_id)

    salida.en_cola = False
    salida.orden_cola = None
    salida.activo = False
    salida.save(update_fields=["en_cola", "orden_cola", "activo"])

    recalcular_cola(empresa=empresa)
    messages.success(request, "Unidad quitada de la cola y salida cancelada.")
    return redirect_panel_despachador(request, ruta_id=salida.ruta_id)


@login_required
@empresa_required
@require_POST
def cambiar_intervalo_global(request):
    empresa = request.empresa
    ConfiguracionDespacho.objects.filter(empresa=empresa).update(activa=False)

    accion = request.POST.get("accion")
    valor = request.POST.get("intervalo")

    if accion == "manual" and valor:
        ConfiguracionDespacho.objects.create(
            intervalo_fijo=int(valor),
            activa=True,
            empresa=empresa,
        )
        messages.success(request, f"Intervalo fijo establecido en {valor} minutos.")
    else:
        ConfiguracionDespacho.objects.create(
            intervalo_fijo=None,
            activa=True,
            empresa=empresa,
        )
        messages.success(request, "Intervalo automatico activado.")

    recalcular_cola(empresa=empresa)
    return redirect_panel_despachador(request)


@login_required
@empresa_required
@require_POST
def asignar_hora_fija(request, salida_id):
    empresa = request.empresa
    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa,
    )

    hora_str = request.POST.get("hora_fija")
    fecha_str = request.POST.get("current_fecha", "").strip()
    if not hora_str:
        messages.error(request, "Hora invalida.")
        return redirect_panel_despachador(request, ruta_id=salida.ruta_id)

    try:
        fecha_operativa = _parse_fecha_panel(fecha_str) if fecha_str else salida.fecha
        hora_fija_dt = _parse_hora_panel(hora_str, fecha_base=fecha_operativa)
        _programar_salida(salida, empresa, hora_fija_dt, fecha_operativa=fecha_operativa)
    except (ValueError, ValidationError) as error:
        mensaje = error.messages[0] if isinstance(error, ValidationError) else str(error)
        messages.error(request, mensaje)
        return redirect_panel_despachador(request, ruta_id=salida.ruta_id)

    messages.success(request, f"Hora de salida programada correctamente: {hora_str}")
    return redirect_panel_despachador(request, ruta_id=salida.ruta_id)


@login_required
@empresa_required
@require_POST
def desbloquear_hora(request, salida_id):
    empresa = request.empresa
    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa,
    )

    marco_sali = MarcacionPunto.objects.filter(
        registro_salida=salida,
        punto__codigo="SALI",
        hora_marcada__isnull=False,
    ).exists()

    if marco_sali:
        salida.activo = False
        salida.en_cola = False
        salida.orden_cola = None
        salida.save(update_fields=["activo", "en_cola", "orden_cola"])
        recalcular_cola(empresa=empresa)
        messages.success(request, "Ruta activa finalizada por despachador.")
        return redirect_panel_despachador(request, ruta_id=salida.ruta_id)

    salida.hora_salida = None
    salida.bloqueado = False
    salida.save(update_fields=["hora_salida", "bloqueado"])
    messages.success(request, "Salida cancelada. Unidad nuevamente SIN HORA.")
    return redirect_panel_despachador(request, ruta_id=salida.ruta_id)


@login_required
@empresa_required
def detalle_salida(request, salida_id):
    empresa = request.empresa
    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa,
    )
    context = _calcular_detalle_salida(salida)

    return render(
        request,
        "flota_app/detalle_salida.html",
        {
            "salida": salida,
            "detalle": context["detalle"],
            "resumen": context["resumen"],
            "vehiculo_id": salida.vehiculo.id,
            "fecha": salida.fecha,
        },
    )


@login_required
@empresa_required
def historial_vehiculo(request, vehiculo_id):
    empresa = request.empresa
    vehiculo = get_object_or_404(Vehiculo, id=vehiculo_id, empresa=empresa)

    salidas = RegistroSalida.objects.filter(vehiculo=vehiculo).order_by("-fecha", "-hora_salida")
    total = salidas.count()
    a_tiempo = salidas.filter(hora_real_salida__isnull=False).count()
    tarde = total - a_tiempo
    porcentaje = round((a_tiempo / total) * 100, 2) if total > 0 else 0
    fechas = list(salidas.values_list("fecha", flat=True))
    porcentajes = [100 if i % 2 == 0 else 80 for i in range(len(fechas))]

    return render(
        request,
        "flota_app/despachador/historial_vehiculo.html",
        {
            "vehiculo": vehiculo,
            "salidas": salidas,
            "total": total,
            "a_tiempo": a_tiempo,
            "tarde": tarde,
            "porcentaje": porcentaje,
            "fechas": fechas,
            "porcentajes": porcentajes,
            "MAPBOX_TOKEN": settings.MAPBOX_TOKEN,
        },
    )


@login_required
@empresa_required
def control_ruta(request, salida_id):
    empresa = request.empresa
    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa,
    )

    context = _construir_control_ruta_contexto(salida)

    return render(
        request,
        "flota_app/despachador/control_ruta.html",
        context,
    )


@login_required
@empresa_required
@require_POST
def marcar_paso(request, salida_id, punto_id):
    empresa = request.empresa
    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa,
    )
    punto = get_object_or_404(
        PuntoControl,
        id=punto_id,
        ruta__empresa=empresa,
        requiere_marcacion=True,
    )

    marcacion, _ = MarcacionPunto.objects.get_or_create(
        registro_salida=salida,
        punto=punto,
    )
    marcacion.marcar()

    messages.success(request, f"Punto {punto.nombre} marcado ({marcacion.estado}).")
    return redirect("control_ruta", salida_id=salida.id)


@login_required
@empresa_required
@require_POST
def marcar_siguiente_punto(request, salida_id):
    empresa = request.empresa
    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa,
    )

    marcacion = salida.siguiente_marcacion()
    if not marcacion:
        messages.info(request, "No hay mas puntos pendientes.")
        return redirect("control_ruta", salida_id=salida.id)

    marcacion.marcar()
    punto = marcacion.punto

    ultimo = (
        PuntoControl.objects.filter(
            ruta=salida.ruta,
            activo=True,
            requiere_marcacion=True,
        )
        .order_by("-orden")
        .first()
    )

    if ultimo and punto.id == ultimo.id:
        salida.activo = False
        salida.en_cola = False
        salida.save(update_fields=["activo", "en_cola"])
        messages.success(request, "Ultimo punto marcado. Ruta finalizada.")
        return redirect("detalle_salida", salida_id=salida.id)

    messages.success(request, f"Punto {punto.nombre} marcado ({marcacion.estado}).")
    return redirect("control_ruta", salida_id=salida.id)


@login_required
@empresa_required
def marcar_siguiente_punto_auto(request, salida_id):
    empresa = request.empresa
    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa,
    )

    marcacion = salida.siguiente_marcacion()
    if not marcacion:
        messages.info(request, "No hay mas puntos por marcar.")
        return redirect("detalle_salida", salida_id=salida.id)

    return marcar_paso(
        request,
        salida_id=salida.id,
        punto_id=marcacion.punto.id,
    )


@login_required
@empresa_required
def auditoria_horas(request):
    empresa = request.empresa
    salidas = RegistroSalida.objects.for_empresa(empresa).order_by("-fecha", "-hora_salida")
    return render(
        request,
        "flota_app/despachador/auditoria_horas.html",
        {"salidas": salidas},
    )


@login_required
@empresa_required
def historial_salidas(request):
    empresa = request.empresa
    desde = request.GET.get("desde", "").strip()
    hasta = request.GET.get("hasta", "").strip()
    ruta_id = request.GET.get("ruta", "").strip()
    context = _construir_historial_salidas_contexto(
        empresa=empresa,
        desde=desde,
        hasta=hasta,
        ruta_id=ruta_id,
    )
    for error in context["errores"]:
        messages.error(request, error)

    return render(
        request,
        "flota_app/despachador/historial_salidas.html",
        context,
    )


@login_required
@empresa_required
def reporte_control(request):
    empresa = request.empresa
    salidas = RegistroSalida.objects.for_empresa(empresa).order_by("-fecha", "-hora_salida")
    return render(
        request,
        "flota_app/despachador/reporte_control.html",
        {"salidas": salidas},
    )


@login_required
def exportar_excel(request):
    messages.info(request, "Exportacion a Excel aun no implementada.")
    return redirect_panel_despachador(request)


@login_required
@empresa_required
def panel_frecuencia(request):
    empresa = request.empresa
    rutas = Ruta.objects.for_empresa(empresa).order_by("nombre")
    ruta_id = request.GET.get("ruta", "").strip()
    ruta = None

    if ruta_id:
        ruta = rutas.filter(id=ruta_id).first()
    elif rutas.count() == 1:
        ruta = rutas.first()

    puntos = PuntoControl.objects.for_empresa(empresa).filter(
        activo=True,
        requiere_marcacion=True,
    )
    if ruta:
        puntos = puntos.filter(ruta=ruta)
    else:
        puntos = puntos.none()

    puntos = puntos.order_by("orden")
    return render(
        request,
        "flota_app/despachador/frecuencia_ruta.html",
        {
            "puntos": puntos,
            "rutas": rutas,
            "ruta_actual_id": str(ruta.id) if ruta else "",
        },
    )


@login_required
@empresa_required
def debug_gps(request):
    empresa = request.empresa
    data = []

    for ubicacion in UbicacionVehiculo.objects.for_empresa(empresa).select_related("vehiculo"):
        data.append(
            {
                "vehiculo": ubicacion.vehiculo.codigo,
                "lat": ubicacion.latitud,
                "lng": ubicacion.longitud,
                "updated_at": ubicacion.updated_at,
            }
        )

    return JsonResponse(data, safe=False)
