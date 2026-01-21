from datetime import timedelta
from django.db import models
from django.utils import timezone


from django.db import models
from django.core.exceptions import ValidationError

# =========================
# VEHÍCULO
# =========================
class Vehiculo(models.Model):
    # Código operativo (01, 02, 15, 25...)
    codigo = models.CharField(
        max_length=10,
        help_text="Código visible del vehículo (puede reutilizarse)"
    )

    # 🔴 PLACA (TEMPORALMENTE SIN UNIQUE)
    # Se hace así SOLO para migrar datos antiguos
    placa = models.CharField(
        max_length=15,
        null=True,
        blank=True,
        help_text="Placa del vehículo (temporalmente permite vacío)"
    )

    # Estado del vehículo
    activo = models.BooleanField(
        default=True,
        help_text="Indica si el vehículo está activo en operación"
    )

    # Fechas administrativas
    fecha_alta = models.DateField(
        auto_now_add=True
    )

    fecha_baja = models.DateField(
        null=True,
        blank=True
    )

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['codigo', 'activo']),
        ]

    def clean(self):
        # Regla: no puede haber dos vehículos ACTIVOS con el mismo código
        if self.activo:
            existe = Vehiculo.objects.filter(
                codigo=self.codigo,
                activo=True
            ).exclude(id=self.id).exists()

            if existe:
                raise ValidationError(
                    f"Ya existe un vehículo activo con el código {self.codigo}"
                )

    # =========================
    # 🔁 ALIAS DE COMPATIBILIDAD
    # =========================
    @property
    def numero(self):
        """
        Alias temporal para compatibilidad con código antiguo.
        TODO: eliminar cuando todo el sistema use 'codigo'.
        """
        return self.codigo

    def __str__(self):
        estado = "ACTIVO" if self.activo else "INACTIVO"
        return f"Unidad {self.codigo} - {self.placa or 'SIN PLACA'} ({estado})"

# =========================
# RUTA
# =========================
class Ruta(models.Model):
    nombre = models.CharField(max_length=100)

    def __str__(self):
        return self.nombre


# =========================
# CONFIGURACIÓN DESPACHO
# =========================
class ConfiguracionDespacho(models.Model):
    intervalo_fijo = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Intervalo fijo en minutos. Vacío = automático"
    )
    activa = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return (
            f"Fijo {self.intervalo_fijo} min"
            if self.intervalo_fijo
            else "Automático"
        )


from datetime import timedelta
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Q


