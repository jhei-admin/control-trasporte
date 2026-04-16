from datetime import timedelta
import uuid

from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

# =========================
# 🔥 MANAGER MULTIEMPRESA (PRO)
# =========================
class EmpresaQuerySet(models.QuerySet):
    def for_empresa(self, empresa):
        if empresa is None:
            return self.none()
        return self.filter(empresa=empresa)


class EmpresaManager(models.Manager):
    def get_queryset(self):
        return EmpresaQuerySet(self.model, using=self._db)

    def for_empresa(self, empresa):
        return self.get_queryset().for_empresa(empresa)

# =========================
# EMPRESA
# =========================
class Empresa(models.Model):

    nombre = models.CharField(
        max_length=200,
        db_index=True  # 🔥 búsquedas por nombre (admin / filtros)
    )

    ruc = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        db_index=True  # 🔥 útil si luego validas o buscas por RUC
    )

    activa = models.BooleanField(
        default=True,
        db_index=True  # 🔥 consultas frecuentes (empresa activa)
    )

    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [

            # 🔥 opcional (si usas RUC para lookup)
            models.Index(fields=["ruc"]),
        ]

    def __str__(self):
        return self.nombre
    
# =========================
# VEHÍCULO
# =========================
class Vehiculo(models.Model):

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        db_index=True,
        related_name="vehiculos"
    )

    objects = EmpresaManager()

    codigo = models.CharField(
        max_length=10,
        help_text="Código visible del vehículo (01, 02, 15, etc.)"
    )

    placa = models.CharField(
        max_length=15,
        null=True,
        blank=True,
        help_text="Placa del vehículo"
    )

    activo = models.BooleanField(default=True)

    fecha_alta = models.DateField(auto_now_add=True)
    fecha_baja = models.DateField(null=True, blank=True)

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["empresa", "codigo"]),
            models.Index(fields=["empresa", "activo"]),
            models.Index(fields=["activo"]),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "codigo"],
                condition=Q(activo=True),
                name="unique_codigo_activo_por_empresa"
            )
        ]


    def clean(self):

        if self.activo:

            existe = Vehiculo.objects.filter(
                empresa=self.empresa,   # 🔴 ahora valida por empresa
                codigo=self.codigo,
                activo=True
            ).exclude(id=self.id).exists()

            if existe:
                raise ValidationError(
                    f"Ya existe un vehículo activo con el código {self.codigo} en esta empresa"
                )

    @property
    def numero(self):
        # Alias de compatibilidad
        return self.codigo

    def __str__(self):
        estado = "ACTIVO" if self.activo else "INACTIVO"

        if self.empresa:
            return f"{self.empresa} - Unidad {self.codigo} ({estado})"

        return f"Unidad {self.codigo} ({estado})"

# =========================
# RUTA
# =========================
class Ruta(models.Model):

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="rutas",
        db_index=True  # 🔥 joins rápidos
    )
    
    objects = EmpresaManager()

    nombre = models.CharField(max_length=100)
    geometria = models.JSONField(
        default=list,
        blank=True,
        help_text="Lista ordenada de coordenadas [lat, lng] para dibujar la ruta real",
    )

    class Meta:
        # 🔥 reemplazo de unique_together (moderno)
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "nombre"],
                name="unique_ruta_empresa_nombre"
            )
        ]

        indexes = [
            # 🔥 este es el más importante
            models.Index(fields=["empresa", "nombre"]),
        ]

    def __str__(self):
        return f"{self.empresa} - {self.nombre}"
    
# =========================
# CONFIGURACIÓN DESPACHO
# =========================
class ConfiguracionDespacho(models.Model):

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="configuraciones",
        db_index=True  # 🔥 importante para joins
    )
    
    objects = EmpresaManager()

    intervalo_fijo = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Intervalo fijo en minutos (vacío = automático)"
    )

    activa = models.BooleanField(
        default=True,
        db_index=True  # 🔥 filtro frecuente
    )

    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["empresa"],
                condition=Q(activa=True),
                name="una_config_activa_por_empresa"
            )
        ]
        indexes = [
            # 🔥 ESTE ES EL MÁS IMPORTANTE
            models.Index(fields=["empresa", "activa"]),
        ]

    def __str__(self):
        return (
            f"{self.empresa} - Fijo {self.intervalo_fijo} min"
            if self.intervalo_fijo
            else f"{self.empresa} - Automático"
        )

class RegistroSalidaQuerySet(models.QuerySet):
    def for_empresa(self, empresa):
        if empresa is None:
            return self.none()
        return self.filter(vehiculo__empresa=empresa)

