# =================================================
# 📦 IMPORTS ÚNICOS Y LIMPIOS (FIX DEFINITIVO)
# =================================================

from datetime import datetime, date, timedelta
import json
from io import BytesIO

from django.shortcuts import (
    render, redirect, get_object_or_404
)
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.cache import never_cache
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Case, When, IntegerField
from django.db import models
from django.db.models import Max, Q
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView
from django.contrib.auth import login
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth.forms import AuthenticationForm
from django.views.decorators.csrf import ensure_csrf_cookie
from .decorators import empresa_required
from django.db import transaction

import qrcode

# ================= MODELS =================
from .models import (
    MensajeGlobal,
    RegistroSalida,
    Ruta,
    Vehiculo,
    ConfiguracionDespacho,
    PuntoControl,
    MarcacionPunto,
    SesionUnidad,
    GPSRegistro,
    UbicacionVehiculo,
    Parada,
)

# ================= SERVICES (🔥 FIX CLAVE) =================
from .services import (
    validar_sesion,
    calcular_estado_sesion,
    iniciar_salida_segura,
    recalcular_cola,   # ✅ FALTABA ESTO
)

# ================= UTILS =================
from .utils import distancia_metros
@login_required
@empresa_required
def reporte_salidas_diarias(request, vehiculo_id):
    """
    FASE 4A + 4C
    Pantalla tipo app: salidas del día por vehículo

    ✔ Minutos calculados POR SALIDA
    ✔ Resumen diario automático
    ✔ Filtro visual (fecha + unidad)
    ✔ FIX DEFINITIVO de timezone
    ✔ FIX DEFINITIVO contra rutas NULL (500)
    """

    # =================================================
    # 📅 FECHA (🔥 FIX DEFINITIVO TIMEZONE)
    # =================================================
    fecha_param = request.GET.get("fecha")

    if fecha_param:
        try:
            fecha = date.fromisoformat(fecha_param)
        except ValueError:
            fecha = timezone.localdate()
    else:
        fecha = timezone.localdate()

    # =================================================
    # 🏢 EMPRESA (DESDE MIDDLEWARE ✅)
    # =================================================
    empresa = request.empresa

    # =================================================
    # 🚌 VEHÍCULO ACTUAL
    # =================================================
    vehiculo = get_object_or_404(
        Vehiculo,
        id=vehiculo_id,
        empresa=empresa
    )

    # =================================================
    # 🚌 LISTA DE VEHÍCULOS (FILTRO SUPERIOR)
    # =================================================
    vehiculos = Vehiculo.objects.for_empresa(empresa)

    # =================================================
    # 🚍 SALIDAS DEL DÍA (ACTIVAS + FINALIZADAS)
    # =================================================
    salidas = list(
        RegistroSalida.objects.for_empresa(empresa).filter(
            vehiculo=vehiculo,
            fecha=fecha
        )
        .order_by("hora_salida", "creado_en")
    )

    resultado = []
    total_salidas = len(salidas)

    # =================================================
    # 🔁 PROCESAR CADA SALIDA
    # =================================================
    for index, salida in enumerate(salidas):

        vuelta = index + 1

        # -------------------------------------------------
        # 🔒 FIX CRÍTICO — RUTA NULL
        # -------------------------------------------------
        if not salida.ruta:
            total_puntos = 0
        else:
            total_puntos = PuntoControl.objects.for_empresa(empresa).filter(
                ruta=salida.ruta,
                activo=True
            ).count()

        puntos_marcados = salida.marcaciones.exclude(
            hora_marcada__isnull=True
        ).count()

        porcentaje = int(
            (puntos_marcados / total_puntos) * 100
        ) if total_puntos > 0 else 0

        # -------------------------------------------------
        # ⏱ RANGO DE TIEMPO DE LA SALIDA
        # -------------------------------------------------
        inicio = salida.hora_salida

        if index + 1 < total_salidas:
            fin = salidas[index + 1].hora_salida
        else:
            fin = salida.hora_real_salida or timezone.now()

        # -------------------------------------------------
        # 🛑 MINUTOS POR PARADAS PROLONGADAS
        # -------------------------------------------------
        minutos = 0

        if inicio and fin:
            paradas = Parada.objects.for_empresa(empresa).filter(
                vehiculo=vehiculo,
                es_prolongada=True,
                inicio__gte=inicio,
                inicio__lt=fin
            )

            for p in paradas:
                minutos += int(p.duracion_segundos / 60)

        # -------------------------------------------------
        # 📦 RESULTADO FINAL POR SALIDA
        # -------------------------------------------------
        resultado.append({
            "hora": salida.hora_salida,
            "ruta": salida.ruta.nombre if salida.ruta else "SIN RUTA",
            "vuelta": vuelta,
            "porcentaje": porcentaje,
            "minutos": minutos,
            "salida_id": salida.id,
        })

    # =================================================
    # 📊 RESUMEN DIARIO
    # =================================================
    total_vueltas = len(resultado)

    promedio_marcacion = (
        int(sum(s["porcentaje"] for s in resultado) / total_vueltas)
        if total_vueltas > 0 else 0
    )

    minutos_totales = sum(s["minutos"] for s in resultado)

    alertas = []

    if total_vueltas > 0 and promedio_marcacion < 90:
        alertas.append("Marcación promedio baja")

    if minutos_totales > 15:
        alertas.append("Exceso de minutos por paradas prolongadas")

    # =================================================
    # 📤 RENDER FINAL
    # =================================================
    return render(
        request,
        "reportes/salidas_diarias.html",
        {
            "vehiculo": vehiculo,
            "vehiculos": vehiculos,
            "fecha": fecha,
            "salidas": resultado,

            "total_vueltas": total_vueltas,
            "promedio_marcacion": promedio_marcacion,
            "minutos_totales": minutos_totales,
            "alertas": alertas,
        }
    )

