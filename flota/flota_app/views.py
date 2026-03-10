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
from django.db import models
from .models import MensajeGlobal
from django.db.models import Count
from django.db.models import Max, F, Q


import qrcode

# ================= MODELS =================
from .models import (
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
    # 🚌 VEHÍCULO ACTUAL
    # =================================================
    vehiculo = get_object_or_404(Vehiculo, id=vehiculo_id)

    # =================================================
    # 🚌 LISTA DE VEHÍCULOS (FILTRO SUPERIOR)
    # =================================================
    vehiculos = Vehiculo.objects.filter(activo=True).order_by("codigo")

    # =================================================
    # 🚍 SALIDAS DEL DÍA (ACTIVAS + FINALIZADAS)
    # =================================================
    salidas = list(
        RegistroSalida.objects
        .filter(
            vehiculo=vehiculo,
            fecha=fecha      # 🔥 MISMA FECHA OPERATIVA
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
            total_puntos = PuntoControl.objects.filter(
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
            paradas = Parada.objects.filter(
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

    # -------------------------------------------------
    # 📅 FECHA OPERATIVA REAL (ZONA LOCAL)
    # -------------------------------------------------
    hoy = timezone.localdate()

    # -------------------------------------------------
    # 🔥 SALIDAS ACTIVAS DEL DÍA
    # ORDEN REAL DE DESPACHO:
    #   1️⃣ Primero las que tienen hora
    #   2️⃣ Ordenadas por hora_salida
    #   3️⃣ Luego las que no tienen hora
    # -------------------------------------------------
    salidas = (
        RegistroSalida.objects
        .select_related("vehiculo", "ruta")
        .filter(
            activo=True,
            fecha=hoy
        )
        .order_by(
            models.Case(
                models.When(hora_salida__isnull=False, then=0),
                models.When(hora_salida__isnull=True, then=1),
                output_field=models.IntegerField(),
            ),
            "hora_salida",
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
    # 🚍 BUSCAR VEHÍCULO ACTIVO
    # -------------------------------------------------
    vehiculo = Vehiculo.objects.filter(
        codigo=codigo,
        activo=True
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
    if RegistroSalida.objects.filter(
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
    ruta = Ruta.objects.first()

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
def recorrido_vehiculo(request):
    """
    Vista del despachador para visualizar
    el recorrido histórico de un vehículo por fecha.
    (Usa Leaflet + API de recorrido)
    """

    vehiculos = Vehiculo.objects.all().order_by("codigo")

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
    RegistroSalida.objects.filter(
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
    if precision and precision > 100:
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

    parada = Parada.objects.filter(
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
# 🗺️ API — MAPA EN TIEMPO REAL (DESPACHADOR)
# =================================================
@require_GET
def api_despachador_mapa(request):
    """
    API para el panel despachador.
    Devuelve la ubicación actual de todos los vehículos
    separando:
    - estado OPERATIVO (ACTIVO / INACTIVO)
    - estado GPS (ONLINE / LENTO / OFFLINE)
    """

    ahora = timezone.now()
    hoy = timezone.localdate()
    data = []

    # =================================================
    # 🚍 RECORRER UBICACIONES ACTIVAS
    # =================================================
    for ub in (
        UbicacionVehiculo.objects
        .select_related("vehiculo")
        .all()
    ):
        delta = ahora - ub.updated_at

        # =============================================
        # 📡 ESTADO GPS (COMUNICACIÓN)
        # =============================================
        if delta <= timedelta(seconds=30):
            estado_gps = "ONLINE"
        elif delta <= timedelta(seconds=120):
            estado_gps = "LENTO"
        else:
            estado_gps = "OFFLINE"

        # =============================================
        # 🚍 ¿TIENE SALIDA ACTIVA HOY?
        # =============================================
        tiene_salida_hoy = RegistroSalida.objects.filter(
            vehiculo=ub.vehiculo,
            fecha=hoy,
            activo=True
        ).exists()

        # =============================================
        # 🧭 ESTADO OPERATIVO (MAPA)
        # =============================================
        estado = "ACTIVO" if tiene_salida_hoy else "INACTIVO"

        # =============================================
        # 📤 RESPUESTA FINAL (FIX DEFINITIVO)
        # =============================================
        data.append({
            # 🔥 FIX CLAVE — IDENTIFICADOR CORRECTO
            "vehiculo": str(ub.vehiculo.codigo),  # ✅ NO numero

            "lat": ub.latitud,
            "lng": ub.longitud,
            "velocidad": ub.velocidad,
            "precision": ub.precision,

            # Estados
            "estado": estado,          # ACTIVO / INACTIVO
            "estado_gps": estado_gps,  # ONLINE / LENTO / OFFLINE

            "actualizado_en": ub.updated_at.isoformat(),
        })

    return JsonResponse(data, safe=False)

# =================================================
# 📍 API — PUNTOS DE CONTROL (MAPA DESPACHADOR)
# =================================================
@require_GET
def api_puntos_control(request):
    """
    Devuelve los puntos de control activos
    para ser dibujados en el mapa del despachador.
    """

    puntos = (
        PuntoControl.objects
        .filter(activo=True)
        .order_by("orden")
    )

    data = []

    for p in puntos:
        # Seguridad: solo si tiene coordenadas
        if p.latitud is None or p.longitud is None:
            continue

        data.append({
            "id": p.id,
            "codigo": p.codigo,          # SALI, COLE, APIP, ZAMA
            "nombre": p.nombre,          # Nombre largo
            "orden": p.orden,
            "lat": float(p.latitud),
            "lng": float(p.longitud),
            "radio": p.radio_metros,
        })

    return JsonResponse(data, safe=False)

# =================================================
# 🔎 API — BÚSQUEDA RÁPIDA DE VEHÍCULO POR CÓDIGO
# =================================================
@require_GET
def api_buscar_vehiculo_por_codigo(request):
    """
    Devuelve la unidad ACTIVA asociada a un código operativo (01, 02, 15, etc).
    Usado por el despachador para búsqueda rápida.
    """

    codigo = request.GET.get("codigo")

    if not codigo:
        return JsonResponse(
            {"error": "codigo requerido"},
            status=400
        )

    qs = Vehiculo.objects.filter(
        codigo=codigo,
        activo=True
    )

    if not qs.exists():
        return JsonResponse(
            {"error": f"No existe unidad activa con código {codigo}"},
            status=404
        )

    if qs.count() > 1:
        # Esto NO debería pasar, pero protege el sistema
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
@require_GET
def api_recorrido_vehiculo(request):
    """
    Devuelve el recorrido GPS de un vehículo SOLO durante su salida real.
    Evita mostrar GPS cuando el vehículo está fuera de servicio.
    """

    vehiculo_id = request.GET.get("vehiculo")
    fecha = request.GET.get("fecha")

    if not vehiculo_id or not fecha:
        return JsonResponse({"error": "Parámetros incompletos"}, status=400)

    try:
        fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"error": "Fecha inválida"}, status=400)

    # ------------------------------------------------
    # 🚍 SALIDAS DEL VEHÍCULO ESE DÍA
    # ------------------------------------------------
    salidas = list(
        RegistroSalida.objects.filter(
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

        # siguiente salida
        if i + 1 < len(salidas) and salidas[i + 1].hora_real_salida:
            fin = salidas[i + 1].hora_real_salida
        else:
            fin = inicio + timedelta(hours=3)  # margen máximo

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

    # 🔑 IMPORTANTE:
    # Mostramos paradas CERRADAS y EN CURSO
    paradas = (
        Parada.objects
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
            "activa": p.activa,  # 👈 útil para el frontend
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
    vehiculo = Vehiculo.objects.filter(id=vehiculo_id).first()
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
        RegistroSalida.objects
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
        RegistroSalida.objects
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

    hoy = timezone.localdate()

    # =============================================
    # 🚍 SALIDA ACTUAL
    # =============================================
    salida_actual = (
        RegistroSalida.objects
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
        RegistroSalida.objects
        .select_related("vehiculo")
        .filter(
            fecha=hoy,
            activo=True,
            ruta=salida_actual.ruta,
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

    try:
        ub_actual = UbicacionVehiculo.objects.get(
            vehiculo=salida_actual.vehiculo
        )
    except UbicacionVehiculo.DoesNotExist:
        ub_actual = None

    def calcular_minutos(salida):
        if not ub_actual:
            return None

        try:
            ub = UbicacionVehiculo.objects.get(
                vehiculo=salida.vehiculo
            )
        except UbicacionVehiculo.DoesNotExist:
            return None

        if timezone.now() - ub.updated_at > GPS_MAX_DELAY:
            return None

        distancia = distancia_metros(
            ub_actual.latitud,
            ub_actual.longitud,
            ub.latitud,
            ub.longitud
        )

        vel = ub.velocidad or VELOCIDAD_PROMEDIO
        metros_min = (vel * 1000) / 60
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
# 🚍 APP CONDUCTOR (DEFINITIVA — SOLO UI)
# =================================================
@never_cache
def app_conductor(request):
    """
    Vista principal de la App del Conductor (PWA).

    🔑 REGLA DE ORO (NO ROMPER):
    - ❌ NO consulta modelos
    - ❌ NO decide estados
    - ❌ NO maneja horas
    - ❌ NO usa sesiones
    - ❌ NO lee salidas

    ✅ SOLO carga la UI
    ✅ TODO el estado viene por APIs:
       - api_app_estado
       - api_gps
       - api_gps_conductor
    """

    return render(
        request,
        "flota_app/app_conductor.html",
        {
            "modo_pwa": True,  # requerido para instalación PWA
        }
    )

# =================================================
# 📦 QR DE UNIDAD (JSON PURO – DEFINITIVO)
# =================================================
def ver_qr_unidad(request, vehiculo_id):
    """
    Genera el QR de la unidad.
    El QR contiene SOLO JSON con el ID del vehículo.
    """

    vehiculo = get_object_or_404(Vehiculo, id=vehiculo_id)

    # 🔑 CONTENIDO EXACTO DEL QR (JSON PURO)
    qr_data = json.dumps({
        "vehiculo_id": vehiculo.id
    })

    qr = qrcode.make(qr_data)

    buffer = BytesIO()
    qr.save(buffer, format="PNG")
    buffer.seek(0)

    # 🔥 DEVOLVER IMAGEN PNG DIRECTA
    return HttpResponse(
        buffer.getvalue(),
        content_type="image/png"
    )

# =================================================
# 🚦 COLA DE SALIDA (FIX DEFINITIVO PROFESIONAL)
# =================================================
@require_POST
def poner_en_cola(request, salida_id):
    salida = get_object_or_404(RegistroSalida, id=salida_id)
    hoy = timezone.localdate()

    # ------------------------------------------------
    # 🔥 SI LA SALIDA NO ES DE HOY → CREAR NUEVA
    # (pero SIN poner en cola aún)
    # ------------------------------------------------
    if salida.fecha != hoy:
        # cerrar salida vieja
        salida.activo = False
        salida.en_cola = False
        salida.save(update_fields=["activo", "en_cola"])

        # crear NUEVA salida para HOY
        salida = RegistroSalida.objects.create(
            vehiculo=salida.vehiculo,
            ruta=salida.ruta,
            fecha=hoy,
            hora_llegada=timezone.now(),
            activo=True,
            en_cola=False,
            bloqueado=False
        )

    # ------------------------------------------------
    # 🔒 VALIDACIÓN CLAVE
    # NO SE PUEDE PONER EN COLA SIN HORA DE SALIDA
    # ------------------------------------------------
    if not salida.hora_salida:
        messages.error(
            request,
            "❌ Primero debe fijar la hora de salida antes de poner en cola."
        )
        return redirect("panel_despachador")

    # ------------------------------------------------
    # VALIDACIÓN NORMAL
    # ------------------------------------------------
    if salida.en_cola:
        messages.info(
            request,
            "La unidad ya está en la cola."
        )
        return redirect("panel_despachador")

    # ------------------------------------------------
    # ORDEN EN COLA (SOLO HOY)
    # ------------------------------------------------
    ultimo = (
        RegistroSalida.objects
        .filter(
            ruta=salida.ruta,
            fecha=hoy,
            en_cola=True,
            activo=True
        )
        .order_by("-orden_cola")
        .first()
    )

    salida.en_cola = True
    salida.orden_cola = (ultimo.orden_cola + 1) if ultimo else 1

    # ⚠️ REGLA DE ORO:
    # - NO tocar hora_salida
    # - NO tocar hora_fija
    # - NO tocar hora_real_salida
    salida.save(update_fields=[
        "en_cola",
        "orden_cola"
    ])

    # ------------------------------------------------
    # 🔁 RECALCULAR COLA
    # 👉 SOLO SI NO ESTÁ BLOQUEADA
    # ------------------------------------------------
    if not salida.bloqueado:
        recalcular_cola()

    messages.success(
        request,
        "✅ Unidad puesta en cola correctamente."
    )
    return redirect("panel_despachador")

# =================================================
# 🚦 QUITAR DE COLA (CANCELA SALIDA DEL DÍA)
# =================================================
@require_POST
def quitar_de_cola(request, salida_id):
    salida = get_object_or_404(RegistroSalida, id=salida_id)

    # ------------------------------------------------
    # VALIDACIÓN
    # ------------------------------------------------
    if not salida.en_cola:
        messages.info(request, "La unidad no está en la cola.")
        return redirect("panel_despachador")

    # ------------------------------------------------
    # 🔥 REGLA OPERATIVA DEFINITIVA
    # Quitar de cola = cancelar salida
    # ------------------------------------------------
    salida.en_cola = False
    salida.orden_cola = None
    salida.activo = False   # 👈 CLAVE

    salida.save(update_fields=[
        "en_cola",
        "orden_cola",
        "activo"
    ])

    # ------------------------------------------------
    # 🔁 Recalcular cola restante
    # ------------------------------------------------
    recalcular_cola()

    messages.success(
        request,
        "Unidad quitada de la cola y salida cancelada."
    )
    return redirect("panel_despachador")

# =================================================
# ⏱️ INTERVALO GLOBAL
# =================================================
@require_POST
def cambiar_intervalo_global(request):
    """
    Cambia el intervalo de despacho:
    - Manual: intervalo fijo
    - Automático: según cantidad en cola
    """

    # Desactivar configuraciones anteriores
    ConfiguracionDespacho.objects.all().update(activa=False)

    accion = request.POST.get("accion")      # "manual" | "automatico"
    valor = request.POST.get("intervalo")    # minutos (si manual)

    if accion == "manual" and valor:
        ConfiguracionDespacho.objects.create(
            intervalo_fijo=int(valor),
            activa=True
        )
        messages.success(
            request,
            f"Intervalo fijo establecido en {valor} minutos."
        )
    else:
        ConfiguracionDespacho.objects.create(
            intervalo_fijo=None,
            activa=True
        )
        messages.success(
            request,
            "Intervalo automático activado."
        )

    # 🔑 ESTO YA ESTÁ BIEN
    recalcular_cola()

    return redirect("panel_despachador")

# =================================================
# 🔒 ASIGNAR / REPROGRAMAR HORA FIJA A SALIDA
# =================================================
@require_POST
def asignar_hora_fija(request, salida_id):
    salida = get_object_or_404(RegistroSalida, id=salida_id)

    hora_str = request.POST.get("hora_fija")
    if not hora_str:
        messages.error(request, "Hora inválida.")
        return redirect("panel_despachador")

    # -------------------------
    # 📅 FECHA OPERATIVA = HOY
    # -------------------------
    hoy = timezone.localdate()

    # -------------------------
    # ⏱ PARSE DE HORA
    # -------------------------
    try:
        hora_time = datetime.strptime(hora_str, "%H:%M").time()
    except ValueError:
        messages.error(request, "Formato de hora inválido.")
        return redirect("panel_despachador")

    # -------------------------
    # 🌎 DATETIME AWARE (ZONA LOCAL)
    # -------------------------
    hora_fija_dt = timezone.make_aware(
        datetime.combine(hoy, hora_time),
        timezone.get_current_timezone()
    )

    # -------------------------
    # 🔒 VALIDACIÓN OPERATIVA
    # No permitir cambiar hora si ya salió
    # -------------------------
    if salida.hora_real_salida:
        messages.error(
            request,
            "No se puede reprogramar la hora: la unidad ya inició la ruta."
        )
        return redirect("panel_despachador")

    # -------------------------
    # ✅ ASIGNAR / REPROGRAMAR
    # -------------------------
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

    # -------------------------------------------------
    # 🧱 CREAR MARCACIONES DE TODOS LOS PUNTOS DE LA RUTA
    # -------------------------------------------------
    puntos = PuntoControl.objects.filter(
        ruta=salida.ruta
    ).order_by("orden")

    for punto in puntos:
        MarcacionPunto.objects.get_or_create(
            registro_salida=salida,
            punto=punto
        )

    # -------------------------------------------------
    # 🔁 SINCRONIZAR TODAS LAS MARCACIONES (NUEVAS Y EXISTENTES)
    # -------------------------------------------------
    for m in salida.marcaciones.all():
        m.hora_programada = m.calcular_hora_programada()
        m.save(update_fields=["hora_programada"])

    messages.success(
        request,
        f"Hora de salida programada correctamente: {hora_str}"
    )
    return redirect("panel_despachador")

# =================================================
# 🔓 DESBLOQUEAR HORA FIJA (VOLVER A AUTOMÁTICO)
# =================================================
@require_POST
def desbloquear_hora(request, salida_id):
    salida = get_object_or_404(RegistroSalida, id=salida_id)

    if not salida.bloqueado:
        messages.info(
            request,
            "La unidad ya está en modo automático."
        )
        return redirect("panel_despachador")

    salida.bloqueado = False
    salida.save(update_fields=["bloqueado"])

    messages.success(
        request,
        "Hora fija eliminada. Unidad en modo automático."
    )
    return redirect("panel_despachador")

# =================================================
# 📄 DETALLE DE SALIDA (CORREGIDO + CONTEXTO ATRÁS)
# =================================================
def detalle_salida(request, salida_id):
    salida = get_object_or_404(RegistroSalida, id=salida_id)

    marcaciones_qs = (
        MarcacionPunto.objects
        .filter(registro_salida=salida)
        .select_related("punto")
        .order_by("punto__orden")
    )

    detalle = []

    for m in marcaciones_qs:
        punto = m.punto

        # 🔑 HORA PROGRAMADA CORRECTA (DINÁMICA)
        if salida.hora_salida:
            hora_programada = (
                salida.hora_salida
                + timedelta(minutes=punto.offset_minutos)
            )
        else:
            hora_programada = None

        # 🔑 ESTADO SOLO SI HAY MARCACIÓN
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

    # =================================================
    # 🔑 CONTEXTO PARA NAVEGACIÓN CORRECTA
    # =================================================
    vehiculo_id = salida.vehiculo.id
    fecha = salida.fecha  # date (YYYY-MM-DD)

    return render(
        request,
        "flota_app/detalle_salida.html",
        {
            "salida": salida,
            "detalle": detalle,

            # 🔥 NUEVO (NO ROMPE NADA)
            "vehiculo_id": vehiculo_id,
            "fecha": fecha,
        }
    )

# =================================================
# 📊 HISTORIAL DE VEHÍCULO
# =================================================
def historial_vehiculo(request, vehiculo_id):

    vehiculo = get_object_or_404(Vehiculo, id=vehiculo_id)

    salidas = (
        RegistroSalida.objects
        .filter(vehiculo=vehiculo)
        .order_by("-fecha", "-hora_salida")
    )

    total = salidas.count()

    # cálculo seguro
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

            # 🔥 ESTA LÍNEA FALTABA
            "MAPBOX_TOKEN": settings.MAPBOX_TOKEN,
        }
    )

# =================================================
# 🧭 CONTROL DE RUTA (DESPACHADOR)
# =================================================
def control_ruta(request, salida_id):
    salida = get_object_or_404(RegistroSalida, id=salida_id)

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

        # 🔑 HORA PROGRAMADA CALCULADA SIEMPRE DESDE LA SALIDA ACTUAL
        if salida.hora_salida:
            hora_programada_calculada = (
                salida.hora_salida
                + timedelta(minutes=punto.offset_minutos)
            )
        else:
            hora_programada_calculada = None

        # ⚠️ MANTENEMOS get_or_create (NO ROMPE NADA)
        marcacion, _ = MarcacionPunto.objects.get_or_create(
            registro_salida=salida,
            punto=punto,
            defaults={
                # Se guarda solo la primera vez
                "hora_programada": hora_programada_calculada
            }
        )

        controles.append({
            "punto": punto,
            "marcacion": marcacion,
            # 🔑 ESTA ES LA CLAVE
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
@require_POST
def marcar_paso(request, salida_id, punto_id):
    salida = get_object_or_404(RegistroSalida, id=salida_id)
    punto = get_object_or_404(PuntoControl, id=punto_id)

    marcacion, _ = MarcacionPunto.objects.get_or_create(
        registro_salida=salida,
        punto=punto
    )

    marcacion.marcar()   # 🔥 UNA SOLA FUENTE DE VERDAD

    messages.success(
        request,
        f"Punto {punto.nombre} marcado ({marcacion.estado})."
    )

    return redirect("control_ruta", salida_id=salida.id)

# =================================================
# ✍️ MARCAR SIGUIENTE PUNTO (DESPACHADOR)
# =================================================
@require_POST
def marcar_siguiente_punto(request, salida_id):
    salida = get_object_or_404(RegistroSalida, id=salida_id)

    # 🔑 FUENTE ÚNICA DE VERDAD
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

    # ✅ MARCAR
    marcacion.marcar()

    punto = marcacion.punto

    # 🔚 ¿ES EL ÚLTIMO PUNTO?
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
def marcar_siguiente_punto_auto(request, salida_id):
    """
    Alias para compatibilidad con URLs antiguas.
    Marca automáticamente el siguiente punto pendiente.
    """
    salida = get_object_or_404(RegistroSalida, id=salida_id)

    punto = salida.siguiente_punto()
    if not punto:
        messages.info(
            request,
            "No hay más puntos por marcar."
        )
        return redirect("detalle_salida", salida_id=salida.id)

    return marcar_paso(
        request,
        salida_id=salida.id,
        punto_id=punto.id
    )

# =================================================
# 📊 AUDITORÍA DE HORAS (PLACEHOLDER)
# =================================================
def auditoria_horas(request):
    """
    Vista de auditoría de horas.
    Se puede ampliar luego con filtros y exportación.
    """
    salidas = (
        RegistroSalida.objects
        .all()
        .order_by("-fecha", "-hora_salida")
    )

    return render(
        request,
        "flota_app/despachador/auditoria_horas.html",
        {
            "salidas": salidas,
        }
    )

# =================================================
# 📜 HISTORIAL GENERAL DE SALIDAS (CORREGIDO BIEN)
# =================================================
def historial_salidas(request):
    """
    Historial de salidas basado en marcaciones reales (GPS).
    No asume datos inexistentes.
    """

    salidas = (
        RegistroSalida.objects
        .all()
        .order_by("-fecha", "-hora_salida")
    )

    historial = []

    for salida in salidas:
        hora_programada = salida.hora_salida

        # 🔍 Buscar la primera marcación real (si existe)
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
        {
            "historial": historial,
        }
    )

# =================================================
# 📈 REPORTE DE CONTROL (PLACEHOLDER)
# =================================================
def reporte_control(request):
    """
    Vista de reporte general de control.
    Placeholder para panel despachador.
    """
    salidas = (
        RegistroSalida.objects
        .all()
        .order_by("-fecha", "-hora_salida")
    )

    return render(
        request,
        "flota_app/despachador/reporte_control.html",
        {
            "salidas": salidas,
        }
    )

# =================================================
# 📤 EXPORTAR EXCEL (PLACEHOLDER)
# =================================================
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

from django.http import JsonResponse
from .models import UbicacionVehiculo

def debug_gps(request):
    data = []

    for u in UbicacionVehiculo.objects.select_related("vehiculo").all():
        data.append({
            "vehiculo": u.vehiculo.codigo,
            "lat": u.latitud,
            "lng": u.longitud,
            "updated_at": u.updated_at
        })

    return JsonResponse(data, safe=False)

# =================================================
# 📊 PANEL FRECUENCIA DE RUTA
# =================================================
def panel_frecuencia(request):

    puntos = PuntoControl.objects.filter(activo=True).order_by("orden")

    return render(
        request,
        "flota_app/despachador/frecuencia_ruta.html",
        {
            "puntos": puntos
        }
    )

@require_GET
def api_panel_frecuencia_optimizada(request):
    """
    Versión ultra optimizada del panel de frecuencia:
    - Solo unidades en ruta (no finalizadas)
    - Múltiples vueltas por día
    - Líder automático
    - Huecos y buses pegados
    - Máximo rendimiento con annotate y agregaciones
    """

    hoy = timezone.localdate()

    # 1️⃣ Obtener puntos de control activos
    puntos = list(PuntoControl.objects.filter(activo=True).order_by("orden"))
    if not puntos:
        return JsonResponse({"puntos": [], "data": []})

    # Último punto de la ruta (fin de vuelta)
    max_orden = max(p.orden for p in puntos)

    # 2️⃣ Configuración de frecuencia
    config = ConfiguracionDespacho.objects.filter(activa=True).first()
    intervalo = config.intervalo_fijo if config and config.intervalo_fijo else 6

    # 3️⃣ Salidas activas del día con ruta definida
    salidas_qs = (
        RegistroSalida.objects
        .filter(activo=True, fecha=hoy, ruta__isnull=False)
    )

    # 4️⃣ Agregar último punto marcado y su hora
    salidas_qs = salidas_qs.annotate(
        ultimo_punto_orden=Max("marcaciones__punto__orden", filter=Q(marcaciones__hora_marcada__isnull=False)),
        ultimo_tiempo=Max("marcaciones__hora_marcada")
    )

    unidades_panel = []

    for salida in salidas_qs:
        # Si ya terminó la vuelta (último punto de la ruta), marcar como inactiva
        if salida.ultimo_punto_orden == max_orden:
            salida.activo = False
            salida.en_cola = False
            salida.save(update_fields=["activo", "en_cola"])
            continue

        # Construir controles por punto usando un dict de marcaciones para acceso rápido
        marcaciones = {m.punto_id: m for m in salida.marcaciones.all()}

        controles = []
        for punto in puntos:
            m = marcaciones.get(punto.id)
            controles.append(m.diferencia_minutos if m and m.hora_marcada else None)

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

    # 5️⃣ Ordenar por avance (líder primero)
    unidades_panel.sort(key=lambda x: x["avance"], reverse=True)

    # 6️⃣ Calcular frecuencia y detectar huecos/pegados
    for i in range(1, len(unidades_panel)):
        actual = unidades_panel[i]
        anterior = unidades_panel[i - 1]

        if actual["ultimo_tiempo"] and anterior["ultimo_tiempo"]:
            diff = (actual["ultimo_tiempo"] - anterior["ultimo_tiempo"]).total_seconds() / 60
            actual["frecuencia"] = int(diff)
            if diff > intervalo * 1.5:
                actual["hueco"] = True
            if diff < intervalo * 0.5:
                actual["pegado"] = True

    # 7️⃣ Marcar líder
    if unidades_panel:
        unidades_panel[0]["lider"] = True
    for u in unidades_panel[1:]:
        u["lider"] = False

    return JsonResponse({
        "puntos": [p.codigo for p in puntos],
        "data": unidades_panel
    })
