# =================================================
# 📦 IMPORTS
# =================================================
from datetime import datetime, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST, require_GET
from django.db.models import Case, When, IntegerField, Max, Q
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.conf import settings
from django.db import models, transaction

from django.contrib.auth.views import LoginView
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie

from ..models import (
    RegistroSalida,
    Vehiculo,
    Ruta,
    PuntoControl,
    MarcacionPunto,
    ConfiguracionDespacho,
)

from ..services import recalcular_cola
from ..decorators import empresa_required


# =================================================
# 🧭 PANEL DESPACHADOR
# =================================================
def es_despachador(user):
    return user.groups.filter(name="despachador").exists() or user.is_superuser


@login_required(login_url="/sistema/login/")
@empresa_required
def panel_despachador(request):
    if not es_despachador(request.user):
        messages.error(request, "No tiene permisos para acceder al panel despachador.")
        return redirect("/admin/")  # o a otra página de tu elección

    hoy = timezone.localdate()
    empresa = request.empresa

    salidas = (
        RegistroSalida.objects
        .for_empresa(empresa)
        .select_related("vehiculo", "ruta")
        .filter(
            ruta__isnull=False,
            activo=True,
            fecha=hoy
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

    return render(
        request,
        "flota_app/despachador/panel_despachador.html",
        {"salidas": salidas}
    )


# =================================================
# 🔍 BUSCAR UNIDAD
# =================================================
@login_required
@empresa_required
def buscar_unidad_panel(request):
    if request.method != "POST":
        return redirect("panel_despachador")

    # -------------------------------------------------
    # 📥 LEER Y NORMALIZAR CÓDIGO
    # -------------------------------------------------
    codigo_raw = request.POST.get("codigo", "").strip()

    if not codigo_raw:
        messages.error(request, "Ingrese un código de unidad.")
        return redirect("panel_despachador")

    # Normalizar: "1" -> "01"
    if codigo_raw.isdigit() and len(codigo_raw) == 1:
        codigo = codigo_raw.zfill(2)
    else:
        codigo = codigo_raw

    hoy = timezone.localdate()

    # -------------------------------------------------
    # 🏢 EMPRESA (DESDE MIDDLEWARE ✅)
    # -------------------------------------------------
    empresa = request.empresa

    # -------------------------------------------------
    # 🚍 BUSCAR VEHÍCULO ACTIVO
    # -------------------------------------------------
    vehiculo = Vehiculo.objects.for_empresa(empresa).filter(
        codigo=codigo,
        activo=True,
    ).first()

    if not vehiculo:
        messages.error(
            request,
            f"No existe unidad activa con código {codigo_raw}."
        )
        return redirect("panel_despachador")

    # -------------------------------------------------
    # 🔒 EVITAR DUPLICADO DEL DÍA
    # -------------------------------------------------
    if RegistroSalida.objects.for_empresa(empresa).filter(
        vehiculo=vehiculo,
        fecha=hoy,
        activo=True
    ).exists():
        messages.info(
            request,
            f"La unidad {vehiculo.codigo} ya está registrada hoy."
        )
        return redirect("panel_despachador")

    # -------------------------------------------------
    # 🧭 RUTA AUTOMÁTICA (SEGURA)
    # 👉 TOMA LA PRIMERA RUTA ACTIVA
    # -------------------------------------------------
    ruta = Ruta.objects.for_empresa(empresa).first()

    if not ruta:
        messages.error(
            request,
            "No existe ninguna ruta registrada en el sistema."
        )
        return redirect("panel_despachador")

    # -------------------------------------------------
    # 🟢 CREAR SALIDA
    # -------------------------------------------------
    try:
        salida = RegistroSalida(
            vehiculo=vehiculo,
            ruta=ruta,
            fecha=hoy,
            hora_llegada=timezone.now(),
            activo=True,
            en_cola=False,
            bloqueado=False
        )
        salida.full_clean()
        salida.save()

    except ValidationError as e:
        messages.error(
            request,
            f"No se pudo crear la salida: {e.messages[0]}"
        )
        return redirect("panel_despachador")

    messages.success(
        request,
        f"Unidad {vehiculo.codigo} agregada correctamente al panel."
    )

    return redirect("panel_despachador")

# =================================================
# 🚦 COLA
# =================================================
@login_required
@empresa_required
@require_POST
def poner_en_cola(request, salida_id):

    empresa = request.empresa

    with transaction.atomic():  # 🔥 BLOQUEO TOTAL

        salida = get_object_or_404(
            RegistroSalida.objects.select_for_update(),  # 🔥 LOCK
            id=salida_id,
            vehiculo__empresa=empresa
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
                bloqueado=False
            )

        if not salida.hora_salida:
            messages.error(
                request,
                "❌ Primero debe fijar la hora de salida antes de poner en cola."
            )
            return redirect("panel_despachador")

        if salida.en_cola:
            messages.info(request, "La unidad ya está en la cola.")
            return redirect("panel_despachador")

        # 🔥 BLOQUEAMOS TODA LA COLA
        cola = (
            RegistroSalida.objects
            .select_for_update()
            .filter(
                ruta=salida.ruta,
                fecha=hoy,
                en_cola=True,
                activo=True
            )
            .order_by("orden_cola")
        )

        ultimo = cola.last()

        salida.en_cola = True
        salida.orden_cola = (ultimo.orden_cola + 1) if ultimo else 1

        salida.save(update_fields=["en_cola", "orden_cola"])

        if not salida.bloqueado:
            recalcular_cola(empresa=empresa)

    messages.success(request, "✅ Unidad puesta en cola correctamente.")
    return redirect("panel_despachador")
    

@login_required
@empresa_required
@require_POST
def quitar_de_cola(request, salida_id):

    empresa = request.empresa

    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa
    )

    if not salida.en_cola:
        messages.info(request, "La unidad no está en la cola.")
        return redirect("panel_despachador")

    salida.en_cola = False
    salida.orden_cola = None
    salida.activo = False

    salida.save(update_fields=[
        "en_cola",
        "orden_cola",
        "activo"
    ])

    recalcular_cola(empresa=empresa)

    messages.success(
        request,
        "Unidad quitada de la cola y salida cancelada."
    )
    return redirect("panel_despachador")


# =================================================
# ⏱️ HORAS
# =================================================
@login_required
@empresa_required
@require_POST
def asignar_hora_fija(request, salida_id):

    empresa = request.empresa

    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa
    )

    hora_str = request.POST.get("hora_fija")
    if not hora_str:
        messages.error(request, "Hora inválida.")
        return redirect("panel_despachador")

    hoy = timezone.localdate()

    try:
        hora_time = datetime.strptime(hora_str, "%H:%M").time()
    except ValueError:
        messages.error(request, "Formato de hora inválido.")
        return redirect("panel_despachador")

    hora_fija_dt = timezone.make_aware(
        datetime.combine(hoy, hora_time),
        timezone.get_current_timezone()
    )

    if salida.hora_real_salida:
        messages.error(
            request,
            "No se puede reprogramar la hora: la unidad ya inició la ruta."
        )
        return redirect("panel_despachador")

    salida.fecha = hoy
    salida.hora_fija = hora_fija_dt
    salida.hora_salida = hora_fija_dt
    salida.bloqueado = True

    salida.save(update_fields=[
        "fecha",
        "hora_fija",
        "hora_salida",
        "bloqueado"
    ])

    puntos = PuntoControl.objects.for_empresa(empresa).filter(
        ruta=salida.ruta
    ).order_by("orden")

    for punto in puntos:
        MarcacionPunto.objects.get_or_create(
            registro_salida=salida,
            punto=punto
        )

    for m in salida.marcaciones.all():
        m.hora_programada = m.calcular_hora_programada()
        m.save(update_fields=["hora_programada"])

    messages.success(
        request,
        f"Hora de salida programada correctamente: {hora_str}"
    )

    return redirect("panel_despachador")