# =================================================
# 🧭 PANEL DESPACHADOR (REFORMADO Y COHERENTE)
# =================================================
def es_despachador(user):
    return user.groups.filter(name="despachador").exists() or user.is_superuser


@login_required
@user_passes_test(es_despachador)
@empresa_required
def panel_despachador(request):
    """
    PANEL PRINCIPAL DEL DESPACHADOR (REFORMADO)

    REGLAS:
    - El despachador SOLO fija hora
    - No existe poner en cola desde el panel
    - No se recalculan horas
    - El orden es por hora de salida
    - Compatible con producción
    """

    # 🔍 DEBUG (puedes quitarlo después)
    print("DEBUG EMPRESA:", request.empresa)

    # -------------------------------------------------
    # 📅 FECHA OPERATIVA REAL (ZONA LOCAL)
    # -------------------------------------------------
    hoy = timezone.localdate()

    # -------------------------------------------------
    # 🏢 EMPRESA (AHORA DESDE MIDDLEWARE ✅)
    # -------------------------------------------------
    empresa = request.empresa

    # -------------------------------------------------
    # 🔥 SALIDAS ACTIVAS DEL DÍA
    # ORDEN REAL DE DESPACHO:
    #   1️⃣ Primero las que tienen hora
    #   2️⃣ Ordenadas por hora_salida
    #   3️⃣ Luego las que no tienen hora
    # -------------------------------------------------
    salidas = (
    RegistroSalida.objects
    .for_empresa(empresa)   # 🔥 NUEVO
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

    # -------------------------------------------------
    # 📤 RENDER FINAL (SOLO LO NECESARIO)
    # -------------------------------------------------
    return render(
        request,
        "flota_app/despachador/panel_despachador.html",
        {
            "salidas": salidas,
        }
    )

# =================================================
# 🔍 BUSCAR UNIDAD Y CREAR SALIDA DEL DÍA
# SOLUCIÓN DEFINITIVA (SIN es_default / SIN 500)
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
# 🗺️ DESPACHADOR — MAPA TIEMPO REAL (VISTA SEPARADA)
# =================================================
@login_required
@empresa_required
def despachador_mapa(request):
    """
    Vista exclusiva del mapa en tiempo real.
    Se accede desde el botón 'Ver mapa' del panel.
    """
    return render(
        request,
        "flota_app/despachador/mapa.html",
        {
            "MAPBOX_TOKEN": settings.MAPBOX_TOKEN
        }
    )

# =================================================
# 🧭 DESPACHADOR — RECORRIDO HISTÓRICO (VISTA)
# =================================================
@login_required
@empresa_required
def recorrido_vehiculo(request):
    """
    Vista del despachador para visualizar
    el recorrido histórico de un vehículo por fecha.
    (Usa Leaflet + API de recorrido)
    """

    # 🏢 EMPRESA DESDE MIDDLEWARE
    empresa = request.empresa

    vehiculos = Vehiculo.objects.for_empresa(empresa).order_by("codigo")

    return render(
        request,
        "flota_app/despachador/recorrido.html",
        {
            "vehiculos": vehiculos
        }
    )

# =================================================
# 📡 API APP CONDUCTOR — GPS OFICIAL (VERSIÓN EMPRESA)
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

    sesion = validar_sesion(token)
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
# 🛑 DETECTOR DE PARADAS (AUTOMÁTICO + FASE 4)
# =================================================
TIEMPO_MIN_PARADA = 120          # 2 minutos → parada válida
TIEMPO_PARADA_PROLONGADA = 300   # 🔥 5 minutos → FASE 4
VEL_DETENIDO = 1                # km/h
RADIO_METROS = 20               # metros

def procesar_parada(vehiculo, lat, lng, velocidad, timestamp):
    """
    Detecta paradas de vehículo:
    - Evita falsas paradas
    - Cierra paradas reales
    - 🔥 Marca parada prolongada (> X minutos) → FASE 4
    """

    parada = Parada.objects.for_empresa(vehiculo.empresa).filter(
        vehiculo=vehiculo,
        activa=True
    ).order_by("-inicio").first()

    # =================================================
    # 🚍 VEHÍCULO DETENIDO
    # =================================================
    if velocidad <= VEL_DETENIDO:

        # ▶️ Inicia nueva parada
        if not parada:
            Parada.objects.create(
                vehiculo=vehiculo,
                lat=lat,
                lng=lng,
                inicio=timestamp
            )
            return

        # ▶️ Verificar si se desplazó (nuevo punto)
        distancia = distancia_metros(
            parada.lat, parada.lng,
            lat, lng
        )

        if distancia > RADIO_METROS:
            # Se movió: cerrar parada anterior y abrir otra
            parada.cerrar(timestamp)
            Parada.objects.create(
                vehiculo=vehiculo,
                lat=lat,
                lng=lng,
                inicio=timestamp
            )
            return

        # =================================================
        # 🔥 FASE 4 — PARADA PROLONGADA
        # =================================================
        duracion = (timestamp - parada.inicio).total_seconds()

        if (
            not parada.es_prolongada and
            duracion >= TIEMPO_PARADA_PROLONGADA
        ):
            parada.es_prolongada = True
            parada.save(update_fields=["es_prolongada"])

    # =================================================
    # 🚗 VEHÍCULO EN MOVIMIENTO
    # =================================================
    else:
        if parada:
            duracion = (timestamp - parada.inicio).total_seconds()

            # ❌ Falsa parada
            if duracion < TIEMPO_MIN_PARADA:
                parada.delete()
            else:
                parada.cerrar(timestamp)

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

    sesion = validar_sesion(token)
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
# 🗺️ API — MAPA EN TIEMPO REAL (DESPACHADOR) OPTIMIZADO
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

# =================================================
# 📍 API — PUNTOS DE CONTROL (MAPA DESPACHADOR)
# =================================================
@login_required
@empresa_required
@require_GET
def api_puntos_control(request):
    """
    Devuelve los puntos de control activos
    para ser dibujados en el mapa del despachador.
    """

    # 🏢 EMPRESA DESDE MIDDLEWARE
    empresa = request.empresa

    puntos = (
        PuntoControl.objects.for_empresa(empresa)
        .filter(
            activo=True,
        )
        .order_by("orden")
    )

    data = []

    for p in puntos:
        # Seguridad: solo si tiene coordenadas
        if p.latitud is None or p.longitud is None:
            continue

        data.append({
            "id": p.id,
            "codigo": p.codigo,
            "nombre": p.nombre,
            "orden": p.orden,
            "lat": float(p.latitud),
            "lng": float(p.longitud),
            "radio": p.radio_metros,
        })

    return JsonResponse(data, safe=False)

# =================================================
# 🔎 API — BÚSQUEDA RÁPIDA DE VEHÍCULO POR CÓDIGO
# =================================================
@login_required
@empresa_required
@require_GET
def api_buscar_vehiculo_por_codigo(request):
    """
    Devuelve la unidad ACTIVA asociada a un código operativo.
    """

    codigo = request.GET.get("codigo", "").strip()

    if not codigo:
        return JsonResponse(
            {"error": "codigo requerido"},
            status=400
        )

    # 🏢 EMPRESA DESDE MIDDLEWARE
    empresa = request.empresa

    qs = Vehiculo.objects.for_empresa(empresa).filter(
        codigo=codigo,
        activo=True,
    )

    if not qs.exists():
        return JsonResponse(
            {"error": f"No existe unidad activa con código {codigo}"},
            status=404
        )

    if qs.count() > 1:
        return JsonResponse(
            {"error": f"Conflicto: más de una unidad activa con código {codigo}"},
            status=409
        )

    vehiculo = qs.first()

    return JsonResponse({
        "vehiculo_id": vehiculo.id,
        "codigo": vehiculo.codigo,
        "placa": vehiculo.placa,
        "activo": vehiculo.activo,
    })

# =================================================
# 🧭 API — RECORRIDO HISTÓRICO (DESPACHADOR)
# =================================================
@login_required
@empresa_required
@require_GET
def api_recorrido_vehiculo(request):
    """
    Devuelve el recorrido GPS de un vehículo SOLO durante su salida real.
    """

    vehiculo_id = request.GET.get("vehiculo")
    fecha = request.GET.get("fecha")

    if not vehiculo_id or not fecha:
        return JsonResponse({"error": "Parámetros incompletos"}, status=400)

    try:
        fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"error": "Fecha inválida"}, status=400)

    # 🏢 EMPRESA DESDE MIDDLEWARE
    empresa = request.empresa

    # 🚍 SALIDAS DEL VEHÍCULO ESE DÍA
    salidas = list(
        RegistroSalida.objects.for_empresa(empresa).filter(
            vehiculo_id=vehiculo_id,
            fecha=fecha_dt
        ).order_by("hora_real_salida")
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

        registros = GPSRegistro.objects.filter(
            sesion__vehiculo_id=vehiculo_id,
            timestamp__gte=inicio,
            timestamp__lte=fin
        ).order_by("timestamp")

        for r in registros:
            data.append({
                "lat": r.lat,
                "lng": r.lng,
                "hora": r.timestamp.strftime("%H:%M:%S"),
                "velocidad": r.velocidad or 0
            })

    return JsonResponse(data, safe=False)

# =================================================
# 🛑 API — PARADAS POR VEHÍCULO Y FECHA (DESPACHADOR)
# =================================================
@login_required
@empresa_required
@require_GET
def api_paradas_vehiculo(request):

    vehiculo_id = request.GET.get("vehiculo")
    fecha = request.GET.get("fecha")

    if not vehiculo_id or not fecha:
        return JsonResponse(
            {"error": "Parámetros incompletos"},
            status=400
        )

    try:
        fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse(
            {"error": "Fecha inválida"},
            status=400
        )

    # 🏢 EMPRESA DESDE MIDDLEWARE
    empresa = request.empresa

    paradas = (
        Parada.objects.for_empresa(empresa)
        .filter(
            vehiculo_id=vehiculo_id,
            inicio__date=fecha_dt
        )
        .order_by("inicio")
    )

    data = []

    for p in paradas:
        data.append({
            "lat": p.lat,
            "lng": p.lng,
            "inicio": p.inicio.strftime("%H:%M:%S"),
            "fin": p.fin.strftime("%H:%M:%S") if p.fin else None,
            "duracion_min": int(p.duracion_segundos / 60),
            "activa": p.activa,
        })

    return JsonResponse(data, safe=False)

# =================================================
# 🫀 API — HEARTBEAT (SEÑAL DE VIDA APP CONDUCTOR)
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
    sesion = validar_sesion(token)

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
# 🔐 API — ESCANEAR QR (ACTIVACIÓN DEFINITIVA)
# =================================================
@csrf_exempt
def api_escanear_qr(request):

    # =============================================
    # 🔥 PREFLIGHT (CORS)
    # =============================================
    if request.method == "OPTIONS":
        return JsonResponse(
            {},
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Credentials": "true",
            }
        )

    # =============================================
    # 🔒 SOLO POST
    # =============================================
    if request.method != "POST":
        return JsonResponse(
            {"ok": False, "error": "Método no permitido"},
            status=405,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Credentials": "true",
            }
        )

    # =============================================
    # 📦 LEER JSON (SEGURO)
    # =============================================
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse(
            {"ok": False, "error": "JSON inválido"},
            status=400,
            headers={"Access-Control-Allow-Origin": "*"}
        )

    # =============================================
    # 🚍 VALIDAR vehiculo_id
    # =============================================
    vehiculo_id = data.get("vehiculo_id")
    if not vehiculo_id:
        return JsonResponse(
            {"ok": False, "error": "vehiculo_id requerido"},
            status=400,
            headers={"Access-Control-Allow-Origin": "*"}
        )

    # =============================================
    # 🚍 BUSCAR VEHÍCULO
    # =============================================
    vehiculo = Vehiculo.objects.filter(
        id=vehiculo_id,
        activo=True
        ).select_related("empresa").first()
    if not vehiculo:
        return JsonResponse(
            {"ok": False, "error": "Unidad no registrada"},
            status=200,  # mantenemos tu contrato
            headers={"Access-Control-Allow-Origin": "*"}
        )

    # =============================================
    # 🔥 REGLA CLAVE:
    # TODO ESCANEO REEMPLAZA LA SESIÓN
    # =============================================
    SesionUnidad.objects.filter(
        vehiculo=vehiculo,
        activa=True
    ).update(activa=False)

    # =============================================
    # 🟢 CREAR NUEVA SESIÓN
    # =============================================
    sesion = SesionUnidad.objects.create(
        vehiculo=vehiculo,
        activa=True
    )

    # =============================================
    # ✅ RESPUESTA FINAL
    # =============================================
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
        }
    )