# =========================
# REGISTRO DE SALIDA
# =========================
class RegistroSalida(models.Model):

    vehiculo = models.ForeignKey(
        "Vehiculo",
        on_delete=models.CASCADE
    )

    # ✅ RUTA PUEDE SER NULL
    # ⚠️ pero NO puede entrar en cola sin ruta (blindaje abajo)
    ruta = models.ForeignKey(
        "Ruta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    # 📅 Fecha operativa (local)
    fecha = models.DateField(
        default=timezone.localdate
    )

    # 🕓 Hora llegada a terminal
    hora_llegada = models.DateTimeField(
        default=timezone.now
    )

    # =========================
    # ESTADO OPERATIVO
    # =========================
    activo = models.BooleanField(default=True)
    en_cola = models.BooleanField(default=False)

    orden_cola = models.PositiveIntegerField(
        null=True,
        blank=True
    )

    # =========================
    # DESPACHO
    # =========================
    hora_salida = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Hora base de salida (referencia para offsets)"
    )

    hora_fija = models.DateTimeField(
        null=True,
        blank=True
    )

    intervalo_minutos = models.PositiveIntegerField(
        null=True,
        blank=True
    )

    bloqueado = models.BooleanField(default=False)

    # =========================
    # AUDITORÍA
    # =========================
    hora_real_salida = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Momento real en que la unidad inició la salida"
    )

    diferencia_minutos = models.IntegerField(
        null=True,
        blank=True
    )

    creado_en = models.DateTimeField(auto_now_add=True)

    # =================================================
    # 🔒 BLINDAJE EN BASE DE DATOS
    # =================================================
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["vehiculo", "fecha"],
                condition=Q(activo=True),
                name="una_salida_activa_por_vehiculo_y_dia"
            )
        ]
        ordering = ["fecha", "creado_en"]

    # =================================================
    # 🔒 VALIDACIONES DE NEGOCIO (FIX DEFINITIVO)
    # =================================================
    def clean(self):

        # ❌ Fuera de cola no puede tener orden
        if not self.en_cola and self.orden_cola is not None:
            raise ValidationError(
                "Una unidad fuera de la cola no puede tener orden."
            )

        # ❌ Bloqueado sin hora fija
        if self.bloqueado and not self.hora_fija:
            raise ValidationError(
                "Una unidad bloqueada debe tener hora fija."
            )

        # ❌ Hora fija solo si está en cola
        if self.hora_fija and not self.en_cola:
            raise ValidationError(
                "No se puede fijar hora si la unidad no está en cola."
            )

        # ❌ Hora fija y hora salida deben coincidir
        if (
            self.hora_fija
            and self.hora_salida
            and self.hora_fija != self.hora_salida
        ):
            raise ValidationError(
                "La hora fija debe coincidir con la hora de salida."
            )

        # =================================================
        # 🔥 BLINDAJE CLAVE (PASO 2)
        # =================================================
        # ❌ NO permitir cola sin ruta
        if self.en_cola and not self.ruta:
            raise ValidationError(
                "No se puede poner en cola una salida sin ruta asignada."
            )

    # =================================================
    # 💾 GUARDADO CONTROLADO
    # =================================================
    def save(self, *args, **kwargs):

        # 🧼 Sale de cola → reset automático
        if not self.en_cola:
            self.orden_cola = None
            self.bloqueado = False
            self.hora_fija = None

        # 🔒 Bloqueado → hora fija manda
        if self.bloqueado and self.hora_fija:
            self.hora_salida = self.hora_fija

        # 🔁 Automático → nunca conserva hora fija
        if not self.bloqueado:
            self.hora_fija = None

        super().save(*args, **kwargs)

    # =================================================
    # ▶️ INICIAR SALIDA
    # =================================================
    def iniciar_salida(self):
        if self.hora_real_salida:
            return

        ahora = timezone.now()

        self.hora_real_salida = ahora
        self.en_cola = False
        self.activo = True

        self.save(
            update_fields=[
                "hora_real_salida",
                "en_cola",
                "activo"
            ]
        )

    # =================================================
    # ⏹ FINALIZAR SALIDA
    # =================================================
    def finalizar_salida(self):
        self.activo = False
        self.en_cola = False

        self.save(update_fields=["activo", "en_cola"])

    # =================================================
    # 🧠 HORA BASE
    # =================================================
    def hora_base(self):
        return self.hora_salida

    # =================================================
    # 📍 SIGUIENTE PUNTO
    # =================================================
    def siguiente_punto(self):

        if not self.ruta:
            return None

        from django.apps import apps
        PuntoControl = apps.get_model("flota_app", "PuntoControl")
        MarcacionPunto = apps.get_model("flota_app", "MarcacionPunto")

        puntos = (
            PuntoControl.objects
            .filter(ruta=self.ruta, activo=True)
            .order_by("orden")
        )

        for punto in puntos:
            if not MarcacionPunto.objects.filter(
                registro_salida=self,
                punto=punto
            ).exists():
                return punto

        return None

    # =================================================
    # 🔥 SIGUIENTE MARCACIÓN
    # =================================================
    def siguiente_marcacion(self):

        punto = self.siguiente_punto()
        if not punto:
            return None

        from django.apps import apps
        MarcacionPunto = apps.get_model("flota_app", "MarcacionPunto")

        marcacion, _ = MarcacionPunto.objects.get_or_create(
            registro_salida=self,
            punto=punto
        )
        return marcacion

    # =================================================
    # ⏱ DIFERENCIA REAL
    # =================================================
    def calcular_diferencia(self, hora_programada, hora_real):
        return int(
            (hora_real - hora_programada).total_seconds() / 60
        )

    # =================================================
    # 🧭 MODO
    # =================================================
    @property
    def modo(self):
        return "MANUAL" if self.bloqueado else "AUTOMÁTICO"

    def __str__(self):
        ruta = self.ruta.nombre if self.ruta else "SIN RUTA"
        return f"{self.vehiculo} - {ruta} ({self.fecha})"