@login_required
@empresa_required
@require_POST
def desbloquear_hora(request, salida_id):

    empresa = request.empresa

    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa
    )

    marco_sali = MarcacionPunto.objects.filter(
        registro_salida=salida,
        punto__codigo="SALI",
        hora_marcada__isnull=False
    ).exists()

    if marco_sali:
        messages.error(
            request,
            "No se puede cancelar la salida porque la unidad ya inició la ruta."
        )
        return redirect("panel_despachador")

    salida.hora_salida = None
    salida.bloqueado = False
    salida.save(update_fields=["hora_salida", "bloqueado"])

    messages.success(
        request,
        "Salida cancelada. Unidad nuevamente SIN HORA."
    )

    return redirect("panel_despachador")


# =================================================
# 📄 DETALLE DE SALIDA
# =================================================
@login_required
@empresa_required
def detalle_salida(request, salida_id):

    empresa = request.empresa

    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa
    )

    marcaciones_qs = (
        MarcacionPunto.objects
        .filter(registro_salida=salida)
        .select_related("punto")
        .order_by("punto__orden")
    )

    detalle = []

    for m in marcaciones_qs:
        punto = m.punto

        if salida.hora_salida:
            hora_programada = (
                salida.hora_salida
                + timedelta(minutes=punto.offset_minutos)
            )
        else:
            hora_programada = None

        estado = "pendiente"
        diferencia = None

        if m.hora_marcada and hora_programada:
            diferencia = int(
                (m.hora_marcada - hora_programada).total_seconds() / 60
            )

            if diferencia < 0:
                estado = "adelantado"
            elif diferencia == 0:
                estado = "a_tiempo"
            else:
                estado = "tarde"

        detalle.append({
            "punto": punto,
            "hora_programada": hora_programada,
            "hora_marcada": m.hora_marcada,
            "diferencia": diferencia,
            "estado": estado,
        })

    return render(
        request,
        "flota_app/detalle_salida.html",
        {
            "salida": salida,
            "detalle": detalle,
            "vehiculo_id": salida.vehiculo.id,
            "fecha": salida.fecha,
        }
    )