# =================================================
# 📱 API APP — ESTADO DE UNIDAD (FIX DEFINITIVO REAL)
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
    sesion = validar_sesion(token)
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
# 🚍 API APP — REFERENCIA DE TIEMPO (CORREGIDA + TIMEZONE OK)
# =================================================
@require_GET
def api_app_referencia_tiempo(request):

    # ---------------------------------------------
    # 🔐 VALIDAR TOKEN
    # ---------------------------------------------
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"ok": False}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({"ok": False}, status=403)

    hoy = timezone.localdate()

    # ---------------------------------------------
    # 🚍 SALIDA ACTIVA DE HOY
    # ---------------------------------------------
    salida = (
        RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa)
        .filter(
            vehiculo=sesion.vehiculo,
            fecha=hoy,
            activo=True
        )
        .order_by("-id")
        .first()
    )

    if not salida or not salida.hora_salida:
        return JsonResponse({"ok": False})

    # =================================================
    # 🕒 CONVERTIR HORA SALIDA A ZONA LOCAL
    # =================================================
    tz = timezone.get_current_timezone()
    hora_salida_local = timezone.localtime(salida.hora_salida, tz)

    # ---------------------------------------------
    # ✅ ÚLTIMO PUNTO MARCADO
    # ---------------------------------------------
    ultimo_marcado = (
        MarcacionPunto.objects
        .filter(
            registro_salida=salida,
            hora_marcada__isnull=False
        )
        .select_related("punto")
        .order_by("-punto__orden")
        .first()
    )

    # ---------------------------------------------
    # 📍 SI TODAVÍA NO MARCÓ NADA
    # ---------------------------------------------
    if not ultimo_marcado:

        primer_pendiente = salida.siguiente_marcacion()

        return JsonResponse({
            "ok": True,
            "salida": hora_salida_local.strftime("%H:%M"),
            "actual": {
                "codigo": primer_pendiente.punto.codigo if primer_pendiente else None,
                "diferencia": 0,
                "estado": None
            },
            "siguiente": None
        })

    # ---------------------------------------------
    # 📍 SIGUIENTE PUNTO
    # ---------------------------------------------
    siguiente = (
        MarcacionPunto.objects
        .filter(
            registro_salida=salida,
            punto__orden__gt=ultimo_marcado.punto.orden
        )
        .select_related("punto")
        .order_by("punto__orden")
        .first()
    )

    # =================================================
    # 🕒 CONVERTIR HORA SIGUIENTE A LOCAL
    # =================================================
    hora_siguiente_local = None
    if siguiente and siguiente.hora_programada:
        hora_siguiente_local = timezone.localtime(
            siguiente.hora_programada,
            tz
        )

    # ---------------------------------------------
    # 📤 RESPUESTA FINAL
    # ---------------------------------------------
    return JsonResponse({
        "ok": True,
        "salida": hora_salida_local.strftime("%H:%M"),

        "actual": {
            "codigo": ultimo_marcado.punto.codigo,
            "diferencia": ultimo_marcado.diferencia_minutos or 0,
            "estado": ultimo_marcado.estado
        },

        "siguiente": {
            "codigo": siguiente.punto.codigo if siguiente else None,
            "hora": (
                hora_siguiente_local.strftime("%H:%M")
                if hora_siguiente_local
                else None
            )
        }
    })

