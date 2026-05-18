from django.contrib import admin
from .services import calcular_estado_sesion
from .models import (
    EstadoDispositivo,
    Vehiculo,
    Ruta,
    RegistroSalida,
    ConfiguracionDespacho,
    PuntoControl,
    MarcacionPunto,
    SesionUnidad,
    GPSRegistro,
    UbicacionVehiculo,
    MensajeGlobal,
    Empresa,   # 👈 NUEVO
    PerfilUsuario,
)

# =================================================
# EMPRESA
# =================================================
@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("id", "nombre", "ruc", "activa", "creado_en")
    search_fields = ("nombre", "ruc")

# =================================================
# VEHÍCULO
# =================================================
@admin.register(Vehiculo)
class VehiculoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "codigo",
        "placa",
        "activo",
        "fecha_alta",
        "fecha_baja",
    )

    list_filter = ("activo",)

    search_fields = (
        "codigo",
        "placa",
    )

    ordering = ("codigo",)


# =================================================
# RUTA
# =================================================
@admin.register(Ruta)
class RutaAdmin(admin.ModelAdmin):
    list_display = ("id", "empresa", "nombre", "tiene_geometria")
    search_fields = ("nombre", "empresa__nombre")
    ordering = ("nombre",)
    readonly_fields = ("tiene_geometria",)

    def tiene_geometria(self, obj):
        return bool(obj.geometria)

    tiene_geometria.boolean = True
    tiene_geometria.short_description = "Geometria"


# =================================================
# REGISTRO DE SALIDA
# =================================================
@admin.register(RegistroSalida)
class RegistroSalidaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "vehiculo",
        "ruta",
        "fecha",
        "hora_llegada",
        "hora_salida",
        "intervalo_minutos",
        "en_cola",
        "bloqueado",
    )

    list_filter = (
        "ruta",
        "en_cola",
        "bloqueado",
        "fecha",
    )

    search_fields = (
        "vehiculo__numero",
    )

    ordering = ("hora_llegada",)

    readonly_fields = (
        "creado_en",
    )


# =================================================
# CONFIGURACIÓN DE DESPACHO
# =================================================
@admin.register(ConfiguracionDespacho)
class ConfiguracionDespachoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "intervalo_fijo",
        "activa",
        "creado_en",
    )

    list_filter = ("activa",)
    ordering = ("-creado_en",)


# =================================================
# PUNTO DE CONTROL
# =================================================
@admin.register(PuntoControl)
class PuntoControlAdmin(admin.ModelAdmin):
    list_display = (
        "codigo",
        "orden",
        "fase",
        "nombre",
        "requiere_marcacion",
        "confirma_avance",
        "es_contexto_interno",
        "offset_minutos",
        "radio_metros",
        "activo",
    )

    list_display_links = ("codigo",)

    list_editable = (
        "orden",
        "fase",
        "requiere_marcacion",
        "confirma_avance",
        "es_contexto_interno",
        "offset_minutos",
        "radio_metros",
        "activo",
    )

    list_filter = ("ruta", "fase", "requiere_marcacion", "confirma_avance", "activo", "es_contexto_interno")
    search_fields = ("codigo", "nombre")
    ordering = ("ruta", "orden")


# =================================================
# MARCACIÓN DE PUNTOS
# =================================================
@admin.register(MarcacionPunto)
class MarcacionPuntoAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "registro_salida",
        "punto",
        "hora_programada",
        "hora_marcada",
        "diferencia_minutos",
        "estado",
    )

    list_filter = (
        "estado",
        "punto",
    )

    search_fields = (
        "registro_salida__vehiculo__numero",
        "punto__codigo",
    )

    ordering = (
        "registro_salida",
        "punto__orden",
    )

    readonly_fields = (
        "hora_programada",
        "hora_marcada",
        "diferencia_minutos",
        "estado",
        "creado_en",
    )


# =================================================
# 🔐 SESIÓN ACTIVA POR UNIDAD
# =================================================
@admin.register(SesionUnidad)
class SesionUnidadAdmin(admin.ModelAdmin):
    list_display = (
        "vehiculo",
        "activa",
        "creada_en",
        "expira_en",
        "last_heartbeat",
        "token",
    )

    list_filter = ("activa",)

    readonly_fields = (
        "token",
        "creada_en",
        "expira_en",
        "last_heartbeat",
    )


# =================================================
# 📜 GPS HISTÓRICO (AUDITORÍA)
# =================================================
@admin.register(GPSRegistro)
class GPSRegistroAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "sesion",
        "lat",
        "lng",
        "velocidad",
        "precision",
        "bateria",
        "timestamp",
    )

    list_filter = ("sesion",)
    search_fields = ("sesion__vehiculo__numero",)
    ordering = ("-timestamp",)

    readonly_fields = ("timestamp",)


# =================================================
# 📍 UBICACIÓN ACTUAL (TIEMPO REAL)
# =================================================
@admin.register(UbicacionVehiculo)
class UbicacionVehiculoAdmin(admin.ModelAdmin):
    list_display = (
        "vehiculo",
        "latitud",
        "longitud",
        "velocidad",
        "updated_at",
    )

    search_fields = (
        "vehiculo__numero",
    )

    ordering = ("-updated_at",)

    readonly_fields = ("updated_at",)


# =================================================
# 📢 MENSAJES GLOBALES (APP CONDUCTOR)
# =================================================
@admin.register(EstadoDispositivo)
class EstadoDispositivoAdmin(admin.ModelAdmin):
    list_display = (
        "vehiculo",
        "empresa",
        "estado_operacion",
        "kiosco_activo",
        "wifi_conectado",
        "wifi_ssid",
        "internet_disponible",
        "gps_activo",
        "bateria_porcentaje",
        "app_version",
        "reportado_en",
    )

    list_filter = (
        "kiosco_activo",
        "wifi_conectado",
        "internet_disponible",
        "gps_activo",
    )

    search_fields = (
        "vehiculo__codigo",
        "vehiculo__placa",
        "vehiculo__empresa__nombre",
        "wifi_ssid",
        "device_model",
        "android_version",
    )

    ordering = ("-reportado_en",)

    readonly_fields = (
        "creado_en",
        "reportado_en",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("vehiculo", "vehiculo__empresa")

    def empresa(self, obj):
        return obj.vehiculo.empresa

    def estado_operacion(self, obj):
        sesion = obj.vehiculo.sesiones.filter(activa=True).order_by("-creada_en").first()
        return calcular_estado_sesion(sesion) if sesion else "SIN_SESION"

    empresa.short_description = "Empresa"
    estado_operacion.short_description = "Estado app"


@admin.register(MensajeGlobal)
class MensajeGlobalAdmin(admin.ModelAdmin):
    list_display = (
        "empresa",
        "vehiculo",
        "texto",
        "activo",
        "fecha_inicio",
        "fecha_fin",
        "creado_en",
    )

    list_filter = ("empresa", "vehiculo", "activo")

    search_fields = ("texto", "empresa__nombre", "vehiculo__codigo", "vehiculo__placa")

    ordering = ("-fecha_inicio",)


admin.site.register(PerfilUsuario)