class RegistroSalidaManager(models.Manager.from_queryset(RegistroSalidaQuerySet)):
    pass

class RegistroSalida(models.Model):

    vehiculo = models.ForeignKey(
        "Vehiculo",
        on_delete=models.CASCADE
    )

    ruta = models.ForeignKey(
        "Ruta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    objects = RegistroSalidaManager()
    # =========================
    # FECHAS Y HORAS
    # =========================
    fecha = models.DateField(default=timezone.localdate)
    hora_llegada = models.DateTimeField(default=timezone.now)

    hora_salida = models.DateTimeField(null=True, blank=True)
    hora_fija = models.DateTimeField(null=True, blank=True)
    hora_real_salida = models.DateTimeField(null=True, blank=True)

    # =========================
    # ESTADOS
    # =========================
    activo = models.BooleanField(default=True)
    en_cola = models.BooleanField(default=False)
    bloqueado = models.BooleanField(default=False)

    # =========================
    # COLA
    # =========================
    orden_cola = models.PositiveIntegerField(null=True, blank=True)
    intervalo_minutos = models.PositiveIntegerField(null=True, blank=True)

    # =========================
    # AUDITORÍA
    # =========================
    diferencia_minutos = models.IntegerField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    # =========================
    # REGLAS DE INTEGRIDAD
    # =========================
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["vehiculo", "fecha"],
                condition=Q(activo=True),
                name="una_salida_activa_por_vehiculo_y_dia"
            )
        ]
        ordering = ["fecha", "creado_en"]
        indexes = [
            models.Index(fields=["vehiculo", "fecha", "activo", "en_cola"]),
            models.Index(fields=["ruta", "fecha", "en_cola", "orden_cola"]),
            models.Index(fields=["en_cola", "orden_cola"]),
        ] 
    # =========================
    # VALIDACIONES
    # =========================
    def clean(self):

        if not self.en_cola and self.orden_cola is not None:
            raise ValidationError("Fuera de cola no puede tener orden.")

        if self.bloqueado and not self.hora_fija:
            raise ValidationError("Bloqueado requiere hora fija.")

        if (
            self.hora_fija
            and self.hora_salida
            and self.hora_fija != self.hora_salida
        ):
            raise ValidationError(
                "Hora fija y hora salida deben coincidir."
            )

        if self.en_cola and not self.ruta:
            raise ValidationError(
                "No se puede poner en cola sin ruta."
            )
        
        if self.ruta and self.vehiculo:
            if self.ruta.empresa != self.vehiculo.empresa:
                raise ValidationError(
                    "La ruta no pertenece a la misma empresa del vehículo"
                )

    # =========================
    # SAVE CENTRAL
    # =========================
    def save(self, *args, **kwargs):

        if not self.en_cola:
            self.orden_cola = None

        if self.bloqueado and self.hora_fija:
            self.hora_salida = self.hora_fija

        if not self.bloqueado:
            self.hora_fija = None

        super().save(*args, **kwargs)

    # =========================
    # FLUJO OPERATIVO
    # =========================
    def iniciar_salida(self):
        if self.hora_real_salida:
            return

        self.hora_real_salida = timezone.now()
        self.en_cola = False
        self.activo = True

        self.save(update_fields=[
            "hora_real_salida",
            "en_cola",
            "activo"
        ])

    def finalizar_salida(self):
        self.activo = False
        self.en_cola = False

        self.save(update_fields=[
            "activo",
            "en_cola"
        ])

    # =========================
    # 🔥 PUNTO CORRECTO (FIX CLAVE)
    # =========================
    def siguiente_marcacion(self):
        """
        Devuelve la siguiente marcación pendiente.
        NO usa hora.
        NO depende de existencia.
        SOLO estado real.
        """

        from django.apps import apps
        MarcacionPunto = apps.get_model(
            "flota_app",
            "MarcacionPunto"
        )

        return (
            MarcacionPunto.objects
            .filter(
                registro_salida=self,
                hora_marcada__isnull=True
            )
            .select_related("punto", "registro_salida")
            .order_by("punto__orden")
            .first()
        )

    # =========================
    # UTILIDAD
    # =========================
    @property
    def modo(self):
        return "MANUAL" if self.bloqueado else "AUTOMÁTICO"

    def __str__(self):
        ruta = self.ruta.nombre if self.ruta else "SIN RUTA"
        return f"{self.vehiculo} - {ruta} ({self.fecha})"