# =================================================
# 🚍 API APP — CONTEXTO DE COLA (MINUTOS REALES GPS)
# =================================================
@require_GET
def api_app_cola_contexto(request):
    """
    Devuelve el contexto de cola para la App Conductor:
    - unidades atrás
    - unidad actual
    - unidades adelante

    ✔ Muestra unidades aunque no tengan GPS
    ✔ Minutos = null hasta SALI
    """

    # =============================================
    # 🔑 VALIDAR TOKEN
    # =============================================
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JsonResponse({"ok": False}, status=401)

    token = auth.replace("Bearer ", "").strip()
    sesion = validar_sesion(token)
    if not sesion:
        return JsonResponse({"ok": False}, status=403)

    ahora = timezone.now()
    hoy = timezone.localdate()

    # =============================================
    # 🚍 SALIDA ACTUAL
    # =============================================
    salida_actual = (
        RegistroSalida.objects
        .for_empresa(sesion.vehiculo.empresa)
        .select_related("vehiculo", "ruta")
        .filter(
            vehiculo=sesion.vehiculo,
            fecha=hoy,
            activo=True,
            hora_salida__isnull=False
        )
        .order_by("hora_salida")
        .first()
    )

    if not salida_actual:
        return JsonResponse({"ok": False})

    # =============================================
    # 🚦 COLA COMPLETA (POR HORA)
    # =============================================
    cola = list(
        RegistroSalida.objects.for_empresa(sesion.vehiculo.empresa)
        .select_related("vehiculo")
        .filter(
            fecha=hoy,
            activo=True,
            ruta=salida_actual.ruta,
            vehiculo__empresa=sesion.vehiculo.empresa,
            hora_salida__isnull=False
        )
        .order_by("hora_salida")
    )

    index_actual = cola.index(salida_actual)

    atras = cola[max(0, index_actual - 2):index_actual]
    adelante = cola[index_actual + 1:index_actual + 3]

    # =============================================
    # ⏱️ MINUTOS REALES (GPS SI EXISTE)
    # =============================================
    GPS_MAX_DELAY = timedelta(seconds=60)
    VELOCIDAD_PROMEDIO = 25

    # 📍 UBICACIÓN ACTUAL DEL VEHÍCULO
    try:
        ub_actual = UbicacionVehiculo.objects.get(
            vehiculo=salida_actual.vehiculo
        )
    except UbicacionVehiculo.DoesNotExist:
        ub_actual = None

    # 📍 MAPA DE UBICACIONES (OPTIMIZADO 🔥)
    ubicaciones_map = {
        u.vehiculo_id: u
        for u in UbicacionVehiculo.objects.filter(
            vehiculo__in=[s.vehiculo for s in cola]
        )
    }

    def calcular_minutos(salida):
        # ❌ sin ubicación actual → no calcular
        if not ub_actual:
            return None

        ub = ubicaciones_map.get(salida.vehiculo.id)

        # ❌ no hay GPS de esa unidad
        if not ub:
            return None

        # ❌ GPS viejo
        if ahora - ub.updated_at > GPS_MAX_DELAY:
            return None

        distancia = distancia_metros(
            ub_actual.latitud,
            ub_actual.longitud,
            ub.latitud,
            ub.longitud
        )

        vel = ub.velocidad or VELOCIDAD_PROMEDIO
        metros_min = (vel * 1000) / 60

        if metros_min <= 0:
            return None

        return max(int(round(distancia / metros_min)), 0)

    def serializar(s):
        return {
            "unidad": s.vehiculo.codigo,
            "minutos": calcular_minutos(s)
        }

    # =============================================
    # 📤 RESPUESTA FINAL
    # =============================================
    return JsonResponse({
        "ok": True,
        "actual": {
            "unidad": salida_actual.vehiculo.codigo,
            "minutos": 0
        },
        "atras": [serializar(s) for s in atras],
        "adelante": [serializar(s) for s in adelante],
    })