from django.db import models

# =========================
# PUNTO DE CONTROL
# =========================
class PuntoControl(models.Model):

    ruta = models.ForeignKey(
        Ruta,
        on_delete=models.CASCADE,
        related_name="puntos_control"
    )

    codigo = models.CharField(
        max_length=10
    )

    nombre = models.CharField(
        max_length=100
    )

    # 📍 Coordenadas GPS
    latitud = models.DecimalField(
        max_digits=9,
        decimal_places=6
    )

    longitud = models.DecimalField(
        max_digits=9,
        decimal_places=6
    )

    # 📏 Radio permitido
    radio_metros = models.PositiveIntegerField(
        default=50
    )

    # 🔢 Orden dentro de la ruta
    orden = models.PositiveIntegerField()

    # ⏱ OFFSET REAL (MINUTOS DESDE HORA DE SALIDA)
    # 🔥 CAMPO CLAVE DEL SISTEMA
    offset_minutos = models.PositiveIntegerField(
        default=0,
        help_text="Minutos desde la hora de salida"
    )

    activo = models.BooleanField(
        default=True
    )

    class Meta:
        ordering = ["orden"]
        unique_together = ("ruta", "orden")

    def __str__(self):
        return f"{self.ruta} | {self.orden}. {self.codigo}"


from datetime import timedelta
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError

# =========================
# MARCACIÓN DE PUNTO
# =========================
class MarcacionPunto(models.Model):

    registro_salida = models.ForeignKey(
        RegistroSalida,
        on_delete=models.CASCADE,
        related_name="marcaciones"
    )

    punto = models.ForeignKey(
        PuntoControl,
        on_delete=models.CASCADE
    )

    # ⏱ Hora programada REAL (calculada automáticamente)
    # 👉 Se guarda SOLO como auditoría
    hora_programada = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Hora programada calculada (hora salida + offset)"
    )

    # 🕓 Hora real en que se marcó el punto
    hora_marcada = models.DateTimeField(
        null=True,
        blank=True
    )

    # ➕➖ Diferencia real en minutos
    diferencia_minutos = models.IntegerField(
        null=True,
        blank=True
    )

    ESTADOS = (
        ("adelantado", "Adelantado"),
        ("a_tiempo", "A tiempo"),
        ("tarde", "Tarde"),
    )

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        null=True,
        blank=True
    )

    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("registro_salida", "punto")
        ordering = ["punto__orden"]

    # =================================================
    # 🔒 BLINDAJE
    # =================================================
    def clean(self):
        # ❌ No permitir modificar una marcación ya realizada
        if self.pk and self.hora_marcada:
            raise ValidationError(
                "Este punto ya fue marcado y no puede modificarse."
            )

    # =================================================
    # ⏱ CÁLCULO DE HORA PROGRAMADA (OFFSET)
    # =================================================
    def calcular_hora_programada(self):
        """
        Calcula la hora programada REAL del punto
        basada en:
        hora_salida + offset_minutos
        """
        if not self.registro_salida.hora_salida:
            return None

        return (
            self.registro_salida.hora_salida +
            timedelta(minutes=self.punto.offset_minutos)
        )

    # =================================================
    # 🧮 CÁLCULO DE DIFERENCIA Y ESTADO
    # =================================================
    def evaluar_estado(self, tolerancia_min=2):
        """
        Calcula diferencia en minutos y estado
        según tolerancia.
        """
        if not self.hora_marcada or not self.hora_programada:
            return

        diferencia = int(
            (self.hora_marcada - self.hora_programada).total_seconds() / 60
        )

        self.diferencia_minutos = diferencia

        if diferencia < -tolerancia_min:
            self.estado = "adelantado"
        elif diferencia > tolerancia_min:
            self.estado = "tarde"
        else:
            self.estado = "a_tiempo"

    # =================================================
    # 🔥 MÉTODO CLAVE (USADO POR GPS Y DESPACHADOR)
    # =================================================
    def marcar(self, hora=None):
        """
        Marca el punto una sola vez.
        Usado por:
        - GPS automático (app conductor)
        - Despachador (manual / auto)
        """

        # 🛑 Si ya está marcado, no hacer nada
        if self.hora_marcada:
            return

        ahora = hora or timezone.now()
        self.hora_marcada = ahora

        # ⏱ Calcular hora programada si no existe
        if not self.hora_programada:
            self.hora_programada = self.calcular_hora_programada()

        # 🧮 Evaluar estado y diferencia
        self.evaluar_estado()

        # 💾 Guardar todo junto
        self.save()

    # =================================================
    # 💾 GUARDADO CONTROLADO (ROBUSTO)
    # =================================================
    def save(self, *args, **kwargs):
        """
        Guardado seguro:
        ✔ Calcula hora_programada solo una vez
        ✔ Evalúa estado solo cuando se marca
        ✔ No recalcula datos históricos
        """

        # 🧠 Calcular hora programada SOLO si aún no existe
        if not self.hora_programada:
            self.hora_programada = self.calcular_hora_programada()

        # 🕓 Si se marca por primera vez, evaluar estado
        if self.hora_marcada and not self.estado:
            self.evaluar_estado()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.registro_salida} - {self.punto.codigo}"