@login_required
@empresa_required
def control_ruta(request, salida_id):

    empresa = request.empresa

    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa
    )

    puntos = (
        PuntoControl.objects
        .filter(
            ruta=salida.ruta,
            activo=True
        )
        .order_by("orden")
    )

    controles = []

    for punto in puntos:

        if salida.hora_salida:
            hora_programada_calculada = (
                salida.hora_salida
                + timedelta(minutes=punto.offset_minutos)
            )
        else:
            hora_programada_calculada = None

        marcacion, _ = MarcacionPunto.objects.get_or_create(
            registro_salida=salida,
            punto=punto,
            defaults={
                "hora_programada": hora_programada_calculada
            }
        )

        controles.append({
            "punto": punto,
            "marcacion": marcacion,
            "hora_programada_calculada": hora_programada_calculada,
        })

    return render(
        request,
        "flota_app/despachador/control_ruta.html",
        {
            "salida": salida,
            "controles": controles,
        }
    )


@login_required
@empresa_required
@require_POST
def marcar_paso(request, salida_id, punto_id):

    empresa = request.empresa

    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa
    )

    punto = get_object_or_404(
        PuntoControl,
        id=punto_id,
        ruta__empresa=empresa
    )

    marcacion, _ = MarcacionPunto.objects.get_or_create(
        registro_salida=salida,
        punto=punto
    )

    marcacion.marcar()

    messages.success(
        request,
        f"Punto {punto.nombre} marcado ({marcacion.estado})."
    )

    return redirect("control_ruta", salida_id=salida.id)


