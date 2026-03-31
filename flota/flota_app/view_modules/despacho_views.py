from datetime import datetime, timedelta
from io import BytesIO
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Case, IntegerField, Max, Q, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
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


@login_required
@user_passes_test(es_despachador)
@empresa_required
def panel_despachador(request):
    hoy = timezone.localdate()
    empresa = request.empresa
    rutas = list(Ruta.objects.for_empresa(empresa).order_by("nombre"))

    salidas = list(
        RegistroSalida.objects.for_empresa(empresa)
        .select_related("vehiculo", "ruta")
        .filter(
            ruta__isnull=False,
            activo=True,
            fecha=hoy,
        )
        .order_by(
            Case(
                When(hora_salida__isnull=False, then=0),
                When(hora_salida__isnull=True, then=1),
                output_field=IntegerField(),
            ),
            models.F("hora_salida").asc(nulls_last=True),
            "hora_llegada",
        )
    )

    reporte_vehiculo_id = None
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

    return render(
        request,
        "flota_app/despachador/panel_despachador.html",
        {
            "salidas": salidas,
            "reporte_vehiculo_id": reporte_vehiculo_id,
            "rutas": rutas,
        },
    )


@login_required
@empresa_required
def buscar_unidad_panel(request):
    if request.method != "POST":
        return redirect("panel_despachador")

    codigo_raw = request.POST.get("codigo", "").strip()
    if not codigo_raw:
        messages.error(request, "Ingrese un codigo de unidad.")
        return redirect("panel_despachador")

    codigo = codigo_raw.zfill(2) if codigo_raw.isdigit() and len(codigo_raw) == 1 else codigo_raw
    hoy = timezone.localdate()
    empresa = request.empresa

    vehiculo = Vehiculo.objects.for_empresa(empresa).filter(
        codigo=codigo,
        activo=True,
    ).first()

    if not vehiculo:
        messages.error(request, f"No existe unidad activa con codigo {codigo_raw}.")
        return redirect("panel_despachador")

    if RegistroSalida.objects.for_empresa(empresa).filter(
        vehiculo=vehiculo,
        fecha=hoy,
        activo=True,
    ).exists():
        messages.info(request, f"La unidad {vehiculo.codigo} ya esta registrada hoy.")
        return redirect("panel_despachador")

    ruta_id = request.POST.get("ruta_id", "").strip()
    rutas = Ruta.objects.for_empresa(empresa).order_by("nombre")

    if ruta_id:
        ruta = rutas.filter(id=ruta_id).first()
    elif rutas.count() == 1:
        ruta = rutas.first()
    else:
        ruta = None

    if not ruta:
        if rutas.exists():
            messages.error(request, "Seleccione la ruta antes de agregar la unidad.")
        else:
            messages.error(request, "No existe ninguna ruta registrada en el sistema.")
        return redirect("panel_despachador")

    try:
        salida = RegistroSalida(
            vehiculo=vehiculo,
            ruta=ruta,
            fecha=hoy,
            hora_llegada=timezone.now(),
            activo=True,
            en_cola=False,
            bloqueado=False,
        )
        salida.full_clean()
        salida.save()
    except ValidationError as error:
        messages.error(request, f"No se pudo crear la salida: {error.messages[0]}")
        return redirect("panel_despachador")

    messages.success(request, f"Unidad {vehiculo.codigo} agregada correctamente al panel.")
    return redirect("panel_despachador")


@login_required
@empresa_required
def despachador_mapa(request):
    return render(
        request,
        "flota_app/despachador/mapa.html",
        {"MAPBOX_TOKEN": settings.MAPBOX_TOKEN},
    )