class PuntoControlQuerySet(models.QuerySet):
    def for_empresa(self, empresa):
        if empresa is None:
            return self.none()
        return self.filter(ruta__empresa=empresa)


class PuntoControlManager(models.Manager.from_queryset(PuntoControlQuerySet)):
    pass
# =========================
# PUNTO DE CONTROL
# =========================
class PuntoControl(models.Model):

    ruta = models.ForeignKey(
        Ruta,
        on_delete=models.CASCADE,
        related_name="puntos_control"
    )

    codigo = models.CharField(max_length=10)
    nombre = models.CharField(max_length=100)

    latitud = models.DecimalField(max_digits=9, decimal_places=6)
    longitud = models.DecimalField(max_digits=9, decimal_places=6)

    radio_metros = models.PositiveIntegerField(default=50)
    orden = models.PositiveIntegerField()

    offset_minutos = models.PositiveIntegerField(default=0)
    requiere_marcacion = models.BooleanField(
        default=True,
        help_text="Desactive para usar este punto solo como referencia visual en el mapa.",
    )
    activo = models.BooleanField(default=True)

    class Meta:
        constraints = [
           models.UniqueConstraint(
               fields=["ruta", "orden"],
               name="unique_ruta_orden"
            )
        ]
        indexes = [
            models.Index(fields=["ruta", "activo"]),
            models.Index(fields=["ruta", "orden"]),
    ]
        ordering = ["orden"]

    def __str__(self):
        return f"{self.ruta} | {self.orden}. {self.codigo}"

    objects = PuntoControlManager()

# =========================
# MARCACIÓN DE PUNTO
# =========================
class MarcacionPunto(models.Model):

    registro_salida = models.ForeignKey(
        RegistroSalida,
        on_delete=models.CASCADE,
        related_name="marcaciones"
    )

    punto = models.ForeignKey(PuntoControl, on_delete=models.CASCADE)

    # ⚠️ Cache / auditoría (NO fuente de verdad)
    hora_programada = models.DateTimeField(null=True, blank=True)
    hora_marcada = models.DateTimeField(null=True, blank=True)

    diferencia_minutos = models.IntegerField(null=True, blank=True)

    estado = models.CharField(
        max_length=20,
        choices=[
            ("adelantado", "Adelantado"),
            ("a_tiempo", "A tiempo"),
            ("tarde", "Tarde"),
        ],
        null=True,
        blank=True
    )

    # 🔊 SOLO PARA ADELANTADO / TARDE
    audio_flag = models.CharField(
        max_length=50,
        null=True,
        blank=True
    )

    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["registro_salida", "hora_marcada"]),
            models.Index(fields=["punto"]),
        ]
        unique_together = ("registro_salida", "punto")
        ordering = ["punto__orden"]

    # -------------------------------------------------
    # ⏱ HORA PROGRAMADA = SALIDA + OFFSET
    # -------------------------------------------------
    def calcular_hora_programada(self):
        if not self.registro_salida or not self.registro_salida.hora_salida:
            return None

        return (
            self.registro_salida.hora_salida +
            timedelta(minutes=self.punto.offset_minutos)
        )

    # -------------------------------------------------
    # 🧠 EVALUAR ESTADO (REGLA ÚNICA)
    # -------------------------------------------------
    def evaluar_estado(self, tolerancia_min=2):
        if not self.hora_marcada or not self.hora_programada:
            return

        diff = int(
            (self.hora_marcada - self.hora_programada)
            .total_seconds() / 60
        )

        self.diferencia_minutos = diff

        if diff < -tolerancia_min:
            self.estado = "adelantado"
            self.audio_flag = "audio_adelantado"

        elif diff > tolerancia_min:
            self.estado = "tarde"
            self.audio_flag = "audio_tarde"

        else:
            self.estado = "a_tiempo"
            self.audio_flag = None

    # -------------------------------------------------
    # ✅ MARCAR PUNTO (FUENTE ÚNICA DE VERDAD)
    # -------------------------------------------------
    def marcar(self, hora=None):
        if self.hora_marcada:
            return

        self.hora_marcada = hora or timezone.now()

        # 🔥 SIEMPRE recalcular desde la salida actual
        self.hora_programada = self.calcular_hora_programada()

        self.evaluar_estado()
        self.save()

    # -------------------------------------------------
    # 💾 SAVE BLINDADO (ANTI-INCOHERENCIAS)
    # -------------------------------------------------
    def save(self, *args, **kwargs):

        # 🔑 Mantener sincronizada la hora programada
        if self.registro_salida and self.registro_salida.hora_salida:
            self.hora_programada = self.calcular_hora_programada()

        if self.hora_marcada and not self.estado:
            self.evaluar_estado()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.registro_salida} - {self.punto.codigo}"

