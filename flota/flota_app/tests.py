import json
from datetime import timedelta

from django.core import signing
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Empresa,
    MensajeGlobal,
    RegistroSalida,
    Ruta,
    SesionUnidad,
    Vehiculo,
)
from .services import recalcular_cola


class BaseFlotaTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.empresa = Empresa.objects.create(nombre="Empresa Uno")
        self.empresa_2 = Empresa.objects.create(nombre="Empresa Dos")
        self.ruta_a = Ruta.objects.create(empresa=self.empresa, nombre="Ruta A")
        self.ruta_b = Ruta.objects.create(empresa=self.empresa, nombre="Ruta B")
        self.vehiculo_1 = Vehiculo.objects.create(
            empresa=self.empresa,
            codigo="01",
            activo=True,
        )
        self.vehiculo_2 = Vehiculo.objects.create(
            empresa=self.empresa,
            codigo="02",
            activo=True,
        )
        self.vehiculo_3 = Vehiculo.objects.create(
            empresa=self.empresa,
            codigo="03",
            activo=True,
        )


class RegistroSalidaRulesTests(BaseFlotaTestCase):
    def test_hora_fija_persiste_fuera_de_cola(self):
        salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            activo=True,
            en_cola=False,
            bloqueado=True,
            hora_fija=timezone.now() + timedelta(minutes=5),
            hora_salida=timezone.now() + timedelta(minutes=5),
        )

        salida.refresh_from_db()

        self.assertTrue(salida.bloqueado)
        self.assertIsNotNone(salida.hora_fija)
        self.assertEqual(salida.hora_salida, salida.hora_fija)

    def test_solo_una_sesion_activa_por_vehiculo(self):
        SesionUnidad.objects.create(vehiculo=self.vehiculo_1, activa=True)

        with self.assertRaises(IntegrityError):
            SesionUnidad.objects.create(vehiculo=self.vehiculo_1, activa=True)


class RecalcularColaTests(BaseFlotaTestCase):
    def test_intervalo_automatico_se_calcula_por_ruta(self):
        ahora = timezone.now()
        salidas_ruta_a = [
            RegistroSalida.objects.create(
                vehiculo=vehiculo,
                ruta=self.ruta_a,
                fecha=timezone.localdate(),
                hora_llegada=ahora + timedelta(minutes=index),
                activo=True,
                en_cola=True,
                orden_cola=index + 1,
            )
            for index, vehiculo in enumerate([self.vehiculo_1, self.vehiculo_2, self.vehiculo_3])
        ]
        vehiculos_ruta_b = [
            Vehiculo.objects.create(empresa=self.empresa, codigo=f"1{index}", activo=True)
            for index in range(4, 10)
        ]
        for index, vehiculo in enumerate(vehiculos_ruta_b):
            RegistroSalida.objects.create(
                vehiculo=vehiculo,
                ruta=self.ruta_b,
                fecha=timezone.localdate(),
                hora_llegada=ahora + timedelta(minutes=index),
                activo=True,
                en_cola=True,
                orden_cola=index + 1,
            )

        recalcular_cola(empresa=self.empresa)

        for salida in salidas_ruta_a:
            salida.refresh_from_db()
            self.assertEqual(salida.intervalo_minutos, 7)

        primera_ruta_b = RegistroSalida.objects.get(vehiculo=vehiculos_ruta_b[0], ruta=self.ruta_b)
        primera_ruta_b.refresh_from_db()
        self.assertEqual(primera_ruta_b.intervalo_minutos, 10)


class ApiSecurityAndIsolationTests(BaseFlotaTestCase):
    def setUp(self):
        super().setUp()
        self.sesion = SesionUnidad.objects.create(
            vehiculo=self.vehiculo_1,
            activa=True,
            last_heartbeat=timezone.now(),
        )

    @override_settings(ALLOW_LEGACY_QR=False)
    def test_qr_invalido_no_abre_sesion(self):
        response = self.client.post(
            reverse("api_escanear_qr"),
            data=json.dumps({"token": str(self.vehiculo_1.id)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "QR invalido o manipulado")

    @override_settings(ALLOW_LEGACY_QR=False)
    def test_qr_firmado_abre_sesion(self):
        token = signing.dumps(
            {"vehiculo_id": self.vehiculo_1.id, "empresa_id": self.empresa.id},
            salt="qr-unidad",
        )

        response = self.client.post(
            reverse("api_escanear_qr"),
            data=json.dumps({"token": token}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_heartbeat_filtra_mensajes_por_empresa(self):
        hoy = timezone.localdate()
        MensajeGlobal.objects.create(
            empresa=self.empresa_2,
            texto="Solo empresa dos",
            activo=True,
            fecha_inicio=hoy,
            fecha_fin=hoy,
        )
        MensajeGlobal.objects.create(
            empresa=None,
            texto="Mensaje global",
            activo=True,
            fecha_inicio=hoy,
            fecha_fin=hoy,
        )
        MensajeGlobal.objects.create(
            empresa=self.empresa,
            texto="Mensaje empresa uno",
            activo=True,
            fecha_inicio=hoy,
            fecha_fin=hoy,
        )

        response = self.client.post(
            reverse("api_heartbeat"),
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mensaje"]["texto"], "Mensaje empresa uno")


class OperacionProduccionTests(BaseFlotaTestCase):
    def test_healthz_responde_ok_en_pruebas(self):
        response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    @override_settings(INACTIVE_SESSION_RETENTION_DAYS=7, MENSAJES_RETENTION_DAYS=30)
    def test_limpiar_historicos_elimina_sesiones_y_mensajes_antiguos(self):
        sesion = SesionUnidad.objects.create(
            vehiculo=self.vehiculo_1,
            activa=False,
        )
        SesionUnidad.objects.filter(pk=sesion.pk).update(
            creada_en=timezone.now() - timedelta(days=10)
        )

        hoy = timezone.localdate()
        MensajeGlobal.objects.create(
            empresa=self.empresa,
            texto="Caducado",
            activo=False,
            fecha_inicio=hoy - timedelta(days=40),
            fecha_fin=hoy - timedelta(days=35),
        )

        call_command("limpiar_historicos")

        self.assertFalse(
            SesionUnidad.objects.filter(pk=sesion.pk).exists()
        )
        self.assertFalse(
            MensajeGlobal.objects.filter(texto="Caducado").exists()
        )