@login_required
@empresa_required
def recorrido_vehiculo(request):
    empresa = request.empresa
    vehiculos = Vehiculo.objects.for_empresa(empresa).order_by("codigo")
    return render(
        request,
        "flota_app/despachador/recorrido.html",
        {"vehiculos": vehiculos},
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
    qr_data = json.dumps({"vehiculo_id": vehiculo.id})
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
            return redirect("panel_despachador")

        if salida.en_cola:
            messages.info(request, "La unidad ya esta en la cola.")
            return redirect("panel_despachador")

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
    return redirect("panel_despachador")


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
        return redirect("panel_despachador")

    salida.en_cola = False
    salida.orden_cola = None
    salida.activo = False
    salida.save(update_fields=["en_cola", "orden_cola", "activo"])

    recalcular_cola(empresa=empresa)
    messages.success(request, "Unidad quitada de la cola y salida cancelada.")
    return redirect("panel_despachador")


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
    return redirect("panel_despachador")


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
    if not hora_str:
        messages.error(request, "Hora invalida.")
        return redirect("panel_despachador")

    hoy = timezone.localdate()

    try:
        hora_time = datetime.strptime(hora_str, "%H:%M").time()
    except ValueError:
        messages.error(request, "Formato de hora invalido.")
        return redirect("panel_despachador")

    hora_fija_dt = timezone.make_aware(
        datetime.combine(hoy, hora_time),
        timezone.get_current_timezone(),
    )

    if salida.hora_real_salida:
        messages.error(request, "No se puede reprogramar la hora: la unidad ya inicio la ruta.")
        return redirect("panel_despachador")

    salida.fecha = hoy
    salida.hora_fija = hora_fija_dt
    salida.hora_salida = hora_fija_dt
    salida.bloqueado = True
    salida.save(update_fields=["fecha", "hora_fija", "hora_salida", "bloqueado"])

    puntos = PuntoControl.objects.for_empresa(empresa).filter(ruta=salida.ruta).order_by("orden")
    for punto in puntos:
        MarcacionPunto.objects.get_or_create(registro_salida=salida, punto=punto)

    for marcacion in salida.marcaciones.all():
        marcacion.hora_programada = marcacion.calcular_hora_programada()
        marcacion.save(update_fields=["hora_programada"])

    messages.success(request, f"Hora de salida programada correctamente: {hora_str}")
    return redirect("panel_despachador")


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
        messages.error(request, "No se puede cancelar la salida porque la unidad ya inicio la ruta.")
        return redirect("panel_despachador")

    salida.hora_salida = None
    salida.bloqueado = False
    salida.save(update_fields=["hora_salida", "bloqueado"])
    messages.success(request, "Salida cancelada. Unidad nuevamente SIN HORA.")
    return redirect("panel_despachador")


@login_required
@empresa_required
def detalle_salida(request, salida_id):
    empresa = request.empresa
    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa,
    )

    marcaciones_qs = (
        MarcacionPunto.objects.filter(registro_salida=salida)
        .select_related("punto")
        .order_by("punto__orden")
    )

    detalle = []
    for marcacion in marcaciones_qs:
        punto = marcacion.punto

        if salida.hora_salida:
            hora_programada = salida.hora_salida + timedelta(minutes=punto.offset_minutos)
        else:
            hora_programada = None

        estado = "pendiente"
        diferencia = None

        if marcacion.hora_marcada and hora_programada:
            diferencia = int((marcacion.hora_marcada - hora_programada).total_seconds() / 60)
            if diferencia < 0:
                estado = "adelantado"
            elif diferencia == 0:
                estado = "a_tiempo"
            else:
                estado = "tarde"

        detalle.append(
            {
                "punto": punto,
                "hora_programada": hora_programada,
                "hora_marcada": marcacion.hora_marcada,
                "diferencia": diferencia,
                "estado": estado,
            }
        )

    return render(
        request,
        "flota_app/detalle_salida.html",
        {
            "salida": salida,
            "detalle": detalle,
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

    puntos = PuntoControl.objects.filter(ruta=salida.ruta, activo=True).order_by("orden")
    controles = []

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

        controles.append(
            {
                "punto": punto,
                "marcacion": marcacion,
                "hora_programada_calculada": hora_programada_calculada,
            }
        )

    return render(
        request,
        "flota_app/despachador/control_ruta.html",
        {"salida": salida, "controles": controles},
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
        PuntoControl.objects.filter(ruta=salida.ruta, activo=True)
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
    salidas = (
        RegistroSalida.objects.for_empresa(empresa)
        .select_related("vehiculo", "ruta")
        .order_by("-fecha", "-hora_salida")
    )
    rutas = Ruta.objects.for_empresa(empresa).order_by("nombre")
    desde = request.GET.get("desde", "").strip()
    hasta = request.GET.get("hasta", "").strip()
    ruta_id = request.GET.get("ruta", "").strip()

    if desde:
        try:
            salidas = salidas.filter(
                fecha__gte=datetime.strptime(desde, "%Y-%m-%d").date()
            )
        except ValueError:
            messages.error(request, "La fecha 'desde' no es valida.")

    if hasta:
        try:
            salidas = salidas.filter(
                fecha__lte=datetime.strptime(hasta, "%Y-%m-%d").date()
            )
        except ValueError:
            messages.error(request, "La fecha 'hasta' no es valida.")

    if ruta_id:
        salidas = salidas.filter(ruta_id=ruta_id)

    historial = []

    for salida in salidas:
        hora_programada = salida.hora_salida
        primera_marcacion = (
            MarcacionPunto.objects.filter(
                registro_salida=salida,
                hora_marcada__isnull=False,
            )
            .order_by("hora_marcada")
            .first()
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

        historial.append(
            {
                "salida": salida,
                "programada": hora_programada,
                "marcada": hora_marcada,
                "falta": diferencia,
                "estado": estado,
            }
        )

    return render(
        request,
        "flota_app/despachador/historial_salidas.html",
        {
            "historial": historial,
            "rutas": rutas,
        },
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
    return redirect("panel_despachador")


@login_required
@empresa_required
def panel_frecuencia(request):
    empresa = request.empresa
    puntos = PuntoControl.objects.for_empresa(empresa).filter(activo=True).order_by("orden")
    return render(
        request,
        "flota_app/despachador/frecuencia_ruta.html",
        {"puntos": puntos},
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