import uuid
from django.db import models
from django.utils import timezone

# =========================
# 🔐 SESIÓN ACTIVA POR UNIDAD
# =========================
class SesionUnidad(models.Model):

    vehiculo = models.ForeignKey(
        "Vehiculo",
        on_delete=models.CASCADE,
        related_name="sesiones"
    )

    # ⚠️ REFERENCIA OPCIONAL
    # ❌ NO define la sesión
    # ✅ Solo para auditoría o contexto
    salida = models.ForeignKey(
        "RegistroSalida",
        on_delete=models.SET_NULL,   # 🔑 NO destruir sesión si la salida desaparece
        related_name="sesiones",
        null=True,
        blank=True
    )

    token = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True
    )

    # 🔥 CLAVE REAL DE LA SESIÓN
    activa = models.BooleanField(default=True)

    creada_en = models.DateTimeField(auto_now_add=True)

    # ⏳ OPCIONAL — NO se usa para expirar automáticamente
    expira_en = models.DateTimeField(
        null=True,
        blank=True
    )

    # =================================================
    # 🫀 HEARTBEAT (NUEVO — NO ROMPE NADA)
    # =================================================
    last_heartbeat = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Última señal de vida de la app del conductor"
    )

    # =================================================
    # 🧾 REPRESENTACIÓN SEGURA
    # =================================================
    def __str__(self):
        estado = "ACTIVA" if self.activa else "INACTIVA"
        salida_id = self.salida.id if self.salida else "—"
        return f"Unidad {self.vehiculo.numero} | Salida {salida_id} | {estado}"

    # =================================================
    # 🔐 VALIDACIÓN CENTRAL DE SESIÓN
    # =================================================
    def esta_valida(self):
        """
        ✅ Una sesión es válida si:
        - Está activa
        - Y NO tiene expiración
          O aún no ha expirado

        ⚠️ NO depende de la salida
        """
        if not self.activa:
            return False

        if self.expira_en is None:
            return True

        return timezone.now() < self.expira_en

    # =================================================
    # 🫀 UTILIDAD HEARTBEAT (OPCIONAL, SEGURA)
    # =================================================
    def marcar_heartbeat(self):
        """
        Registra señal de vida de la app.
        Método opcional para mantener lógica limpia.
        """
        self.last_heartbeat = timezone.now()
        self.save(update_fields=["last_heartbeat"])


