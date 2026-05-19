
from django.urls import path

from .view_modules.api_views import (
    api_admin_limpiar_gps,
    api_admin_provisioning_qr,
    api_admin_update_apk,
    api_app_cola_contexto,
    api_app_command_ack,
    api_app_command_pull,
    api_app_control_marcar,
    api_app_control_ruta,
    api_app_device_status,
    api_app_ganancias,
    api_app_ganancias_movimiento,
    api_app_mensajes,
    api_app_update_apk,
    api_app_version,
    api_app_gerencia_login,
    api_app_gerencia_mapa,
    api_app_mapa_operativo,
    api_app_estado,
    api_app_referencia_tiempo,
    api_buscar_vehiculo_por_codigo,
    api_despachador_mapa,
    api_escanear_qr,
    api_gps,
    api_gps_conductor,
    api_heartbeat,
    api_historial_salidas,
    api_control_ruta_web,
    api_detalle_salida_web,
    api_panel_despachador,
    api_panel_frecuencia,
    api_paradas_vehiculo,
    api_puntos_control,
    api_reporte_salidas_diarias,
    api_recorrido_vehiculo,
    debug_gps,
)
from .view_modules.auth_views import LoginSistemaView
from .view_modules.despacho_views import (
    asignar_hora_fija,
    auditoria_horas,
    buscar_unidad_panel,
    cambiar_intervalo_global,
    control_ruta,
    despachador_mapa,
    detalle_salida,
    historial_salidas,
    historial_vehiculo,
    marcar_paso,
    marcar_siguiente_punto,
    panel_despachador,
    panel_frecuencia,
    poner_en_cola,
    quitar_de_cola,
    recorrido_vehiculo,
    ver_qr_unidad,
    desbloquear_hora,
)
from .view_modules.reportes_views import reporte_salidas_diarias

urlpatterns = [

    path(
        "api/admin/limpiar-gps/",
        api_admin_limpiar_gps,
        name="api_admin_limpiar_gps"
    ),
    path(
        "api/admin/limpiar-gps",
        api_admin_limpiar_gps
    ),
    path(
        "api/admin/provisioning-qr/",
        api_admin_provisioning_qr,
        name="api_admin_provisioning_qr"
    ),
    path(
        "api/admin/provisioning-qr",
        api_admin_provisioning_qr
    ),
    path(
        "api/admin/update-apk/",
        api_admin_update_apk,
        name="api_admin_update_apk"
    ),
    path(
        "api/admin/update-apk",
        api_admin_update_apk
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
        "api/app/version/",
        api_app_version,
        name="api_app_version"
    ),
    path(
        "api/app/version",
        api_app_version
    ),
    path(
        "api/app/update-apk/",
        api_app_update_apk,
        name="api_app_update_apk"
    ),
    path(
        "api/app/update-apk",
        api_app_update_apk
    ),

    path(
        "api/app/referencia-tiempo/",
        api_app_referencia_tiempo,
        name="api_app_referencia_tiempo"
    ),

    # 🆕 CONTEXTO DE COLA (GPS IDEOVAL)
    path(
        "api/app/control-ruta/",
        api_app_control_ruta,
        name="api_app_control_ruta"
    ),
    path(
        "api/app/control-ruta",
        api_app_control_ruta
    ),
    path(
        "api/app/control-ruta/marcar/",
        api_app_control_marcar,
        name="api_app_control_marcar"
    ),
    path(
        "api/app/control-ruta/marcar",
        api_app_control_marcar
    ),

    path(
        "api/app/ganancias/",
        api_app_ganancias,
        name="api_app_ganancias"
    ),
    path(
        "api/app/ganancias",
        api_app_ganancias
    ),
    path(
        "api/app/ganancias/movimiento/",
        api_app_ganancias_movimiento,
        name="api_app_ganancias_movimiento"
    ),
    path(
        "api/app/ganancias/movimiento",
        api_app_ganancias_movimiento
    ),

    path(
        "api/app/mensajes/",
        api_app_mensajes,
        name="api_app_mensajes"
    ),
    path(
        "api/app/mensajes",
        api_app_mensajes
    ),

    path(
        "api/app/cola-contexto/",
        api_app_cola_contexto,
        name="api_app_cola_contexto"    
    ),
    path(
        "api/app/cola-contexto",
        api_app_cola_contexto
    ),

    path(
        "api/app/gerencia/login/",
        api_app_gerencia_login,
        name="api_app_gerencia_login"
    ),
    path(
        "api/app/gerencia/login",
        api_app_gerencia_login
    ),

    path(
        "api/app/gerencia/mapa/",
        api_app_gerencia_mapa,
        name="api_app_gerencia_mapa"
    ),
    path(
        "api/app/gerencia/mapa",
        api_app_gerencia_mapa
    ),

    path(
        "api/app/mapa-operativo/",
        api_app_mapa_operativo,
        name="api_app_mapa_operativo"
    ),
    path(
        "api/app/mapa-operativo",
        api_app_mapa_operativo
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

    path(
        "api/app/device-status/",
        api_app_device_status,
        name="api_app_device_status"
    ),
    path(
        "api/app/device-status",
        api_app_device_status
    ),

    path(
        "api/app/device-command/pull/",
        api_app_command_pull,
        name="api_app_command_pull"
    ),
    path(
        "api/app/device-command/pull",
        api_app_command_pull
    ),
    path(
        "api/app/device-command/ack/",
        api_app_command_ack,
        name="api_app_command_ack"
    ),
    path(
        "api/app/device-command/ack",
        api_app_command_ack
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
    path(
        "api/despachador/panel/",
        api_panel_despachador,
        name="api_panel_despachador"
    ),
    path(
        "api/despachador/panel",
        api_panel_despachador
    ),
    path(
        "api/despachador/historial/",
        api_historial_salidas,
        name="api_historial_salidas"
    ),
    path(
        "api/despachador/historial",
        api_historial_salidas
    ),
    path(
        "api/despachador/control-ruta/<int:salida_id>/",
        api_control_ruta_web,
        name="api_control_ruta_web"
    ),
    path(
        "api/despachador/detalle-salida/<int:salida_id>/",
        api_detalle_salida_web,
        name="api_detalle_salida_web"
    ),
    path(
        "api/reportes/salidas/<int:vehiculo_id>/",
        api_reporte_salidas_diarias,
        name="api_reporte_salidas_diarias"
    ),

    # =========================
    # 🧪 DEBUG
    # =========================
    path("debug/gps/", debug_gps),
]