@login_required
@empresa_required
@require_POST
def marcar_siguiente_punto(request, salida_id):

    empresa = request.empresa

    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa
    )

    marcacion = salida.siguiente_marcacion()

    if not marcacion:
        messages.info(
            request,
            "No hay más puntos pendientes."
        )
        return redirect(
            "control_ruta",
            salida_id=salida.id
        )

    marcacion.marcar()

    punto = marcacion.punto

    ultimo = (
        PuntoControl.objects
        .filter(
            ruta=salida.ruta,
            activo=True
        )
        .order_by("-orden")
        .first()
    )

    if ultimo and punto.id == ultimo.id:
        salida.activo = False
        salida.en_cola = False
        salida.save(update_fields=[
            "activo",
            "en_cola"
        ])

        messages.success(
            request,
            "Último punto marcado. Ruta finalizada."
        )

        return redirect(
            "detalle_salida",
            salida_id=salida.id
        )

    messages.success(
        request,
        f"Punto {punto.nombre} marcado ({marcacion.estado})."
    )

    return redirect(
        "control_ruta",
        salida_id=salida.id
    )


@login_required
@empresa_required
def marcar_siguiente_punto_auto(request, salida_id):

    empresa = request.empresa

    salida = get_object_or_404(
        RegistroSalida,
        id=salida_id,
        vehiculo__empresa=empresa
    )

    marcacion = salida.siguiente_marcacion()

    if not marcacion:
        messages.info(
            request,
            "No hay más puntos por marcar."
        )
        return redirect("detalle_salida", salida_id=salida.id)

    return marcar_paso(
        request,
        salida_id=salida.id,
        punto_id=marcacion.punto.id
    )


@login_required
@empresa_required
def historial_salidas(request):

    empresa = request.empresa

    salidas = (
        RegistroSalida.objects.for_empresa(empresa)
        .order_by("-fecha", "-hora_salida")
    )

    historial = []

    for salida in salidas:
        hora_programada = salida.hora_salida

        primera_marcacion = (
            MarcacionPunto.objects
            .filter(
                registro_salida=salida,
                hora_marcada__isnull=False
            )
            .order_by("hora_marcada")
            .first()
        )

        hora_marcada = (
            primera_marcacion.hora_marcada
            if primera_marcacion else None
        )

        estado = "pendiente"
        diferencia = None

        if hora_programada and hora_marcada:
            diferencia = int(
                (hora_marcada - hora_programada).total_seconds() / 60
            )

            if diferencia < 0:
                estado = "adelantado"
            elif diferencia == 0:
                estado = "a_tiempo"
            else:
                estado = "tarde"

        historial.append({
            "salida": salida,
            "programada": hora_programada,
            "marcada": hora_marcada,
            "falta": diferencia,
            "estado": estado,
        })

    return render(
        request,
        "flota_app/despachador/historial_salidas.html",
        {"historial": historial}
    )


@login_required
@empresa_required
def auditoria_horas(request):

    empresa = request.empresa
   
    salidas = (
        RegistroSalida.objects.for_empresa(empresa)
        .order_by("-fecha", "-hora_salida")
    )

    return render(
        request,
        "flota_app/despachador/auditoria_horas.html",
        {"salidas": salidas}
    )


@login_required
@empresa_required
def historial_vehiculo(request, vehiculo_id):

    empresa = request.empresa

    vehiculo = get_object_or_404(
        Vehiculo,
        id=vehiculo_id,
        empresa=empresa
    )

    salidas = (
        RegistroSalida.objects
        .filter(vehiculo=vehiculo)
        .order_by("-fecha", "-hora_salida")
    )

    total = salidas.count()

    a_tiempo = salidas.filter(
        hora_real_salida__isnull=False
    ).count()

    tarde = total - a_tiempo

    porcentaje = round((a_tiempo / total) * 100, 2) if total > 0 else 0

    fechas = list(
        salidas.values_list("fecha", flat=True)
    )

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
        }
    )