# =================================================
# 📦 QR DE UNIDAD (JSON PURO – DEFINITIVO)
# =================================================
@login_required
@empresa_required
def ver_qr_unidad(request, vehiculo_id):
    """
    Genera el QR de la unidad.
    El QR contiene SOLO JSON con el ID del vehículo.
    """

    # 🏢 EMPRESA DESDE MIDDLEWARE
    empresa = request.empresa

    vehiculo = get_object_or_404(
        Vehiculo,
        id=vehiculo_id,
        empresa=empresa
    )

    # 🔑 CONTENIDO EXACTO DEL QR (JSON PURO)
    qr_data = json.dumps({
        "vehiculo_id": vehiculo.id
    })

    qr = qrcode.make(qr_data)

    buffer = BytesIO()
    qr.save(buffer, format="PNG")
    buffer.seek(0)

    return HttpResponse(
        buffer.getvalue(),
        content_type="image/png"
    )

# =================================================
# 🚦 COLA DE SALIDA (FIX DEFINITIVO PROFESIONAL)
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

# =================================================
# 🚦 QUITAR DE COLA (CANCELA SALIDA DEL DÍA)
# =================================================
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
# ⏱️ INTERVALO GLOBAL
# =================================================
@login_required
@empresa_required
@require_POST
def cambiar_intervalo_global(request):

    # 🏢 EMPRESA DESDE MIDDLEWARE
    empresa = request.empresa

    # Desactivar configuraciones anteriores
    ConfiguracionDespacho.objects.filter(
        empresa=empresa
    ).update(activa=False)

    accion = request.POST.get("accion")
    valor = request.POST.get("intervalo")

    if accion == "manual" and valor:
        ConfiguracionDespacho.objects.create(
            intervalo_fijo=int(valor),
            activa=True,
            empresa=empresa
        )
        messages.success(
            request,
            f"Intervalo fijo establecido en {valor} minutos."
        )
    else:
        ConfiguracionDespacho.objects.create(
            intervalo_fijo=None,
            activa=True,
            empresa=empresa
        )
        messages.success(
            request,
            "Intervalo automático activado."
        )

    # 🔥 recalcular con empresa limpia
    recalcular_cola(empresa=empresa)

    return redirect("panel_despachador")