from django.db import models

class GPSRegistro(models.Model):
    """
    Registro histórico y auditable de posiciones GPS
    asociadas a una sesión activa de un vehículo.
    """

    sesion = models.ForeignKey(
        SesionUnidad,
        on_delete=models.CASCADE,
        related_name="gps_registros"
    )

    lat = models.FloatField(help_text="Latitud GPS")
    lng = models.FloatField(help_text="Longitud GPS")

    velocidad = models.FloatField(
        null=True,
        blank=True,
        help_text="Velocidad en km/h"
    )

    precision = models.FloatField(
        null=True,
        blank=True,
        help_text="Precisión GPS en metros"
    )

    bateria = models.IntegerField(
        null=True,
        blank=True,
        help_text="Nivel de batería del dispositivo (%)"
    )

    timestamp = models.DateTimeField(
        auto_now_add=True,
        help_text="Fecha y hora del registro"
    )

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Registro GPS"
        verbose_name_plural = "Registros GPS"

    def __str__(self):
        return (
            f"GPS | Sesión {self.sesion.id} | "
            f"{self.lat}, {self.lng} | {self.timestamp}"
        )

# =========================
# 📍 UBICACIÓN ACTUAL DEL VEHÍCULO (TIEMPO REAL)
# =========================
class UbicacionVehiculo(models.Model):
    vehiculo = models.OneToOneField(
        Vehiculo,
        on_delete=models.CASCADE,
        related_name="ubicacion_actual"
    )

    latitud = models.FloatField()
    longitud = models.FloatField()

    velocidad = models.FloatField(
        null=True,
        blank=True,
        help_text="Velocidad actual en km/h"
    )

    precision = models.FloatField(
        null=True,
        blank=True,
        help_text="Precisión GPS en metros"
    )

    # 🕒 Última actualización (clave para mapa en tiempo real)
    updated_at = models.DateTimeField(
        auto_now=True
    )

    def __str__(self):
        return (
            f"Ubicación actual | "
            f"Unidad {self.vehiculo.numero} | "
            f"{self.latitud}, {self.longitud}"
        )

from django.db import models
from django.utils import timezone

class PosicionActual(models.Model):
    vehiculo = models.OneToOneField(
        'Vehiculo',
        on_delete=models.CASCADE,
        related_name='posicion_actual'
    )
    lat = models.FloatField()
    lng = models.FloatField()
    velocidad = models.FloatField(default=0)
    rumbo = models.FloatField(default=0)
    actualizado_en = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.vehiculo} @ {self.lat},{self.lng}"

# app/models.py
from django.db import models

class Parada(models.Model):
    vehiculo = models.ForeignKey(
        'Vehiculo',
        on_delete=models.CASCADE,
        related_name='paradas'
    )

    lat = models.FloatField()
    lng = models.FloatField()

    inicio = models.DateTimeField()
    fin = models.DateTimeField(null=True, blank=True)

    duracion_segundos = models.PositiveIntegerField(default=0)

    activa = models.BooleanField(default=True)

    # =================================================
    # 🔥 FASE 4 — CLASIFICACIÓN
    # =================================================
    es_prolongada = models.BooleanField(
        default=False,
        help_text="Indica si la parada superó el umbral de tiempo prolongado"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    # =================================================
    # 🔒 CIERRE CONTROLADO
    # =================================================
    def cerrar(self, fin):
        self.fin = fin
        self.duracion_segundos = int((fin - self.inicio).total_seconds())
        self.activa = False
        self.save()

    def __str__(self):
        estado = "PROLONGADA" if self.es_prolongada else "NORMAL"
        return f"Parada {self.vehiculo} | {estado} | {self.duracion_segundos}s"





