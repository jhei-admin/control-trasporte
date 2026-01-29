from django.urls import path
from . import views
from .views import debug_gps

urlpatterns = [

    # =========================
    # 📱 APP CONDUCTOR
    # =========================
    path(
        "conductor/",
        views.app_conductor,
        name="app_conductor"
    ),

    # =========================
    # 📡 APIs APP CONDUCTOR
    # =========================
    path(
        "api/app/escanear-qr/",
        views.api_escanear_qr,
        name="api_escanear_qr"
    ),
    path(
        "api/app/escanear-qr",
        views.api_escanear_qr
    ),

    path(
        "api/app/estado/",
        views.api_app_estado,
        name="api_app_estado"
    ),

    path(
        "api/app/gps/",
        views.api_gps_conductor,
        name="api_gps_conductor"
    ),

    # =========================
    # 🫀 API HEARTBEAT
    # =========================
    path(
        "api/app/heartbeat/",
        views.api_heartbeat,
        name="api_heartbeat"
    ),
    path(
        "api/app/heartbeat",
        views.api_heartbeat
    ),

    # =========================
    # 🗺️ API GPS — MAPA
    # =========================
    path(
        "api/gps/",
        views.api_gps,
        name="api_gps"
    ),
    path(
        "api/gps",
        views.api_gps
    ),

    # =========================
    # 🗺️ MAPA DESPACHADOR (API)
    # =========================
    path(
        "api/despachador/mapa/",
        views.api_despachador_mapa,
        name="api_despachador_mapa"
    ),
    path(
        "api/despachador/mapa",
        views.api_despachador_mapa
    ),

    # =========================
    # 🔴 API PUNTOS DE CONTROL
    # =========================
    path(
        "api/despachador/puntos-control/",
        views.api_puntos_control,
        name="api_puntos_control"
    ),
    path(
        "api/despachador/puntos-control",
        views.api_puntos_control
    ),

    # =========================
    # 🔎 API BÚSQUEDA VEHÍCULO
    # =========================
    path(
        "api/despachador/buscar-vehiculo/",
        views.api_buscar_vehiculo_por_codigo,
        name="api_buscar_vehiculo_por_codigo"
    ),
    path(
        "api/despachador/buscar-vehiculo",
        views.api_buscar_vehiculo_por_codigo
    ),

    # =========================
    # 🧭 API RECORRIDO
    # =========================
    path(
        "api/despachador/recorrido/",
        views.api_recorrido_vehiculo,
        name="api_recorrido_vehiculo"
    ),
    path(
        "api/despachador/recorrido",
        views.api_recorrido_vehiculo
    ),

    # =========================
    # 🛑 API PARADAS
    # =========================
    path(
        "api/despachador/paradas/",
        views.api_paradas_vehiculo,
        name="api_paradas_vehiculo"
    ),
    path(
        "api/despachador/paradas",
        views.api_paradas_vehiculo
    ),

    # =========================
    # 🚍 PANEL DESPACHADOR
    # =========================
    path(
        "despachador/",
        views.panel_despachador,
        name="panel_despachador"
    ),

    path(
        "despachador/mapa/",
        views.despachador_mapa,
        name="despachador_mapa"
    ),

    path(
        "despachador/recorrido/",
        views.recorrido_vehiculo,
        name="recorrido_vehiculo"
    ),

    # =========================
    # 🔍 BUSCADOR CLÁSICO (FORM POST)
    # =========================
    path(
        "despachador/buscar-unidad/",
        views.buscar_unidad_panel,
        name="buscar_unidad_panel"
    ),

    # =========================
    # 🚦 COLA
    # =========================
    path(
        "despachador/poner-en-cola/<int:salida_id>/",
        views.poner_en_cola,
        name="poner_en_cola"
    ),

    path(
        "despachador/quitar-de-cola/<int:salida_id>/",
        views.quitar_de_cola,
        name="quitar_de_cola"
    ),

    path(
        "despachador/asignar-hora-fija/<int:salida_id>/",
        views.asignar_hora_fija,
        name="asignar_hora_fija"
    ),

    path(
        "despachador/desbloquear-hora/<int:salida_id>/",
        views.desbloquear_hora,
        name="desbloquear_hora"
    ),

    path(
        "despachador/cambiar-intervalo-global/",
        views.cambiar_intervalo_global,
        name="cambiar_intervalo_global"
    ),

    # =========================
    # 📋 HISTORIAL
    # =========================
    path(
        "auditoria-horas/",
        views.auditoria_horas,
        name="auditoria_horas"
    ),

    path(
        "historial-salidas/",
        views.historial_salidas,
        name="historial_salidas"
    ),

    # =========================
    # 📍 DETALLE SALIDA
    # =========================
    path(
        "salida/<int:salida_id>/detalle/",
        views.detalle_salida,
        name="detalle_salida"
    ),

    # =========================
    # 🚦 CONTROL DE RUTA
    # =========================
    path(
        "control-ruta/<int:salida_id>/",
        views.control_ruta,
        name="control_ruta"
    ),

    path(
        "control-ruta/<int:salida_id>/marcar/<int:punto_id>/",
        views.marcar_paso,
        name="marcar_paso"
    ),

    path(
        "control-ruta/<int:salida_id>/marcar-siguiente/",
        views.marcar_siguiente_punto,
        name="marcar_siguiente_punto"
    ),

    # =========================
    # 📦 QR VEHÍCULO
    # =========================
    path(
        "vehiculo/<int:vehiculo_id>/qr/",
        views.ver_qr_unidad,
        name="ver_qr_unidad"
    ),

    # =========================
    # 🟡 REPORTES
    # =========================
    path(
        "reportes/salidas/<int:vehiculo_id>/",
        views.reporte_salidas_diarias,
        name="reporte_salidas_diarias"
    ),
    path("debug/gps/", debug_gps),
]