# =================================================
# 🔒 ASIGNAR / REPROGRAMAR HORA FIJA A SALIDA
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

# =================================================
# 🔓 DESBLOQUEAR HORA FIJA
# =================================================
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

# =================================================
# 📊 HISTORIAL DE VEHÍCULO
# =================================================
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
# 🧭 CONTROL DE RUTA (DESPACHADOR)
# =================================================
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

# =================================================
# ✍️ MARCAR PASO (MANUAL DESPACHADOR)
# =================================================
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

# =================================================
# ✍️ MARCAR SIGUIENTE PUNTO (DESPACHADOR)
# =================================================
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

# =================================================
# 🔁 ALIAS: MARCAR SIGUIENTE PUNTO (AUTO / LEGACY)
# =================================================
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

# =================================================
# 📊 AUDITORÍA DE HORAS
# =================================================
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

# =================================================
# 📜 HISTORIAL GENERAL DE SALIDAS
# =================================================
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

# =================================================
# 📈 REPORTE DE CONTROL
# =================================================
@login_required
@empresa_required
def reporte_control(request):

    empresa = request.empresa

    salidas = (
        RegistroSalida.objects.for_empresa(empresa)
        .order_by("-fecha", "-hora_salida")
    )

    return render(
        request,
        "flota_app/despachador/reporte_control.html",
        {"salidas": salidas}
    )

# =================================================
# 📤 EXPORTAR EXCEL (PLACEHOLDER)
# =================================================
@login_required
def exportar_excel(request):
    """
    Exportación a Excel (pendiente).
    Placeholder seguro para evitar errores en URLs.
    """
    messages.info(
        request,
        "📄 Exportación a Excel aún no implementada."
    )
    return redirect("panel_despachador")


@login_required
@empresa_required
def debug_gps(request):

    empresa = request.empresa

    data = []

    for u in UbicacionVehiculo.objects.for_empresa(empresa).select_related("vehiculo"):
        data.append({
            "vehiculo": u.vehiculo.codigo,
            "lat": u.latitud,
            "lng": u.longitud,
            "updated_at": u.updated_at
        })

    return JsonResponse(data, safe=False)

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

    def get_success_url(self):

        user = self.request.user

        # ADMIN
        if user.is_superuser:
            return "/admin/"

        # DESPACHADOR
        if user.groups.filter(name="despachador").exists():
            return "/sistema/despachador/"

        # fallback
        return "/admin/"
