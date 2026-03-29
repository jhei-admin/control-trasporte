from django.urls import path

# =========================
# 📡 API endpoints (JSON)
# =========================
from .api.gps_api import api_gps
from .api.conductor_api import (
    api_gps_conductor,
    api_heartbeat,
    api_app_estado,
    api_escanear_qr,
)
from .api.despacho_api import (
    api_despachador_mapa,
    api_puntos_control,
    api_buscar_vehiculo_por_codigo,
    api_recorrido_vehiculo,
    api_paradas_vehiculo,
    api_panel_frecuencia,
    debug_gps,
)

# =========================
# 🖥️ HTML Views (thin views)
# =========================
from .views.despacho_views import (
    LoginSistemaView,
    panel_despachador,
    buscar_unidad_panel,
    poner_en_cola,
    quitar_de_cola,
    asignar_hora_fija,
    desbloquear_hora,
    auditoria_horas,
    historial_salidas,
    historial_vehiculo,
    control_ruta,
    marcar_paso,
    marcar_siguiente_punto,
    panel_frecuencia,
    detalle_salida,
)

from .views.reportes_views import reporte_salidas_diarias

# =========================
# 🚍 URL Patterns
# =========================
urlpatterns = [

    # =========================
    # 📡 APIs APP CONDUCTOR
    # =========================
    path("api/gps/conductor/", api_gps_conductor, name="api_gps_conductor"),
    path("api/app/heartbeat/", api_heartbeat, name="api_heartbeat"),
    path("api/app/estado/", api_app_estado, name="api_app_estado"),
    path("api/app/escanear-qr/", api_escanear_qr, name="api_escanear_qr"),
    path("api/app/gps/", api_gps_conductor, name="api_gps_conductor"),

    # =========================
    # 🗺️ API GPS — MAPA
    # =========================
    path("api/gps/", api_gps, name="api_gps"),
    path("api/despachador/mapa/", api_despachador_mapa, name="api_despachador_mapa"),

    # =========================
    # 🔴 API PUNTOS DE CONTROL
    # =========================
    path("api/despachador/puntos-control/", api_puntos_control, name="api_puntos_control"),

    # =========================
    # 🔎 API BÚSQUEDA VEHÍCULO
    # =========================
    path("api/despachador/buscar-vehiculo/", api_buscar_vehiculo_por_codigo, name="api_buscar_vehiculo_por_codigo"),

    # =========================
    # 🧭 API RECORRIDO
    # =========================
    path("api/despachador/recorrido/", api_recorrido_vehiculo, name="api_recorrido_vehiculo"),

    # =========================
    # 🛑 API PARADAS
    # =========================
    path("api/despachador/paradas/", api_paradas_vehiculo, name="api_paradas_vehiculo"),

    # =========================
    # 🟡 PANEL DESPACHADOR
    # =========================
    path("login/", LoginSistemaView.as_view(), name="login"),
    path("sistema/despachador/", panel_despachador, name="panel_despachador"),
    path("despachador/buscar-unidad/", buscar_unidad_panel, name="buscar_unidad_panel"),
    path("despachador/poner-en-cola/<int:salida_id>/", poner_en_cola, name="poner_en_cola"),
    path("despachador/quitar-de-cola/<int:salida_id>/", quitar_de_cola, name="quitar_de_cola"),
    path("despachador/asignar-hora-fija/<int:salida_id>/", asignar_hora_fija, name="asignar_hora_fija"),
    path("despachador/desbloquear-hora/<int:salida_id>/", desbloquear_hora, name="desbloquear_hora"),

    # =========================
    # 📋 HISTORIAL
    # =========================
    path("auditoria-horas/", auditoria_horas, name="auditoria_horas"),
    path("historial-salidas/", historial_salidas, name="historial_salidas"),
    path("despachador/historial/<int:vehiculo_id>/", historial_vehiculo, name="historial_vehiculo"),

    # =========================
    # 📍 DETALLE SALIDA
    # =========================
    path("salida/<int:salida_id>/detalle/", detalle_salida, name="detalle_salida"),

    # =========================
    # 🚦 CONTROL DE RUTA
    # =========================
    path("control-ruta/<int:salida_id>/", control_ruta, name="control_ruta"),
    path("control-ruta/<int:salida_id>/marcar/<int:punto_id>/", marcar_paso, name="marcar_paso"),
    path("control-ruta/<int:salida_id>/marcar-siguiente/", marcar_siguiente_punto, name="marcar_siguiente_punto"),

    # =========================
    # 📦 QR VEHÍCULO
    # =========================

    # =========================
    # 🟡 REPORTES
    # =========================
    path("reportes/salidas/<int:vehiculo_id>/", reporte_salidas_diarias, name="reporte_salidas_diarias"),

    # =========================
    # 🟢 FRECUENCIA
    # =========================
    path("despachador/frecuencia/", panel_frecuencia, name="panel_frecuencia"),
    path("api/frecuencia/", api_panel_frecuencia, name="api_panel_frecuencia"),

    # =========================
    # 🧪 DEBUG
    # =========================
    path("debug/gps/", debug_gps),
]