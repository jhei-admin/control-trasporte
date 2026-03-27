from django.urls import path
from .api.despacho_api import debug_gps
from .views.despacho_views import (
    panel_despachador,
    buscar_unidad_panel,
    despachador_mapa,
    recorrido_vehiculo,
    poner_en_cola,
    quitar_de_cola,
    asignar_hora_fija,
    desbloquear_hora,
    cambiar_intervalo_global,
    auditoria_horas,
    historial_salidas,
    historial_vehiculo,
    control_ruta,
    marcar_paso,
    marcar_siguiente_punto,
    ver_qr_unidad,
    panel_frecuencia,
    detalle_salida,
)

from .views.operacion_views import (
    api_gps,
    api_gps_conductor,
    api_app_estado,
    api_app_referencia_tiempo,
    api_app_cola_contexto,
    api_heartbeat,
    api_despachador_mapa,
    api_puntos_control,
    api_buscar_vehiculo_por_codigo,
    api_recorrido_vehiculo,
    api_paradas_vehiculo,
    api_panel_frecuencia,
    api_escanear_qr,
)

from .views.reportes_views import (
    reporte_salidas_diarias,
)

urlpatterns = [

    # =========================
    # 📡 APIs APP CONDUCTOR
    # =========================
    path("api/gps/",
         api_gps_conductor,
         name="api_gps_conductor"
    ),
    path(
        "api/app/escanear-qr/",
         api_escanear_qr,
        name="api_escanear_qr"
    ),
    path(
        "api/app/escanear-qr",
         api_escanear_qr
    ),

    path(
        "api/app/estado/",
        api_app_estado,
        name="api_app_estado"
    ),

    path(
        "api/app/referencia-tiempo/",
        api_app_referencia_tiempo,
        name="api_app_referencia_tiempo"
    ),

    # 🆕 CONTEXTO DE COLA (GPS IDEOVAL)
    path(
        "api/app/cola-contexto/",
         api_app_cola_contexto,
        name="api_app_cola_contexto"    
    ),
    path(
        "api/app/cola-contexto",
         api_app_cola_contexto
    ),

    # ✔️ GPS CONDUCTOR (ruta original)
    path(
        "api/app/gps/",
         api_gps_conductor,
        name="api_gps_conductor"
    ),

    # =================================================
    # 🔥 FIX CLAVE — ALIAS PARA APP CONDUCTOR
    # =================================================
    path(
        "api/gps/conductor/",
         api_gps_conductor
    ),
    path(
        "api/gps/conductor",
         api_gps_conductor
    ),

    # =========================
    # 🫀 API HEARTBEAT
    # =========================
    path(
        "api/app/heartbeat/",
         api_heartbeat,
        name="api_heartbeat"
    ),
    path(
        "api/app/heartbeat",
         api_heartbeat
    ),

    # =========================
    # 🗺️ API GPS — MAPA
    # =========================
    path(
        "api/gps/",
         api_gps,
        name="api_gps"
    ),
    path(
        "api/gps",
         api_gps
    ),

    # =========================
    # 🗺️ MAPA DESPACHADOR (API)
    # =========================
    path(
        "api/despachador/mapa/",
         api_despachador_mapa,
        name="api_despachador_mapa"
    ),
    path(
        "api/despachador/mapa",
         api_despachador_mapa
    ),

    # =========================
    # 🔴 API PUNTOS DE CONTROL
    # =========================
    path(
        "api/despachador/puntos-control/",
         api_puntos_control,
        name="api_puntos_control"
    ),
    path(
        "api/despachador/puntos-control",
         api_puntos_control
    ),

    # =========================
    # 🔎 API BÚSQUEDA VEHÍCULO
    # =========================
    path(
        "api/despachador/buscar-vehiculo/",
         api_buscar_vehiculo_por_codigo,
        name="api_buscar_vehiculo_por_codigo"
    ),
    path(
        "api/despachador/buscar-vehiculo",
         api_buscar_vehiculo_por_codigo
    ),

    # =========================
    # 🧭 API RECORRIDO
    # =========================
    path(
        "api/despachador/recorrido/",
         api_recorrido_vehiculo,
        name="api_recorrido_vehiculo"
    ),
    path(
        "api/despachador/recorrido",
         api_recorrido_vehiculo
    ),

    # =========================
    # 🛑 API PARADAS
    # =========================
    path(
        "api/despachador/paradas/",
         api_paradas_vehiculo,
        name="api_paradas_vehiculo"
    ),
    path(
        "api/despachador/paradas",
         api_paradas_vehiculo
    ),

    # =========================
    # 🚍 PANEL DESPACHADOR
    # =========================
    path(
        "despachador/",
         panel_despachador,
        name="panel_despachador"
    ),

    path(
        "despachador/mapa/",
         despachador_mapa,
        name="despachador_mapa"
    ),

    path(
        "despachador/recorrido/",
         recorrido_vehiculo,
        name="recorrido_vehiculo"
    ),

    # =========================
    # 🔍 BUSCADOR CLÁSICO
    # =========================
    path(
        "despachador/buscar-unidad/",
         buscar_unidad_panel,
        name="buscar_unidad_panel"
    ),

    # =========================
    # 🚦 COLA
    # =========================
    path(
        "despachador/poner-en-cola/<int:salida_id>/",
         poner_en_cola,
        name="poner_en_cola"
    ),

    path(
        "despachador/quitar-de-cola/<int:salida_id>/",
         quitar_de_cola,
        name="quitar_de_cola"
    ),

    path(
        "despachador/asignar-hora-fija/<int:salida_id>/",
         asignar_hora_fija,
        name="asignar_hora_fija"
    ),

    path(
        "despachador/desbloquear-hora/<int:salida_id>/",
         desbloquear_hora,
        name="desbloquear_hora"
    ),

    path(
        "despachador/cambiar-intervalo-global/",
         cambiar_intervalo_global,
        name="cambiar_intervalo_global"
    ),

    # =========================
    # 📋 HISTORIAL
    # =========================
    path(
        "auditoria-horas/",
         auditoria_horas,
        name="auditoria_horas"
    ),

    path(
        "historial-salidas/",
         historial_salidas,
        name="historial_salidas"
    ),

    path(
    "despachador/historial/<int:vehiculo_id>/",
     historial_vehiculo,
    name="historial_vehiculo"
    ),

    # =========================
    # 📍 DETALLE SALIDA
    # =========================
    path(
        "salida/<int:salida_id>/detalle/",
         detalle_salida,
        name="detalle_salida"
    ),

    # =========================
    # 🚦 CONTROL DE RUTA
    # =========================
    path(
        "control-ruta/<int:salida_id>/",
         control_ruta,
        name="control_ruta"
    ),

    path(
        "control-ruta/<int:salida_id>/marcar/<int:punto_id>/",
         marcar_paso,
        name="marcar_paso"
    ),

    path(
        "control-ruta/<int:salida_id>/marcar-siguiente/",
         marcar_siguiente_punto,
        name="marcar_siguiente_punto"
    ),

    # =========================
    # 📦 QR VEHÍCULO
    # =========================
    path(
        "vehiculo/<int:vehiculo_id>/qr/",
         ver_qr_unidad,
        name="ver_qr_unidad"
    ),

    # =========================
    # 🟡 REPORTES
    # =========================
    path(
        "reportes/salidas/<int:vehiculo_id>/",
         reporte_salidas_diarias,
        name="reporte_salidas_diarias"
    ),

    path(
        "despachador/frecuencia/",
         panel_frecuencia,
        name="panel_frecuencia"
    ),
    path(
        "api/frecuencia/",
         api_panel_frecuencia,
        name="api_panel_frecuencia"
    ),

    # =========================
    # 🧪 DEBUG
    # =========================
    path("debug/gps/", debug_gps),
]