# =================================================
# 📈 FRECUENCIA
# =================================================
@login_required
@empresa_required
def panel_frecuencia(request):

    empresa = request.empresa

    puntos = PuntoControl.objects.for_empresa(empresa).filter(
        activo=True,
    ).order_by("orden")

    return render(
        request,
        "flota_app/despachador/frecuencia_ruta.html",
        {"puntos": puntos}
    )


@login_required
@empresa_required
@require_GET
def api_panel_frecuencia(request):

    hoy = timezone.localdate()

    empresa = request.empresa

    puntos = list(
        PuntoControl.objects.for_empresa(empresa).filter(
            activo=True,
        ).order_by("orden")
    )

    if not puntos:
        return JsonResponse({"puntos": [], "data": []})

    max_orden = max(p.orden for p in puntos)

    config = ConfiguracionDespacho.objects.filter(
        activa=True,
        empresa=empresa
    ).first()

    intervalo = config.intervalo_fijo if config and config.intervalo_fijo else 6

    salidas_qs = (
        RegistroSalida.objects.for_empresa(empresa)
        .filter(
            activo=True,
            fecha=hoy,
            ruta__isnull=False,
        )
        .annotate(
            ultimo_punto_orden=Max(
                "marcaciones__punto__orden",
                filter=Q(marcaciones__hora_marcada__isnull=False)
            ),
            ultimo_tiempo=Max("marcaciones__hora_marcada")
        )
        .prefetch_related("marcaciones__punto")
    )

    unidades_panel = []

    for salida in salidas_qs:

        if salida.ultimo_punto_orden == max_orden:
            salida.activo = False
            salida.en_cola = False
            salida.save(update_fields=["activo", "en_cola"])
            continue

        marcaciones = {
            m.punto_id: m
            for m in salida.marcaciones.all()
        }

        controles = []

        for punto in puntos:
            m = marcaciones.get(punto.id)
            controles.append(
                m.diferencia_minutos if m and m.hora_marcada else None
            )

        unidades_panel.append({
            "unidad": salida.vehiculo.codigo,
            "salida_id": salida.id,
            "avance": salida.ultimo_punto_orden or 0,
            "ultimo_tiempo": salida.ultimo_tiempo,
            "controles": controles,
            "frecuencia": None,
            "hueco": False,
            "pegado": False
        })

    unidades_panel.sort(key=lambda x: x["avance"], reverse=True)

    for i in range(1, len(unidades_panel)):
        actual = unidades_panel[i]
        anterior = unidades_panel[i - 1]

        if actual["ultimo_tiempo"] and anterior["ultimo_tiempo"]:
            diff = (
                actual["ultimo_tiempo"] - anterior["ultimo_tiempo"]
            ).total_seconds() / 60

            actual["frecuencia"] = int(diff)

            if diff > intervalo * 1.5:
                actual["hueco"] = True

            if diff < intervalo * 0.5:
                actual["pegado"] = True

    if unidades_panel:
        unidades_panel[0]["lider"] = True
        for u in unidades_panel[1:]:
            u["lider"] = False

    return JsonResponse({
        "puntos": [p.codigo for p in puntos],
        "data": unidades_panel
    })

@method_decorator(never_cache, name="dispatch")
@method_decorator(csrf_protect, name="dispatch")
@method_decorator(ensure_csrf_cookie, name="dispatch")
class LoginSistemaView(LoginView):

    template_name = "login.html"
    authentication_form = AuthenticationForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        user = form.get_user()
        login(self.request, user)
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(
            self.request,
            "Usuario o contraseña incorrectos."
        )
        return super().form_invalid(form)

from django.urls import reverse

def get_success_url(self):
    user = self.request.user

    if user.is_superuser:
        return reverse("admin:index")

    if user.groups.filter(name="despachador").exists():
        return reverse("panel_despachador")

    return reverse("login")