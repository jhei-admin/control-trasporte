from django.contrib import admin
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone
from django.utils.html import format_html
from .services import calcular_estado_sesion
from .models import (
    ComandoDispositivo,
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
        "unidad",
        "empresa",
        "estado_general",
        "estado_operacion_badge",
        "modo_kiosco",
        "conectividad",
        "bateria",
        "version_app",
        "ultimo_reporte",
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

    list_select_related = ("vehiculo", "vehiculo__empresa")
    list_per_page = 30
    actions = None
    readonly_fields = (
        "vehiculo",
        "resumen_ejecutivo",
        "estado_general",
        "estado_operacion_badge",
        "modo_kiosco",
        "conectividad",
        "bateria",
        "version_app",
        "ultimo_reporte",
        "kiosco_activo",
        "pantalla_fija_activa",
        "wifi_conectado",
        "wifi_ssid",
        "internet_disponible",
        "gps_activo",
        "bateria_porcentaje",
        "ip_local",
        "app_version",
        "app_version_code",
        "android_version",
        "device_model",
        "ultimo_reinicio_en",
        "creado_en",
        "reportado_en",
    )
    fieldsets = (
        (
            "Resumen ejecutivo",
            {
                "fields": (
                    "vehiculo",
                    "resumen_ejecutivo",
                    "estado_general",
                    "estado_operacion_badge",
                    "modo_kiosco",
                    "conectividad",
                    "bateria",
                    "version_app",
                    "ultimo_reporte",
                )
            },
        ),
        (
            "Conectividad y operacion",
            {
                "fields": (
                    ("kiosco_activo", "pantalla_fija_activa"),
                    ("wifi_conectado", "wifi_ssid"),
                    ("internet_disponible", "gps_activo"),
                    ("bateria_porcentaje", "ip_local"),
                )
            },
        ),
        (
            "Sistema",
            {
                "fields": (
                    ("app_version", "app_version_code"),
                    ("android_version", "device_model"),
                )
            },
        ),
        (
            "Tiempos",
            {
                "fields": (
                    "ultimo_reinicio_en",
                    "reportado_en",
                    "creado_en",
                )
            },
        ),
    )

    def get_queryset(self, request):
        limite_reporte = timezone.now() - timezone.timedelta(minutes=5)
        return (
            super()
            .get_queryset(request)
            .select_related("vehiculo", "vehiculo__empresa")
            .annotate(
                alerta_prioridad=Case(
                    When(reportado_en__lt=limite_reporte, then=Value(0)),
                    When(kiosco_activo=False, then=Value(1)),
                    When(wifi_conectado=False, then=Value(2)),
                    When(internet_disponible=False, then=Value(3)),
                    When(gps_activo=False, then=Value(4)),
                    default=Value(5),
                    output_field=IntegerField(),
                )
            )
            .order_by("alerta_prioridad", "-reportado_en")
        )

    def empresa(self, obj):
        return obj.vehiculo.empresa

    def unidad(self, obj):
        return f"Unidad {obj.vehiculo.codigo}"

    def estado_operacion(self, obj):
        sesion = obj.vehiculo.sesiones.filter(activa=True).order_by("-creada_en").first()
        return calcular_estado_sesion(sesion) if sesion else "SIN_SESION"

    def _badge(self, text, bg, fg="#ffffff"):
        return format_html(
            '<span style="display:inline-block;padding:4px 10px;border-radius:999px;'
            'background:{};color:{};font-weight:700;font-size:11px;letter-spacing:.3px;">{}</span>',
            bg,
            fg,
            text,
        )

    def _bool_badge(self, value, ok_text="OK", bad_text="NO"):
        return self._badge(ok_text, "#1f9d55") if value else self._badge(bad_text, "#d64545")

    def estado_operacion_badge(self, obj):
        estado = self.estado_operacion(obj)
        colors = {
            "EN_RUTA": ("EN RUTA", "#1f9d55"),
            "DETENIDO": ("DETENIDO", "#1d72b8"),
            "SIN_SENAL": ("SIN SENAL", "#d97706"),
            "SIN_GPS": ("SIN GPS", "#d97706"),
            "BLOQUEADO": ("BLOQUEADO", "#d64545"),
            "SIN_SESION": ("SIN SESION", "#6b7280"),
        }
        label, color = colors.get(estado, (estado, "#6b7280"))
        return self._badge(label, color)

    def estado_general(self, obj):
        minutos_sin_reporte = (timezone.now() - obj.reportado_en).total_seconds() / 60
        if minutos_sin_reporte > 5:
            return self._badge("SIN REPORTE", "#d64545")
        if not obj.kiosco_activo:
            return self._badge("SIN KIOSCO", "#d97706")
        if not obj.wifi_conectado:
            return self._badge("SIN WIFI", "#d97706")
        if not obj.internet_disponible:
            return self._badge("SIN INTERNET", "#d97706")
        if not obj.gps_activo:
            return self._badge("SIN GPS", "#d97706")
        return self._badge("OK", "#1f9d55")

    def modo_kiosco(self, obj):
        principal = self._bool_badge(obj.kiosco_activo, "KIOSCO", "NORMAL")
        secundario = self._bool_badge(obj.pantalla_fija_activa, "LOCK TASK", "SIN LOCK")
        return format_html("{} {}", principal, secundario)

    def conectividad(self, obj):
        wifi = self._bool_badge(obj.wifi_conectado, "WIFI", "SIN WIFI")
        net = self._bool_badge(obj.internet_disponible, "NET", "SIN NET")
        gps = self._bool_badge(obj.gps_activo, "GPS", "SIN GPS")
        ssid = format_html(
            '<div style="margin-top:4px;color:#4b5563;font-size:11px;">{}</div>',
            obj.wifi_ssid or "Sin SSID",
        )
        return format_html("{} {} {} {}", wifi, net, gps, ssid)

    def bateria(self, obj):
        if obj.bateria_porcentaje is None:
            return self._badge("SIN DATO", "#6b7280")
        color = "#1f9d55" if obj.bateria_porcentaje >= 50 else "#d97706" if obj.bateria_porcentaje >= 20 else "#d64545"
        return self._badge(f"{obj.bateria_porcentaje}%", color)

    def version_app(self, obj):
        version = obj.app_version or "Sin version"
        code = obj.app_version_code or "-"
        return format_html(
            '<strong>{}</strong><div style="color:#4b5563;font-size:11px;">code {}</div>',
            version,
            code,
        )

    def ultimo_reporte(self, obj):
        delta = timezone.now() - obj.reportado_en
        minutos = int(delta.total_seconds() // 60)
        if minutos <= 1:
            badge = self._badge("AHORA", "#1f9d55")
        elif minutos <= 5:
            badge = self._badge(f"{minutos} MIN", "#1d72b8")
        else:
            badge = self._badge(f"{minutos} MIN", "#d64545")
        return format_html(
            '{}<div style="color:#4b5563;font-size:11px;margin-top:4px;">{}</div>',
            badge,
            timezone.localtime(obj.reportado_en).strftime("%d/%m/%Y %H:%M:%S"),
        )

    def resumen_ejecutivo(self, obj):
        return format_html(
            "<div><strong>Unidad {}</strong> | {} | {} | bateria {}%</div>"
            "<div style='margin-top:6px;color:#4b5563;'>SSID: {} | IP: {} | Android {} | {}</div>",
            obj.vehiculo.codigo,
            self.estado_operacion(obj).replace("_", " "),
            "Kiosco activo" if obj.kiosco_activo else "Modo normal",
            obj.bateria_porcentaje if obj.bateria_porcentaje is not None else "-",
            obj.wifi_ssid or "Sin WiFi",
            obj.ip_local or "Sin IP",
            obj.android_version or "-",
            obj.device_model or "-",
        )

    def has_add_permission(self, request):
        return False

    empresa.short_description = "Empresa"
    unidad.short_description = "Unidad"
    estado_operacion_badge.short_description = "Estado app"
    estado_general.short_description = "Estado general"
    modo_kiosco.short_description = "Modo kiosco"
    conectividad.short_description = "Conectividad"
    bateria.short_description = "Bateria"
    version_app.short_description = "Version"
    ultimo_reporte.short_description = "Ultimo reporte"
    resumen_ejecutivo.short_description = "Ficha tecnica"


@admin.register(ComandoDispositivo)
class ComandoDispositivoAdmin(admin.ModelAdmin):
    list_display = (
        "unidad",
        "empresa",
        "tipo_badge",
        "estado_badge",
        "nota",
        "solicitado_en",
        "actualizado_en",
    )
    list_filter = ("tipo", "estado")
    search_fields = (
        "vehiculo__codigo",
        "vehiculo__placa",
        "vehiculo__empresa__nombre",
        "nota",
    )
    ordering = ("estado", "-solicitado_en")
    list_select_related = ("vehiculo", "vehiculo__empresa")
    readonly_fields = (
        "solicitado_en",
        "entregado_en",
        "aplicado_en",
        "actualizado_en",
        "detalle_error",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("vehiculo", "vehiculo__empresa")

    def unidad(self, obj):
        return f"Unidad {obj.vehiculo.codigo}"

    def empresa(self, obj):
        return obj.vehiculo.empresa

    def tipo_badge(self, obj):
        labels = {
            ComandoDispositivo.TIPO_FORZAR_SYNC: ("SYNC", "#1d72b8"),
            ComandoDispositivo.TIPO_REPORTAR_ESTADO: ("STATUS", "#1d72b8"),
            ComandoDispositivo.TIPO_REABRIR_APP: ("REABRIR", "#6b7280"),
            ComandoDispositivo.TIPO_ACTIVAR_KIOSCO: ("KIOSCO ON", "#1f9d55"),
            ComandoDispositivo.TIPO_SALIR_KIOSCO: ("KIOSCO OFF", "#d97706"),
            ComandoDispositivo.TIPO_ABRIR_WIFI_TECNICO: ("WIFI", "#7c3aed"),
        }
        text, color = labels.get(obj.tipo, (obj.tipo, "#6b7280"))
        return format_html(
            '<span style="display:inline-block;padding:4px 10px;border-radius:999px;background:{};color:#fff;font-weight:700;font-size:11px;">{}</span>',
            color,
            text,
        )

    def estado_badge(self, obj):
        colors = {
            ComandoDispositivo.ESTADO_PENDIENTE: ("PENDIENTE", "#d97706"),
            ComandoDispositivo.ESTADO_ENTREGADO: ("ENTREGADO", "#1d72b8"),
            ComandoDispositivo.ESTADO_APLICADO: ("APLICADO", "#1f9d55"),
            ComandoDispositivo.ESTADO_ERROR: ("ERROR", "#d64545"),
            ComandoDispositivo.ESTADO_CANCELADO: ("CANCELADO", "#6b7280"),
        }
        text, color = colors.get(obj.estado, (obj.estado, "#6b7280"))
        return format_html(
            '<span style="display:inline-block;padding:4px 10px;border-radius:999px;background:{};color:#fff;font-weight:700;font-size:11px;">{}</span>',
            color,
            text,
        )

    unidad.short_description = "Unidad"
    empresa.short_description = "Empresa"
    tipo_badge.short_description = "Comando"
    estado_badge.short_description = "Estado"


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