# =========================
# SESIÓN DE UNIDAD
# =========================
class SesionUnidad(models.Model):

    vehiculo = models.ForeignKey(
        Vehiculo,
        on_delete=models.CASCADE,
        related_name="sesiones",
        db_index=True  # 🔥 mejora joins y filtros
    )

    salida = models.ForeignKey(
        RegistroSalida,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sesiones",
        db_index=True  # 🔥 consultas por salida
    )

    token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True  # 🔥 lookup por token (API)
    )

    activa = models.BooleanField(default=True, db_index=True)

    creada_en = models.DateTimeField(auto_now_add=True)
    expira_en = models.DateTimeField(null=True, blank=True)

    last_heartbeat = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True  # 🔥 monitoreo de conexión
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["vehiculo"],
                condition=Q(activa=True),
                name="una_sesion_activa_por_vehiculo",
            )
        ]
        indexes = [
            models.Index(fields=["vehiculo", "activa"]),
            models.Index(fields=["token", "activa"]),
            models.Index(fields=["expira_en"]),
            models.Index(fields=["last_heartbeat"]),
        ]

    def esta_valida(self):
        if not self.activa:
            return False
        if self.expira_en is None:
            return True
        return timezone.now() < self.expira_en

    def __str__(self):
        return f"Unidad {self.vehiculo.codigo} | {'ACTIVA' if self.activa else 'INACTIVA'}"


class SesionStaffApp(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="sesiones_staff_app",
        db_index=True,
    )

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="sesiones_staff_app",
        db_index=True,
    )

    token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )

    activa = models.BooleanField(default=True, db_index=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    expira_en = models.DateTimeField(null=True, blank=True)
    ultimo_acceso = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["user", "activa"]),
            models.Index(fields=["empresa", "activa"]),
            models.Index(fields=["token", "activa"]),
            models.Index(fields=["expira_en"]),
            models.Index(fields=["ultimo_acceso"]),
        ]

    def esta_valida(self):
        if not self.activa:
            return False
        if self.expira_en is None:
            return True
        return timezone.now() < self.expira_en

    def __str__(self):
        return f"{self.user.username} | {self.empresa} | {'ACTIVA' if self.activa else 'INACTIVA'}"

# =========================
# GPS HISTÓRICO
# =========================
class MovimientoCajaQuerySet(models.QuerySet):
    def for_empresa(self, empresa):
        if empresa is None:
            return self.none()
        return self.filter(empresa=empresa)


class MovimientoCajaManager(models.Manager.from_queryset(MovimientoCajaQuerySet)):
    pass


class MovimientoCaja(models.Model):
    TIPO_INGRESO = "ingreso"
    TIPO_GASTO = "gasto"
    TIPOS = [
        (TIPO_INGRESO, "Ingreso"),
        (TIPO_GASTO, "Gasto"),
    ]

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="movimientos_caja",
        db_index=True,
    )
    vehiculo = models.ForeignKey(
        Vehiculo,
        on_delete=models.CASCADE,
        related_name="movimientos_caja",
        db_index=True,
    )
    sesion = models.ForeignKey(
        SesionUnidad,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos_caja",
        db_index=True,
    )
    salida = models.ForeignKey(
        RegistroSalida,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos_caja",
        db_index=True,
    )
    tipo = models.CharField(max_length=10, choices=TIPOS, db_index=True)
    categoria = models.CharField(max_length=60, default="Otros", db_index=True)
    nota = models.CharField(max_length=255, blank=True, default="")
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_operacion = models.DateField(default=timezone.localdate, db_index=True)
    creado_en = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = MovimientoCajaManager()

    class Meta:
        ordering = ["-fecha_operacion", "-creado_en", "-id"]
        indexes = [
            models.Index(fields=["empresa", "vehiculo", "fecha_operacion"]),
            models.Index(fields=["vehiculo", "tipo", "fecha_operacion"]),
            models.Index(fields=["sesion", "fecha_operacion"]),
        ]

    def __str__(self):
        return f"{self.vehiculo.codigo} | {self.tipo.upper()} | {self.monto}"


class GPSRegistro(models.Model):

    sesion = models.ForeignKey(
        SesionUnidad,
        on_delete=models.CASCADE,
        related_name="gps_registros",
        db_index=True  # 🔥 explícito (alto tráfico)
    )

    lat = models.FloatField()
    lng = models.FloatField()

    velocidad = models.FloatField(null=True, blank=True)
    precision = models.FloatField(null=True, blank=True)
    bateria = models.IntegerField(null=True, blank=True)

    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_index=True  # 🔥 queries por tiempo
    )

    class Meta:
        ordering = ["-timestamp"]

        indexes = [
            # 🔥 CRÍTICO: últimas ubicaciones por sesión (uso real)
            models.Index(fields=["sesion", "-timestamp"]),

            # 🔥 consultas globales por tiempo (reportes / limpieza)
            models.Index(fields=["-timestamp"]),
        ]

    def __str__(self):
        return f"{self.sesion_id} @ {self.timestamp}"

class UbicacionVehiculoQuerySet(models.QuerySet):
    def for_empresa(self, empresa):
        if empresa is None:
            return self.none()
        return self.filter(vehiculo__empresa=empresa)


class UbicacionVehiculoManager(models.Manager.from_queryset(UbicacionVehiculoQuerySet)):
    pass

# =========================
# UBICACIÓN ACTUAL (MAPA)
# =========================
class UbicacionVehiculo(models.Model):

    objects = UbicacionVehiculoManager()

    vehiculo = models.OneToOneField(
        Vehiculo,
        on_delete=models.CASCADE,
        related_name="ubicacion_actual"
    )

    latitud = models.FloatField()
    longitud = models.FloatField()

    velocidad = models.FloatField(null=True, blank=True)
    precision = models.FloatField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.vehiculo.codigo} @ {self.latitud},{self.longitud}"
    
    class Meta:
        indexes = [
            models.Index(fields=["updated_at"]),
            models.Index(fields=["vehiculo", "updated_at"])
        ]

class ParadaQuerySet(models.QuerySet):
    def for_empresa(self, empresa):
        if empresa is None:
            return self.none()
        return self.filter(vehiculo__empresa=empresa)


class ParadaManager(models.Manager.from_queryset(ParadaQuerySet)):
    pass

# =========================
# PARADAS
# =========================
class Parada(models.Model):
    objects = ParadaManager()
    vehiculo = models.ForeignKey(
        Vehiculo,
        on_delete=models.CASCADE,
        related_name="paradas"
    )

    lat = models.FloatField()
    lng = models.FloatField()

    inicio = models.DateTimeField()
    fin = models.DateTimeField(null=True, blank=True)

    duracion_segundos = models.PositiveIntegerField(default=0)
    activa = models.BooleanField(default=True)

    es_prolongada = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def cerrar(self, fin):
        self.fin = fin
        self.duracion_segundos = int((fin - self.inicio).total_seconds())
        self.activa = False
        self.save()

    def __str__(self):
        estado = "PROLONGADA" if self.es_prolongada else "NORMAL"
        return f"{self.vehiculo} | {estado}"
    
    class Meta:
        indexes = [
            models.Index(fields=["vehiculo", "inicio"]),
            models.Index(fields=["vehiculo", "activa"]),
            models.Index(fields=["activa"]),
    ]
# =========================
# 📢 MENSAJES GLOBALES (APP CONDUCTOR)
# =========================
class MensajeGlobal(models.Model):
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mensajes_globales",
    )

    texto = models.TextField(
        help_text="Mensaje mostrado en la app"
    )

    activo = models.BooleanField(default=True)

    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()

    creado_en = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Última modificación del mensaje"
    )

    class Meta:
        indexes = [
            models.Index(fields=["empresa", "activo", "fecha_inicio", "fecha_fin"]),
            models.Index(fields=["activo", "fecha_inicio", "fecha_fin"]),
            models.Index(fields=["activo"]),
        ]
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        scope = self.empresa.nombre if self.empresa else "GLOBAL"
        return f"{scope}: {self.texto[:50]} ({self.fecha_inicio} -> {self.fecha_fin})"

# =========================
# PERFIL USUARIO EMPRESA
# =========================
class PerfilUsuario(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="perfil"
    )

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    def __str__(self):
        return f"{self.user.username} - {self.empresa}"


@receiver(post_save, sender=User)
def crear_perfil_usuario(sender, instance, created, **kwargs):
    if created:
        PerfilUsuario.objects.create(user=instance)

@receiver(post_save, sender=User)
def guardar_perfil_usuario(sender, instance, **kwargs):
    if hasattr(instance, "perfil"):
        instance.perfil.save()

