import json
from io import StringIO
from datetime import timedelta
from unittest.mock import patch
from decimal import Decimal

from django.core import signing
from django.core.management import call_command
from django.contrib.auth.models import Group, User
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Empresa,
    GPSRegistro,
    MarcacionPunto,
    MensajeGlobal,
    PuntoControl,
    RegistroSalida,
    Ruta,
    SesionUnidad,
    UbicacionVehiculo,
    Vehiculo,
)
from .management.commands.auditar_preproduccion import Command
from .services import recalcular_cola
from .view_modules.api_views import resetear_contexto_inicio_ruta


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
            placa="X3P-953",
            activo=True,
        )
        self.vehiculo_2 = Vehiculo.objects.create(
            empresa=self.empresa,
            codigo="02",
            placa="ABC-222",
            activo=True,
        )
        self.vehiculo_3 = Vehiculo.objects.create(
            empresa=self.empresa,
            codigo="03",
            placa="ABC-333",
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
        self.punto_salida = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="SALI",
            nombre="Salida",
            latitud=-16.401,
            longitud=-71.501,
            radio_metros=50,
            orden=1,
            offset_minutos=0,
            requiere_marcacion=True,
            activo=True,
        )
        self.punto_control = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="CTRL",
            nombre="Control",
            latitud=-16.402,
            longitud=-71.502,
            radio_metros=50,
            orden=2,
            offset_minutos=5,
            requiere_marcacion=True,
            activo=True,
        )
        self.punto_retorno = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="RETO",
            nombre="Retorno",
            latitud=-16.403,
            longitud=-71.503,
            radio_metros=50,
            orden=3,
            offset_minutos=10,
            requiere_marcacion=True,
            activo=True,
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

    def test_codigo_y_placa_abren_sesion_sin_qr(self):
        response = self.client.post(
            reverse("api_escanear_qr"),
            data=json.dumps({"codigo": "01", "placa": "x3p953"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["unidad"], "01")

    def test_codigo_sin_placa_falla_si_hay_colision_entre_empresas(self):
        Vehiculo.objects.create(
            empresa=self.empresa_2,
            codigo="01",
            placa="ZZZ-111",
            activo=True,
        )

        response = self.client.post(
            reverse("api_escanear_qr"),
            data=json.dumps({"codigo": "01"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Ingresa la placa", response.json()["error"])

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

    def test_mapa_muestra_unidades_offline_de_la_empresa(self):
        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_1,
            latitud=-16.4,
            longitud=-71.5,
            velocidad=0,
            precision=10,
        )
        UbicacionVehiculo.objects.filter(vehiculo=self.vehiculo_1).update(
            updated_at=timezone.now() - timedelta(minutes=20)
        )
        RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            activo=True,
            en_cola=True,
            orden_cola=1,
        )

        user = User.objects.create_user(username="desp_mapa", password="x")
        user.perfil.empresa = self.empresa
        user.perfil.save(update_fields=["empresa"])
        self.client.force_login(user)

        response = self.client.get(reverse("api_despachador_mapa"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["vehiculo"], self.vehiculo_1.codigo)
        self.assertEqual(response.json()[0]["estado_gps"], "OFFLINE")

    def test_api_app_control_marcar_rechaza_punto_fuera_de_secuencia(self):
        salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=timezone.now(),
            activo=True,
            en_cola=False,
        )
        MarcacionPunto.objects.create(
            registro_salida=salida,
            punto=self.punto_salida,
            hora_marcada=timezone.now(),
        )
        MarcacionPunto.objects.create(registro_salida=salida, punto=self.punto_control)
        MarcacionPunto.objects.create(registro_salida=salida, punto=self.punto_retorno)

        response = self.client.post(
            reverse("api_app_control_marcar"),
            data=json.dumps({"punto_id": self.punto_retorno.id}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["mensaje"], "Punto fuera de secuencia")
        self.assertEqual(response.json()["esperado"]["codigo"], "CTRL")
        self.assertIsNone(
            MarcacionPunto.objects.get(
                registro_salida=salida,
                punto=self.punto_retorno,
            ).hora_marcada
        )

    def test_api_puntos_control_expone_puntos_referenciales(self):
        PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="ENTR",
            nombre="Entrada",
            latitud=-16.404,
            longitud=-71.504,
            radio_metros=50,
            orden=4,
            offset_minutos=2,
            requiere_marcacion=False,
            activo=True,
        )
        PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="ZAMA_VTA",
            nombre="Zamacola vuelta interno",
            latitud=-16.405,
            longitud=-71.505,
            radio_metros=50,
            orden=40,
            offset_minutos=0,
            requiere_marcacion=False,
            es_contexto_interno=True,
            activo=True,
        )

        user = User.objects.create_user(username="desp_puntos", password="x")
        user.perfil.empresa = self.empresa
        user.perfil.save(update_fields=["empresa"])
        self.client.force_login(user)

        response = self.client.get(reverse("api_puntos_control"))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 4)
        self.assertTrue(any(p["requiere_marcacion"] is False for p in data))
        self.assertFalse(any(p["codigo"] == "ZAMA_VTA" for p in data))

    def test_api_gps_conductor_identifica_ultimo_punto_y_prepara_audio_profesional(self):
        ahora = timezone.now().replace(hour=17, minute=40, second=0, microsecond=0)
        salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=ahora - timedelta(minutes=17),
            activo=True,
            en_cola=False,
        )
        MarcacionPunto.objects.create(
            registro_salida=salida,
            punto=self.punto_salida,
            hora_programada=ahora - timedelta(minutes=10),
            hora_marcada=ahora - timedelta(minutes=10),
            estado="a_tiempo",
            diferencia_minutos=0,
        )
        MarcacionPunto.objects.create(
            registro_salida=salida,
            punto=self.punto_control,
            hora_programada=ahora - timedelta(minutes=5),
            hora_marcada=ahora - timedelta(minutes=3),
            estado="tarde",
            diferencia_minutos=2,
        )
        MarcacionPunto.objects.create(
            registro_salida=salida,
            punto=self.punto_retorno,
            hora_programada=ahora - timedelta(minutes=7),
        )

        with patch("flota_app.view_modules.api_views.timezone.now", return_value=ahora):
            response = self.client.post(
                reverse("api_gps_conductor"),
                data=json.dumps({
                    "lat": float(self.punto_retorno.latitud),
                    "lng": float(self.punto_retorno.longitud),
                    "precision": 10,
                }),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["accion"], "audio")
        self.assertEqual(data["audio"], "ruta_completada")
        self.assertTrue(data["finalizada"])
        hora_audio = timezone.localtime(ahora).strftime("%H:%M")
        self.assertEqual(
            data["audio_texto"],
            f"RETORNO, {hora_audio} TARDE MAS 7. RUTA FINALIZADA. BUEN TRABAJO",
        )
        self.assertEqual(data["visual"]["estado"], "TARDE")
        self.assertEqual(data["visual"]["diferencia_min"], 7)

    def test_api_gps_conductor_devuelve_audio_de_ruta_completada_al_siguiente_ping(self):
        ahora = timezone.now().replace(second=0, microsecond=0)
        salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=ahora - timedelta(minutes=15),
            hora_real_salida=ahora - timedelta(minutes=15),
            activo=True,
            en_cola=False,
        )
        for punto in (self.punto_salida, self.punto_control, self.punto_retorno):
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=punto,
                hora_programada=ahora - timedelta(minutes=5),
                hora_marcada=ahora - timedelta(minutes=4),
                estado="a_tiempo",
                diferencia_minutos=0,
            )

        with patch("flota_app.view_modules.api_views.timezone.now", return_value=ahora):
            response = self.client.post(
                reverse("api_gps_conductor"),
                data=json.dumps({
                    "lat": float(self.punto_retorno.latitud),
                    "lng": float(self.punto_retorno.longitud),
                    "precision": 10,
                }),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["accion"], "audio")
        self.assertEqual(data["audio"], "ruta_completada")
        self.assertTrue(data["finalizada"])
        self.assertEqual(data["audio_texto"], "RUTA FINALIZADA. BUEN TRABAJO")

    def test_api_cola_contexto_no_reordena_solo_por_sobrepaso_gps(self):
        salida_adelante = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=timezone.now().replace(hour=10, minute=0, second=0, microsecond=0),
            hora_real_salida=timezone.now().replace(hour=10, minute=0, second=0, microsecond=0),
            activo=True,
            en_cola=False,
        )
        salida_actual = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_2,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=timezone.now().replace(hour=10, minute=5, second=0, microsecond=0),
            hora_real_salida=timezone.now().replace(hour=10, minute=5, second=0, microsecond=0),
            activo=True,
            en_cola=False,
        )
        salida_atras = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_3,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=timezone.now().replace(hour=10, minute=10, second=0, microsecond=0),
            hora_real_salida=timezone.now().replace(hour=10, minute=10, second=0, microsecond=0),
            activo=True,
            en_cola=False,
        )

        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_1,
            latitud=Decimal("-16.3920"),
            longitud=Decimal("-71.5000"),
            velocidad=20,
            precision=10,
        )
        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_2,
            latitud=Decimal("-16.3850"),
            longitud=Decimal("-71.5000"),
            velocidad=20,
            precision=10,
        )
        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_3,
            latitud=Decimal("-16.3970"),
            longitud=Decimal("-71.5000"),
            velocidad=20,
            precision=10,
        )

        sesion_actual = SesionUnidad.objects.create(
            vehiculo=self.vehiculo_2,
            activa=True,
            last_heartbeat=timezone.now(),
            salida=salida_actual,
        )

        response = self.client.get(
            reverse("api_app_cola_contexto"),
            HTTP_AUTHORIZATION=f"Bearer {sesion_actual.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["actual"]["unidad"], self.vehiculo_2.codigo)
        self.assertEqual(data["adelante"][0]["unidad"], self.vehiculo_1.codigo)
        self.assertEqual(data["atras"][0]["unidad"], self.vehiculo_3.codigo)

    def test_api_cola_contexto_oculta_vecinos_hasta_marcar_sali(self):
        hora_base = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        salida_adelante = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base,
            hora_real_salida=hora_base,
            activo=True,
            en_cola=False,
        )
        salida_actual = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_2,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base + timedelta(minutes=5),
            activo=True,
            en_cola=False,
        )

        MarcacionPunto.objects.create(
            registro_salida=salida_adelante,
            punto=self.punto_salida,
            hora_marcada=hora_base,
            hora_programada=hora_base,
        )
        MarcacionPunto.objects.create(
            registro_salida=salida_adelante,
            punto=self.punto_control,
            hora_marcada=hora_base + timedelta(minutes=5),
            hora_programada=hora_base + timedelta(minutes=5),
        )

        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_1,
            latitud=Decimal("-16.4020"),
            longitud=Decimal("-71.5020"),
            velocidad=20,
            precision=10,
        )
        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_2,
            latitud=Decimal("-16.4010"),
            longitud=Decimal("-71.5010"),
            velocidad=0,
            precision=10,
        )

        sesion_actual = SesionUnidad.objects.create(
            vehiculo=self.vehiculo_2,
            activa=True,
            last_heartbeat=timezone.now(),
            salida=salida_actual,
        )

        response = self.client.get(
            reverse("api_app_cola_contexto"),
            HTTP_AUTHORIZATION=f"Bearer {sesion_actual.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["actual"]["unidad"], self.vehiculo_2.codigo)
        self.assertEqual(data["adelante"], [])
        self.assertEqual(data["atras"], [])

    def test_api_cola_contexto_considera_operativa_una_unidad_con_sali_marcado_aunque_falte_hora_real(self):
        hora_base = timezone.now().replace(hour=15, minute=27, second=0, microsecond=0)
        salida_adelante = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base,
            hora_real_salida=None,
            activo=True,
            en_cola=False,
        )
        salida_actual = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_2,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base + timedelta(minutes=4),
            hora_real_salida=hora_base + timedelta(minutes=4),
            activo=True,
            en_cola=False,
        )
        salida_atras = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_3,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base + timedelta(minutes=8),
            hora_real_salida=None,
            activo=True,
            en_cola=False,
        )

        punto_cole = self.punto_control
        punto_cole.codigo = "COLE"
        punto_cole.nombre = "Colegio"
        punto_cole.orden = 2
        punto_cole.offset_minutos = 4
        punto_cole.save(update_fields=["codigo", "nombre", "orden", "offset_minutos"])

        for salida, minutos in (
            (salida_adelante, 0),
            (salida_actual, 4),
            (salida_atras, 8),
        ):
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=self.punto_salida,
                hora_marcada=hora_base + timedelta(minutes=minutos),
                hora_programada=hora_base + timedelta(minutes=minutos),
            )

        MarcacionPunto.objects.create(
            registro_salida=salida_adelante,
            punto=punto_cole,
            hora_marcada=hora_base + timedelta(minutes=2),
            hora_programada=hora_base + timedelta(minutes=4),
        )

        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_1,
            latitud=Decimal("-16.4020"),
            longitud=Decimal("-71.5020"),
            velocidad=20,
            precision=10,
        )
        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_2,
            latitud=Decimal("-16.4010"),
            longitud=Decimal("-71.5010"),
            velocidad=20,
            precision=10,
        )
        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_3,
            latitud=Decimal("-16.4000"),
            longitud=Decimal("-71.5000"),
            velocidad=20,
            precision=10,
        )

        sesion_actual = SesionUnidad.objects.create(
            vehiculo=self.vehiculo_2,
            activa=True,
            last_heartbeat=timezone.now(),
            salida=salida_actual,
        )

        response = self.client.get(
            reverse("api_app_cola_contexto"),
            HTTP_AUTHORIZATION=f"Bearer {sesion_actual.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["adelante"][0]["unidad"], self.vehiculo_1.codigo)
        self.assertEqual(data["adelante"][0]["punto_referencia_codigo"], punto_cole.codigo)
        self.assertEqual(data["atras"][0]["unidad"], self.vehiculo_3.codigo)

    def test_api_cola_contexto_prefiere_sali_inicial_sobre_sali_final_en_bajada(self):
        hora_base = timezone.now().replace(hour=15, minute=27, second=0, microsecond=0)
        salida_adelante = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base,
            activo=True,
            en_cola=False,
        )
        salida_actual = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_2,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base + timedelta(minutes=4),
            activo=True,
            en_cola=False,
        )

        punto_sali_final = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="SALI",
            nombre="Salida",
            latitud=self.punto_salida.latitud,
            longitud=self.punto_salida.longitud,
            radio_metros=self.punto_salida.radio_metros,
            orden=10,
            offset_minutos=63,
            requiere_marcacion=True,
            activo=True,
        )

        for salida, minutos in ((salida_adelante, 0), (salida_actual, 4)):
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=self.punto_salida,
                hora_marcada=hora_base + timedelta(minutes=minutos),
                hora_programada=hora_base + timedelta(minutes=minutos),
            )
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=punto_sali_final,
                hora_programada=hora_base + timedelta(minutes=minutos + 60),
            )

        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_1,
            latitud=Decimal(str(self.punto_salida.latitud)),
            longitud=Decimal(str(self.punto_salida.longitud)),
            velocidad=5,
            precision=10,
        )
        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_2,
            latitud=Decimal(str(self.punto_salida.latitud)),
            longitud=Decimal(str(self.punto_salida.longitud)),
            velocidad=5,
            precision=10,
        )

        sesion_actual = SesionUnidad.objects.create(
            vehiculo=self.vehiculo_2,
            activa=True,
            last_heartbeat=timezone.now(),
            salida=salida_actual,
        )

        response = self.client.get(
            reverse("api_app_cola_contexto"),
            HTTP_AUTHORIZATION=f"Bearer {sesion_actual.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["actual"]["punto_referencia_codigo"], "SALI")
        self.assertEqual(data["adelante"][0]["unidad"], self.vehiculo_1.codigo)

    def test_api_cola_contexto_prefiere_apip_de_ida_sobre_apip_de_vuelta_en_bajada(self):
        hora_base = timezone.now().replace(hour=15, minute=27, second=0, microsecond=0)
        salida_adelante = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base,
            activo=True,
            en_cola=False,
        )
        salida_actual = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_2,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base + timedelta(minutes=4),
            activo=True,
            en_cola=False,
        )

        punto_cole = self.punto_control
        punto_cole.codigo = "COLE"
        punto_cole.nombre = "Colegio"
        punto_cole.orden = 2
        punto_cole.offset_minutos = 4
        punto_cole.save(update_fields=["codigo", "nombre", "orden", "offset_minutos"])

        punto_apip_ida = self.punto_retorno
        punto_apip_ida.codigo = "APIP"
        punto_apip_ida.nombre = "Entrada Apipa"
        punto_apip_ida.latitud = Decimal("-16.401500")
        punto_apip_ida.longitud = Decimal("-71.501500")
        punto_apip_ida.radio_metros = 60
        punto_apip_ida.orden = 3
        punto_apip_ida.offset_minutos = 12
        punto_apip_ida.requiere_marcacion = True
        punto_apip_ida.activo = True
        punto_apip_ida.save(
            update_fields=[
                "codigo",
                "nombre",
                "latitud",
                "longitud",
                "radio_metros",
                "orden",
                "offset_minutos",
                "requiere_marcacion",
                "activo",
            ]
        )
        punto_apip_vuelta = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="APIP",
            nombre="Entrada Apipa",
            latitud=-16.401500,
            longitud=-71.501500,
            radio_metros=60,
            orden=9,
            offset_minutos=52,
            requiere_marcacion=True,
            activo=True,
        )

        for salida, minutos in ((salida_adelante, 0), (salida_actual, 4)):
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=self.punto_salida,
                hora_marcada=hora_base + timedelta(minutes=minutos),
                hora_programada=hora_base + timedelta(minutes=minutos),
            )
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=punto_cole,
                hora_marcada=hora_base + timedelta(minutes=minutos + 2),
                hora_programada=hora_base + timedelta(minutes=minutos + 4),
            )
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=punto_apip_ida,
                hora_programada=hora_base + timedelta(minutes=minutos + 12),
            )
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=punto_apip_vuelta,
                hora_programada=hora_base + timedelta(minutes=minutos + 52),
            )

        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_1,
            latitud=Decimal(str(punto_apip_ida.latitud)),
            longitud=Decimal(str(punto_apip_ida.longitud)),
            velocidad=20,
            precision=10,
        )
        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_2,
            latitud=Decimal(str(punto_apip_ida.latitud)),
            longitud=Decimal(str(punto_apip_ida.longitud)),
            velocidad=20,
            precision=10,
        )

        sesion_actual = SesionUnidad.objects.create(
            vehiculo=self.vehiculo_2,
            activa=True,
            last_heartbeat=timezone.now(),
            salida=salida_actual,
        )

        response = self.client.get(
            reverse("api_app_cola_contexto"),
            HTTP_AUTHORIZATION=f"Bearer {sesion_actual.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["actual"]["punto_referencia_codigo"], "APIP")
        self.assertEqual(data["adelante"][0]["unidad"], self.vehiculo_1.codigo)

    def test_api_cola_contexto_mantiene_adelante_a_unidad_que_ya_marco_cole(self):
        hora_base = timezone.now().replace(hour=19, minute=23, second=0, microsecond=0)
        salida_adelante = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base,
            hora_real_salida=hora_base,
            activo=True,
            en_cola=False,
        )
        salida_actual = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_2,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base + timedelta(minutes=3),
            hora_real_salida=hora_base + timedelta(minutes=3),
            activo=True,
            en_cola=False,
        )

        punto_cole = self.punto_control
        punto_cole.codigo = "COLE"
        punto_cole.nombre = "Colegio"
        punto_cole.orden = 2
        punto_cole.offset_minutos = 4
        punto_cole.save(update_fields=["codigo", "nombre", "orden", "offset_minutos"])

        punto_apip = self.punto_retorno
        punto_apip.codigo = "APIP"
        punto_apip.nombre = "Entrada Apipa"
        punto_apip.orden = 3
        punto_apip.offset_minutos = 12
        punto_apip.save(update_fields=["codigo", "nombre", "orden", "offset_minutos"])

        for salida, minutos in ((salida_adelante, 0), (salida_actual, 3)):
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=self.punto_salida,
                hora_marcada=hora_base + timedelta(minutes=minutos),
                hora_programada=hora_base + timedelta(minutes=minutos),
            )
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=punto_cole,
                hora_programada=hora_base + timedelta(minutes=minutos + 4),
            )
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=punto_apip,
                hora_programada=hora_base + timedelta(minutes=minutos + 12),
            )

        MarcacionPunto.objects.filter(
            registro_salida=salida_adelante,
            punto=punto_cole,
        ).update(
            hora_marcada=hora_base + timedelta(minutes=4),
        )

        for vehiculo in (self.vehiculo_1, self.vehiculo_2):
            UbicacionVehiculo.objects.create(
                vehiculo=vehiculo,
                latitud=Decimal(str(punto_cole.latitud)),
                longitud=Decimal(str(punto_cole.longitud)),
                velocidad=5,
                precision=10,
            )

        sesion_actual = SesionUnidad.objects.create(
            vehiculo=self.vehiculo_2,
            activa=True,
            last_heartbeat=timezone.now(),
            salida=salida_actual,
        )
        self.sesion.salida = salida_adelante
        self.sesion.save(update_fields=["salida"])

        response_actual = self.client.get(
            reverse("api_app_cola_contexto"),
            HTTP_AUTHORIZATION=f"Bearer {sesion_actual.token}",
        )
        self.assertEqual(response_actual.status_code, 200)
        data_actual = response_actual.json()
        self.assertTrue(data_actual["ok"])
        self.assertEqual(data_actual["actual"]["punto_referencia_codigo"], punto_cole.codigo)
        self.assertEqual(data_actual["adelante"][0]["unidad"], self.vehiculo_1.codigo)
        self.assertEqual(data_actual["adelante"][0]["punto_referencia_codigo"], punto_cole.codigo)
        self.assertEqual(data_actual["atras"], [])

        response_adelante = self.client.get(
            reverse("api_app_cola_contexto"),
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )
        self.assertEqual(response_adelante.status_code, 200)
        data_adelante = response_adelante.json()
        self.assertTrue(data_adelante["ok"])
        self.assertEqual(data_adelante["adelante"], [])
        self.assertEqual(data_adelante["atras"][0]["unidad"], self.vehiculo_2.codigo)
        self.assertEqual(data_adelante["atras"][0]["punto_referencia_codigo"], punto_cole.codigo)

    def test_api_cola_contexto_prioriza_marcacion_confirmada_sobre_radio_sin_marcar(self):
        hora_base = timezone.now().replace(hour=19, minute=20, second=0, microsecond=0)
        salida_actual = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base,
            hora_real_salida=hora_base,
            activo=True,
            en_cola=False,
        )
        salida_adelante = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_2,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base + timedelta(minutes=3),
            hora_real_salida=hora_base + timedelta(minutes=3),
            activo=True,
            en_cola=False,
        )

        punto_cole = self.punto_control
        punto_cole.codigo = "COLE"
        punto_cole.nombre = "Colegio"
        punto_cole.orden = 2
        punto_cole.offset_minutos = 4
        punto_cole.save(update_fields=["codigo", "nombre", "orden", "offset_minutos"])

        for salida, minutos in ((salida_actual, 0), (salida_adelante, 3)):
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=self.punto_salida,
                hora_marcada=hora_base + timedelta(minutes=minutos),
                hora_programada=hora_base + timedelta(minutes=minutos),
            )
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=punto_cole,
                hora_programada=hora_base + timedelta(minutes=minutos + 4),
            )

        MarcacionPunto.objects.filter(
            registro_salida=salida_adelante,
            punto=punto_cole,
        ).update(
            hora_marcada=hora_base + timedelta(minutes=7),
        )

        for vehiculo in (self.vehiculo_1, self.vehiculo_2):
            UbicacionVehiculo.objects.create(
                vehiculo=vehiculo,
                latitud=Decimal(str(punto_cole.latitud)),
                longitud=Decimal(str(punto_cole.longitud)),
                velocidad=0,
                precision=10,
            )

        self.sesion.salida = salida_actual
        self.sesion.save(update_fields=["salida"])
        sesion_adelante = SesionUnidad.objects.create(
            vehiculo=self.vehiculo_2,
            activa=True,
            last_heartbeat=timezone.now(),
            salida=salida_adelante,
        )

        response_actual = self.client.get(
            reverse("api_app_cola_contexto"),
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response_actual.status_code, 200)
        data_actual = response_actual.json()
        self.assertTrue(data_actual["ok"])
        self.assertEqual(data_actual["actual"]["punto_referencia_codigo"], punto_cole.codigo)
        self.assertEqual(data_actual["adelante"][0]["unidad"], self.vehiculo_2.codigo)
        self.assertEqual(data_actual["atras"], [])

        response_adelante = self.client.get(
            reverse("api_app_cola_contexto"),
            HTTP_AUTHORIZATION=f"Bearer {sesion_adelante.token}",
        )
        self.assertEqual(response_adelante.status_code, 200)
        data_adelante = response_adelante.json()
        self.assertTrue(data_adelante["ok"])
        self.assertEqual(data_adelante["adelante"], [])
        self.assertEqual(data_adelante["atras"][0]["unidad"], self.vehiculo_1.codigo)

    def test_resetear_contexto_inicio_ruta_limpia_estado_previsto(self):
        hora_base = timezone.now().replace(hour=19, minute=23, second=0, microsecond=0)
        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_1,
            latitud=Decimal(str(self.punto_salida.latitud)),
            longitud=Decimal(str(self.punto_salida.longitud)),
            velocidad=0,
            precision=10,
            en_retorno=True,
            ultimo_punto_evento_codigo="PESQ",
            ultimo_punto_evento_orden=5,
            ultimo_punto_evento_at=hora_base - timedelta(minutes=10),
        )

        resetear_contexto_inicio_ruta(self.sesion)

        ubicacion = UbicacionVehiculo.objects.get(vehiculo=self.vehiculo_1)
        self.assertFalse(ubicacion.en_retorno)
        self.assertIsNone(ubicacion.ultimo_punto_evento_codigo)
        self.assertIsNone(ubicacion.ultimo_punto_evento_orden)
        self.assertIsNone(ubicacion.ultimo_punto_evento_at)

    def test_api_cola_contexto_reordena_cuando_vecino_confirma_punto_bloqueado(self):
        punto_apip = self.punto_control
        punto_apip.codigo = "APIP"
        punto_apip.nombre = "Entrada Apipa"
        punto_apip.save(update_fields=["codigo", "nombre"])
        punto_muni = self.punto_retorno
        punto_muni.codigo = "MUNI"
        punto_muni.nombre = "Entrada Municipal"
        punto_muni.requiere_marcacion = False
        punto_muni.save(update_fields=["codigo", "nombre", "requiere_marcacion"])

        hora_base = timezone.now() - timedelta(minutes=10)
        salida_actual = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base,
            hora_real_salida=hora_base,
            activo=True,
            en_cola=False,
        )
        salida_vecino = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_2,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base + timedelta(minutes=5),
            hora_real_salida=hora_base + timedelta(minutes=5),
            activo=True,
            en_cola=False,
        )

        for salida in [salida_actual, salida_vecino]:
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=self.punto_salida,
                hora_marcada=salida.hora_salida,
                hora_programada=salida.hora_salida,
            )
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=punto_apip,
                hora_marcada=salida.hora_salida + timedelta(minutes=5),
                hora_programada=salida.hora_salida + timedelta(minutes=5),
            )

        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_1,
            latitud=Decimal("-16.404100"),
            longitud=Decimal("-71.504100"),
            velocidad=20,
            precision=10,
        )
        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_2,
            latitud=Decimal("-16.405000"),
            longitud=Decimal("-71.505000"),
            velocidad=20,
            precision=10,
            ultimo_punto_evento_codigo=punto_muni.codigo,
            ultimo_punto_evento_orden=punto_muni.orden,
            ultimo_punto_evento_at=timezone.now(),
        )

        sesion_actual = self.sesion
        sesion_actual.salida = salida_actual
        sesion_actual.save(update_fields=["salida"])

        response = self.client.get(
            reverse("api_app_cola_contexto"),
            HTTP_AUTHORIZATION=f"Bearer {sesion_actual.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["actual"]["punto_referencia_codigo"], punto_apip.codigo)
        self.assertEqual(data["adelante"][0]["unidad"], self.vehiculo_2.codigo)
        self.assertEqual(data["adelante"][0]["punto_referencia_codigo"], punto_muni.codigo)

    def test_api_cola_contexto_reordena_en_bloqueado_de_bajada_si_ya_marco_sali(self):
        punto_zama = self.punto_retorno
        punto_zama.codigo = "ZAMA"
        punto_zama.nombre = "Zamacola"
        punto_zama.orden = 4
        punto_zama.offset_minutos = 28
        punto_zama.save(update_fields=["codigo", "nombre", "orden", "offset_minutos"])

        punto_apip = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="APIP",
            nombre="Entrada Apipa",
            latitud=-16.401500,
            longitud=-71.501500,
            radio_metros=60,
            orden=3,
            offset_minutos=12,
            requiere_marcacion=True,
            activo=True,
        )

        punto_pesq = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="PESQ",
            nombre="Pesquero",
            latitud=-16.406000,
            longitud=-71.506000,
            radio_metros=60,
            orden=5,
            offset_minutos=35,
            requiere_marcacion=True,
            activo=True,
        )
        PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="ZAMA_VTA",
            nombre="Zamacola vuelta interno",
            latitud=-16.403000,
            longitud=-71.503000,
            radio_metros=60,
            orden=40,
            offset_minutos=0,
            requiere_marcacion=False,
            es_contexto_interno=True,
            activo=True,
        )

        hora_base = timezone.now() - timedelta(minutes=20)
        salida_actual = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base,
            hora_real_salida=hora_base,
            activo=True,
            en_cola=False,
        )
        salida_vecino = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_2,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base + timedelta(minutes=5),
            hora_real_salida=hora_base + timedelta(minutes=5),
            activo=True,
            en_cola=False,
        )

        for salida in [salida_actual, salida_vecino]:
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=self.punto_salida,
                hora_marcada=salida.hora_salida,
                hora_programada=salida.hora_salida,
            )
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=punto_apip,
                hora_marcada=salida.hora_salida + timedelta(minutes=12),
                hora_programada=salida.hora_salida + timedelta(minutes=12),
            )
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=punto_zama,
                hora_programada=salida.hora_salida + timedelta(minutes=28),
            )
            MarcacionPunto.objects.create(
                registro_salida=salida,
                punto=punto_pesq,
                hora_programada=salida.hora_salida + timedelta(minutes=35),
            )

        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_1,
            latitud=Decimal("-16.401500"),
            longitud=Decimal("-71.501500"),
            velocidad=20,
            precision=10,
        )
        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_2,
            latitud=Decimal("-16.406000"),
            longitud=Decimal("-71.506000"),
            velocidad=20,
            precision=10,
            ultimo_punto_evento_codigo="PESQ",
            ultimo_punto_evento_orden=5,
            ultimo_punto_evento_at=timezone.now(),
            en_retorno=False,
        )

        sesion_actual = self.sesion
        sesion_actual.salida = salida_actual
        sesion_actual.save(update_fields=["salida"])

        response = self.client.get(
            reverse("api_app_cola_contexto"),
            HTTP_AUTHORIZATION=f"Bearer {sesion_actual.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["actual"]["punto_referencia_codigo"], punto_apip.codigo)
        self.assertEqual(data["adelante"][0]["unidad"], self.vehiculo_2.codigo)
        self.assertEqual(data["adelante"][0]["punto_referencia_codigo"], punto_pesq.codigo)

    def test_api_cola_contexto_expone_referencia_por_radio_sin_marcar(self):
        hora_base = timezone.now() - timedelta(minutes=10)
        salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=hora_base,
            activo=True,
            en_cola=False,
        )
        self.sesion.salida = salida
        self.sesion.save(update_fields=["salida"])

        MarcacionPunto.objects.create(
            registro_salida=salida,
            punto=self.punto_salida,
            hora_marcada=hora_base,
            hora_programada=hora_base,
        )
        MarcacionPunto.objects.create(
            registro_salida=salida,
            punto=self.punto_control,
            hora_programada=hora_base + timedelta(minutes=5),
        )
        MarcacionPunto.objects.create(
            registro_salida=salida,
            punto=self.punto_retorno,
            hora_programada=hora_base + timedelta(minutes=10),
        )

        UbicacionVehiculo.objects.create(
            vehiculo=self.vehiculo_1,
            latitud=Decimal(str(self.punto_retorno.latitud)),
            longitud=Decimal(str(self.punto_retorno.longitud)),
            velocidad=15,
            precision=10,
        )

        response = self.client.get(
            reverse("api_app_cola_contexto"),
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["actual"]["punto_actual_codigo"], self.punto_salida.codigo)
        self.assertEqual(data["actual"]["punto_referencia_codigo"], self.punto_retorno.codigo)


class LoginSistemaViewTests(BaseFlotaTestCase):
    def setUp(self):
        super().setUp()
        self.despachador_group, _ = Group.objects.get_or_create(name="despachador")

    def test_login_despachador_con_empresa_redirige_al_panel(self):
        user = User.objects.create_user(username="desp_ok", password="secret123")
        user.groups.add(self.despachador_group)
        user.perfil.empresa = self.empresa
        user.perfil.save(update_fields=["empresa"])

        response = self.client.post(
            reverse("login"),
            {"username": "desp_ok", "password": "secret123"},
        )

        self.assertRedirects(response, "/sistema/despachador/", fetch_redirect_response=False)

    def test_login_despachador_staff_con_empresa_prioriza_panel(self):
        user = User.objects.create_user(
            username="desp_staff",
            password="secret123",
            is_staff=True,
        )
        user.groups.add(self.despachador_group)
        user.perfil.empresa = self.empresa
        user.perfil.save(update_fields=["empresa"])

        response = self.client.post(
            reverse("login"),
            {"username": "desp_staff", "password": "secret123"},
        )

        self.assertRedirects(response, "/sistema/despachador/", fetch_redirect_response=False)

    def test_login_despachador_sin_empresa_cierra_sesion_y_muestra_error(self):
        user = User.objects.create_user(username="desp_sin_empresa", password="secret123")
        user.groups.add(self.despachador_group)

        response = self.client.post(
            reverse("login"),
            {"username": "desp_sin_empresa", "password": "secret123"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu usuario despachador no tiene empresa asignada.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_usuario_sin_rol_valido_cierra_sesion_y_muestra_error(self):
        User.objects.create_user(username="sin_rol", password="secret123")

        response = self.client.post(
            reverse("login"),
            {"username": "sin_rol", "password": "secret123"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu usuario no tiene permisos para ingresar al sistema.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_autenticado_sin_rol_valido_se_limpia_antes_de_mostrar_login(self):
        user = User.objects.create_user(username="sin_rol_get", password="secret123")
        self.client.force_login(user)

        response = self.client.get(reverse("login"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu usuario no tiene permisos para ingresar al sistema.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_superuser_sigue_yendo_a_admin(self):
        User.objects.create_user(
            username="admin_total",
            password="secret123",
            is_staff=True,
            is_superuser=True,
        )

        response = self.client.post(
            reverse("login"),
            {"username": "admin_total", "password": "secret123"},
        )

        self.assertRedirects(response, "/admin/", fetch_redirect_response=False)


class PanelDespachadorApiTests(BaseFlotaTestCase):
    def setUp(self):
        super().setUp()
        self.despachador_group, _ = Group.objects.get_or_create(name="despachador")
        self.user = User.objects.create_user(username="desp_panel_api", password="secret123")
        self.user.groups.add(self.despachador_group)
        self.user.perfil.empresa = self.empresa
        self.user.perfil.save(update_fields=["empresa"])
        self.client.force_login(self.user)

    def test_api_panel_despachador_devuelve_kpis_y_salidas_serializadas(self):
        hora_llegada = timezone.now() - timedelta(minutes=5)
        hora_salida = timezone.now() + timedelta(minutes=10)
        salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_llegada=hora_llegada,
            hora_salida=hora_salida,
            activo=True,
            en_cola=False,
            bloqueado=True,
        )

        response = self.client.get(
            reverse("api_panel_despachador"),
            {"ruta": self.ruta_a.id, "fecha": timezone.localdate().isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["stats"]["activas"], 1)
        self.assertEqual(data["stats"]["programadas"], 1)
        self.assertEqual(data["stats"]["atrasadas"], 0)
        self.assertEqual(data["stats"]["sin_hora"], 0)
        self.assertEqual(data["ruta_actual_id"], str(self.ruta_a.id))
        self.assertEqual(data["ruta_actual_nombre"], self.ruta_a.nombre)
        self.assertEqual(len(data["salidas"]), 1)
        self.assertEqual(data["salidas"][0]["id"], salida.id)
        self.assertEqual(data["salidas"][0]["unidad"], self.vehiculo_1.codigo)
        self.assertEqual(
            data["salidas"][0]["urls"]["asignar_hora"],
            reverse("asignar_hora_fija", args=[salida.id]),
        )
        self.assertIn(
            reverse("reporte_salidas_diarias", args=[self.vehiculo_1.id]),
            data["reporte_url"],
        )

    def test_api_panel_despachador_rechaza_fecha_invalida(self):
        response = self.client.get(
            reverse("api_panel_despachador"),
            {"fecha": "2026-99-99"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])


class DispatcherLiveApisTests(BaseFlotaTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="desp_live_api", password="secret123")
        self.user.perfil.empresa = self.empresa
        self.user.perfil.save(update_fields=["empresa"])
        self.client.force_login(self.user)

    def test_api_historial_salidas_devuelve_resumen_y_registros(self):
        salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=timezone.now(),
            activo=True,
        )
        PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="SALI",
            nombre="Salida",
            latitud=-16.4,
            longitud=-71.5,
            radio_metros=50,
            orden=1,
            offset_minutos=0,
            requiere_marcacion=True,
            activo=True,
        )
        MarcacionPunto.objects.create(
            registro_salida=salida,
            punto=PuntoControl.objects.get(codigo="SALI"),
            hora_marcada=salida.hora_salida,
            diferencia_minutos=0,
            estado="a_tiempo",
        )

        response = self.client.get(reverse("api_historial_salidas"))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["resumen"]["total"], 1)
        self.assertEqual(data["historial"][0]["unidad"], self.vehiculo_1.codigo)

    def test_api_reporte_salidas_diarias_devuelve_tarjetas_y_urls(self):
        salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=timezone.now(),
            activo=True,
        )

        response = self.client.get(
            reverse("api_reporte_salidas_diarias", args=[self.vehiculo_1.id]),
            {"fecha": timezone.localdate().isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["resumen"]["total_vueltas"], 1)
        self.assertEqual(data["salidas"][0]["salida_id"], salida.id)
        self.assertEqual(
            data["salidas"][0]["detalle_url"],
            reverse("detalle_salida", args=[salida.id]),
        )

    def test_api_control_y_detalle_salida_web_exponen_progreso(self):
        salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=timezone.now(),
            activo=True,
        )
        punto = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="CTRL",
            nombre="Control 1",
            latitud=-16.4,
            longitud=-71.5,
            radio_metros=50,
            orden=1,
            offset_minutos=0,
            requiere_marcacion=True,
            activo=True,
        )
        MarcacionPunto.objects.create(
            registro_salida=salida,
            punto=punto,
            hora_marcada=salida.hora_salida,
            diferencia_minutos=0,
            estado="a_tiempo",
        )

        control_response = self.client.get(reverse("api_control_ruta_web", args=[salida.id]))
        detalle_response = self.client.get(reverse("api_detalle_salida_web", args=[salida.id]))

        self.assertEqual(control_response.status_code, 200)
        self.assertEqual(detalle_response.status_code, 200)
        self.assertEqual(control_response.json()["resumen"]["completados"], 1)
        self.assertEqual(detalle_response.json()["resumen"]["completados"], 1)
        self.assertEqual(detalle_response.json()["detalle"][0]["codigo"], punto.codigo)

    def test_api_detalle_salida_web_respeta_estado_omitido(self):
        salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=timezone.now(),
            activo=True,
        )
        punto = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="CTRL",
            nombre="Control omitido",
            latitud=-16.4,
            longitud=-71.5,
            radio_metros=50,
            orden=1,
            offset_minutos=15,
            requiere_marcacion=True,
            activo=True,
        )
        MarcacionPunto.objects.create(
            registro_salida=salida,
            punto=punto,
            hora_marcada=salida.hora_salida,
            diferencia_minutos=-15,
            estado="omitido",
        )

        detalle_response = self.client.get(reverse("api_detalle_salida_web", args=[salida.id]))

        self.assertEqual(detalle_response.status_code, 200)
        self.assertEqual(detalle_response.json()["detalle"][0]["estado"], "omitido")


class PuntoReferenciaTests(BaseFlotaTestCase):
    def test_reporte_cuenta_solo_puntos_de_marcacion(self):
        user = User.objects.create_user(username="desp_reportes", password="x")
        user.perfil.empresa = self.empresa
        user.perfil.save(update_fields=["empresa"])
        self.client.force_login(user)

        PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="SALI",
            nombre="Salida",
            latitud=-16.401,
            longitud=-71.501,
            radio_metros=50,
            orden=1,
            offset_minutos=0,
            requiere_marcacion=True,
            activo=True,
        )
        punto_ref = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="ENTR",
            nombre="Entrada",
            latitud=-16.402,
            longitud=-71.502,
            radio_metros=50,
            orden=2,
            offset_minutos=2,
            requiere_marcacion=False,
            activo=True,
        )

        salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=timezone.now(),
            activo=True,
            en_cola=False,
        )
        MarcacionPunto.objects.create(
            registro_salida=salida,
            punto=PuntoControl.objects.get(codigo="SALI", ruta=self.ruta_a),
            hora_marcada=timezone.now(),
        )

        response = self.client.get(
            reverse("reporte_salidas_diarias", args=[self.vehiculo_1.id]),
            {"fecha": timezone.localdate().isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100%")
        self.assertFalse(
            MarcacionPunto.objects.filter(registro_salida=salida, punto=punto_ref).exists()
        )


class MarcacionGpsRecoveryTests(BaseFlotaTestCase):
    def setUp(self):
        super().setUp()
        self.sesion = SesionUnidad.objects.create(
            vehiculo=self.vehiculo_1,
            activa=True,
            last_heartbeat=timezone.now(),
        )
        self.punto_salida = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="SALI",
            nombre="Salida",
            latitud=-16.401000,
            longitud=-71.501000,
            radio_metros=60,
            orden=1,
            offset_minutos=0,
            requiere_marcacion=True,
            activo=True,
        )
        self.punto_control = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="CTRL",
            nombre="Control",
            latitud=-16.402000,
            longitud=-71.502000,
            radio_metros=60,
            orden=2,
            offset_minutos=5,
            requiere_marcacion=True,
            activo=True,
        )
        self.punto_final = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="FIN",
            nombre="Final",
            latitud=-16.403000,
            longitud=-71.503000,
            radio_metros=60,
            orden=3,
            offset_minutos=10,
            requiere_marcacion=True,
            activo=True,
        )
        self.salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=timezone.now() - timedelta(minutes=10),
            activo=True,
            en_cola=False,
        )

    def test_gps_omite_punto_perdido_y_continua_con_el_siguiente(self):
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_salida,
            hora_marcada=timezone.now() - timedelta(minutes=9),
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_control,
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_final,
        )

        response = self.client.post(
            reverse("api_gps_conductor"),
            data=json.dumps(
                {
                    "lat": float(self.punto_final.latitud),
                    "lng": float(self.punto_final.longitud),
                    "precision": 10,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["visual"]["codigo"], "FIN")
        self.assertEqual(data["omitidos"][0]["codigo"], "CTRL")

        omitida = MarcacionPunto.objects.get(registro_salida=self.salida, punto=self.punto_control)
        final = MarcacionPunto.objects.get(registro_salida=self.salida, punto=self.punto_final)

        self.assertEqual(omitida.estado, "omitido")
        self.assertIsNotNone(omitida.hora_marcada)
        self.assertIsNotNone(final.hora_marcada)

    def test_gps_no_omite_si_aun_no_llega_a_un_punto_posterior(self):
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_salida,
            hora_marcada=timezone.now() - timedelta(minutes=9),
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_control,
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_final,
        )

        response = self.client.post(
            reverse("api_gps_conductor"),
            data=json.dumps(
                {
                    "lat": -16.450000,
                    "lng": -71.550000,
                    "precision": 10,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accion"], "ninguna")

        omitida = MarcacionPunto.objects.get(registro_salida=self.salida, punto=self.punto_control)
        final = MarcacionPunto.objects.get(registro_salida=self.salida, punto=self.punto_final)

        self.assertIsNone(omitida.hora_marcada)
        self.assertIsNone(final.hora_marcada)

    def test_gps_no_omite_varios_puntos_en_un_solo_salto(self):
        self.punto_final.orden = 4
        self.punto_final.offset_minutos = 10
        self.punto_final.save(update_fields=["orden", "offset_minutos"])
        punto_extra = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="MID2",
            nombre="Control 2",
            latitud=-16.402500,
            longitud=-71.502500,
            radio_metros=60,
            orden=3,
            offset_minutos=7,
            requiere_marcacion=True,
            activo=True,
        )

        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_salida,
            hora_marcada=timezone.now() - timedelta(minutes=9),
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_control,
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=punto_extra,
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_final,
        )

        response = self.client.post(
            reverse("api_gps_conductor"),
            data=json.dumps(
                {
                    "lat": float(self.punto_final.latitud),
                    "lng": float(self.punto_final.longitud),
                    "precision": 10,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accion"], "beep")
        self.assertEqual(response.json()["esperado"]["codigo"], "CTRL")
        self.assertEqual(response.json()["bloqueado"]["codigo"], "FIN")
        self.assertTrue(response.json()["cola_contexto"]["ok"])

        self.assertIsNone(
            MarcacionPunto.objects.get(
                registro_salida=self.salida,
                punto=self.punto_control,
            ).hora_marcada
        )
        self.assertIsNone(
            MarcacionPunto.objects.get(
                registro_salida=self.salida,
                punto=punto_extra,
            ).hora_marcada
        )
        self.assertIsNone(
            MarcacionPunto.objects.get(
                registro_salida=self.salida,
                punto=self.punto_final,
            ).hora_marcada
        )

    def test_gps_no_omite_el_punto_previo_si_aun_no_estaba_vencido(self):
        hora_salida = timezone.now() - timedelta(minutes=12)
        self.salida.hora_salida = hora_salida
        self.salida.save(update_fields=["hora_salida"])

        self.punto_control.offset_minutos = 15
        self.punto_control.save(update_fields=["offset_minutos"])
        self.punto_final.offset_minutos = 20
        self.punto_final.save(update_fields=["offset_minutos"])

        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_salida,
            hora_marcada=hora_salida,
            hora_programada=hora_salida,
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_control,
            hora_programada=hora_salida + timedelta(minutes=15),
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_final,
            hora_programada=hora_salida + timedelta(minutes=20),
        )

        response = self.client.post(
            reverse("api_gps_conductor"),
            data=json.dumps(
                {
                    "lat": float(self.punto_final.latitud),
                    "lng": float(self.punto_final.longitud),
                    "precision": 10,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accion"], "beep")
        self.assertEqual(response.json().get("omitidos"), None)

        omitida = MarcacionPunto.objects.get(registro_salida=self.salida, punto=self.punto_control)
        final = MarcacionPunto.objects.get(registro_salida=self.salida, punto=self.punto_final)

        self.assertIsNone(omitida.hora_marcada)
        self.assertIsNone(final.hora_marcada)

    def test_gps_bloquea_salto_automatico_desde_zama_hacia_punto_posterior(self):
        hora_salida = timezone.now() - timedelta(minutes=20)
        self.salida.hora_salida = hora_salida
        self.salida.save(update_fields=["hora_salida"])

        self.punto_control.codigo = "ZAMA"
        self.punto_control.nombre = "Zamacola"
        self.punto_control.orden = 4
        self.punto_control.offset_minutos = 28
        self.punto_control.save(update_fields=["codigo", "nombre", "orden", "offset_minutos"])

        self.punto_final.codigo = "PESQ"
        self.punto_final.nombre = "Pesquero"
        self.punto_final.orden = 5
        self.punto_final.offset_minutos = 35
        self.punto_final.save(update_fields=["codigo", "nombre", "orden", "offset_minutos"])

        punto_apip = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="APIP",
            nombre="Entrada apipa",
            latitud=-16.401500,
            longitud=-71.501500,
            radio_metros=60,
            orden=3,
            offset_minutos=12,
            requiere_marcacion=True,
            activo=True,
        )

        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_salida,
            hora_marcada=hora_salida,
            hora_programada=hora_salida,
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=punto_apip,
            hora_marcada=hora_salida + timedelta(minutes=12),
            hora_programada=hora_salida + timedelta(minutes=12),
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_control,
            hora_programada=hora_salida + timedelta(minutes=28),
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_final,
            hora_programada=hora_salida + timedelta(minutes=35),
        )

        response = self.client.post(
            reverse("api_gps_conductor"),
            data=json.dumps(
                {
                    "lat": float(self.punto_final.latitud),
                    "lng": float(self.punto_final.longitud),
                    "precision": 10,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accion"], "beep")
        self.assertEqual(response.json()["esperado"]["codigo"], "ZAMA")
        self.assertEqual(response.json()["bloqueado"]["codigo"], "PESQ")

        self.assertIsNone(
            MarcacionPunto.objects.get(
                registro_salida=self.salida,
                punto=self.punto_control,
            ).hora_marcada
        )
        self.assertIsNone(
            MarcacionPunto.objects.get(
                registro_salida=self.salida,
                punto=self.punto_final,
            ).hora_marcada
        )

    def test_gps_habilita_marcacion_de_retorno_tras_punto_contexto_oculto(self):
        hora_salida = timezone.now() - timedelta(minutes=20)
        self.salida.hora_salida = hora_salida
        self.salida.save(update_fields=["hora_salida"])

        self.punto_control.codigo = "ZAMA"
        self.punto_control.nombre = "Zamacola"
        self.punto_control.orden = 4
        self.punto_control.offset_minutos = 28
        self.punto_control.save(update_fields=["codigo", "nombre", "orden", "offset_minutos"])

        self.punto_final.codigo = "PESQ"
        self.punto_final.nombre = "Pesquero"
        self.punto_final.orden = 5
        self.punto_final.offset_minutos = 35
        self.punto_final.save(update_fields=["codigo", "nombre", "orden", "offset_minutos"])

        punto_apip = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="APIP",
            nombre="Entrada apipa",
            latitud=-16.401500,
            longitud=-71.501500,
            radio_metros=60,
            orden=3,
            offset_minutos=12,
            requiere_marcacion=True,
            activo=True,
        )
        punto_contexto = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="ZAMA_VTA",
            nombre="Zamacola vuelta interno",
            latitud=-16.402000,
            longitud=-71.502000,
            radio_metros=60,
            orden=40,
            offset_minutos=0,
            requiere_marcacion=False,
            es_contexto_interno=True,
            activo=True,
        )

        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_salida,
            hora_marcada=hora_salida,
            hora_programada=hora_salida,
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=punto_apip,
            hora_marcada=hora_salida + timedelta(minutes=12),
            hora_programada=hora_salida + timedelta(minutes=12),
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_control,
            hora_marcada=hora_salida + timedelta(minutes=28),
            hora_programada=hora_salida + timedelta(minutes=28),
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_final,
            hora_programada=hora_salida + timedelta(minutes=35),
        )

        response_bloqueado = self.client.post(
            reverse("api_gps_conductor"),
            data=json.dumps(
                {
                    "lat": float(self.punto_final.latitud),
                    "lng": float(self.punto_final.longitud),
                    "precision": 10,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response_bloqueado.status_code, 200)
        self.assertEqual(response_bloqueado.json()["accion"], "beep")
        self.assertEqual(response_bloqueado.json()["bloqueado"]["codigo"], "PESQ")

        response_contexto = self.client.post(
            reverse("api_gps_conductor"),
            data=json.dumps(
                {
                    "lat": float(punto_contexto.latitud),
                    "lng": float(punto_contexto.longitud),
                    "precision": 10,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response_contexto.status_code, 200)
        self.assertEqual(response_contexto.json()["accion"], "beep")
        self.assertEqual(response_contexto.json()["bloqueado"]["codigo"], "ZAMA")

        response_marcacion = self.client.post(
            reverse("api_gps_conductor"),
            data=json.dumps(
                {
                    "lat": float(self.punto_final.latitud),
                    "lng": float(self.punto_final.longitud),
                    "precision": 10,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response_marcacion.status_code, 200)
        self.assertIn(response_marcacion.json()["accion"], ["audio", "visual"])
        self.assertEqual(response_marcacion.json()["visual"]["codigo"], "PESQ")
        self.assertTrue(
            UbicacionVehiculo.objects.get(vehiculo=self.vehiculo_1).en_retorno
        )
        self.assertIsNotNone(
            MarcacionPunto.objects.get(
                registro_salida=self.salida,
                punto=self.punto_final,
            ).hora_marcada
        )

    def test_gps_reproduce_audio_en_punto_contexto_vuelta_oculto(self):
        hora_salida = timezone.now() - timedelta(minutes=20)
        self.salida.hora_salida = hora_salida
        self.salida.save(update_fields=["hora_salida"])

        self.punto_control.codigo = "ZAMA"
        self.punto_control.nombre = "Zamacola"
        self.punto_control.orden = 4
        self.punto_control.offset_minutos = 28
        self.punto_control.save(update_fields=["codigo", "nombre", "orden", "offset_minutos"])

        self.punto_final.codigo = "PESQ"
        self.punto_final.nombre = "Pesquero"
        self.punto_final.orden = 5
        self.punto_final.offset_minutos = 35
        self.punto_final.save(update_fields=["codigo", "nombre", "orden", "offset_minutos"])

        punto_apip = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="APIP",
            nombre="Entrada apipa",
            latitud=-16.401500,
            longitud=-71.501500,
            radio_metros=60,
            orden=3,
            offset_minutos=12,
            requiere_marcacion=True,
            activo=True,
        )
        punto_contexto = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="ZAMA_VTA",
            nombre="Zamacola vuelta interno",
            latitud=-16.402000,
            longitud=-71.502000,
            radio_metros=60,
            orden=40,
            offset_minutos=0,
            requiere_marcacion=False,
            es_contexto_interno=True,
            activo=True,
        )

        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_salida,
            hora_marcada=hora_salida,
            hora_programada=hora_salida,
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=punto_apip,
            hora_marcada=hora_salida + timedelta(minutes=12),
            hora_programada=hora_salida + timedelta(minutes=12),
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_control,
            hora_marcada=hora_salida + timedelta(minutes=28),
            hora_programada=hora_salida + timedelta(minutes=28),
        )
        MarcacionPunto.objects.create(
            registro_salida=self.salida,
            punto=self.punto_final,
            hora_programada=hora_salida + timedelta(minutes=35),
        )

        response = self.client.post(
            reverse("api_gps_conductor"),
            data=json.dumps(
                {
                    "lat": float(punto_contexto.latitud),
                    "lng": float(punto_contexto.longitud),
                    "precision": 10,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.sesion.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["accion"], "beep")
        self.assertEqual(data["bloqueado"]["codigo"], "ZAMA")
        self.assertEqual(data["bloqueado"]["nombre"], "Zamacola")
        self.assertTrue(data["cola_contexto"]["ok"])
        self.assertTrue(
            UbicacionVehiculo.objects.get(vehiculo=self.vehiculo_1).en_retorno
        )
        self.assertIsNone(
            MarcacionPunto.objects.get(
                registro_salida=self.salida,
                punto=self.punto_final,
            ).hora_marcada
        )


class PanelDespachoRapidoTests(BaseFlotaTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="desp_panel",
            password="x",
            is_staff=True,
            is_superuser=True,
        )
        self.user.perfil.empresa = self.empresa
        self.user.perfil.save(update_fields=["empresa"])
        self.client.force_login(self.user)

    def test_flujo_rapido_crea_salida_y_programa_hora(self):
        PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="SALI",
            nombre="Salida",
            latitud=-16.401,
            longitud=-71.501,
            radio_metros=50,
            orden=1,
            offset_minutos=0,
            requiere_marcacion=True,
            activo=True,
        )
        PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="ENTR",
            nombre="Entrada",
            latitud=-16.402,
            longitud=-71.502,
            radio_metros=50,
            orden=2,
            offset_minutos=2,
            requiere_marcacion=False,
            activo=True,
        )

        response = self.client.post(
            reverse("buscar_unidad_panel"),
            {
                "codigo": self.vehiculo_1.codigo,
                "ruta_id": self.ruta_a.id,
                "hora_fija": "08:15",
                "current_ruta_id": self.ruta_a.id,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        salida = RegistroSalida.objects.get(
            vehiculo=self.vehiculo_1,
            fecha=timezone.localdate(),
            activo=True,
        )
        self.assertEqual(salida.ruta, self.ruta_a)
        self.assertIsNotNone(salida.hora_salida)
        self.assertEqual(salida.marcaciones.count(), 1)

    def test_flujo_rapido_actualiza_salida_existente(self):
        salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_llegada=timezone.now(),
            activo=True,
            en_cola=False,
        )

        response = self.client.post(
            reverse("buscar_unidad_panel"),
            {
                "codigo": self.vehiculo_1.codigo,
                "ruta_id": self.ruta_a.id,
                "hora_fija": "09:45",
                "current_ruta_id": self.ruta_a.id,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        salida.refresh_from_db()
        self.assertEqual(timezone.localtime(salida.hora_salida).strftime("%H:%M"), "09:45")

    def test_flujo_rapido_permita_programar_fecha_siguiente(self):
        manana = timezone.localdate() + timedelta(days=1)

        response = self.client.post(
            reverse("buscar_unidad_panel"),
            {
                "codigo": self.vehiculo_2.codigo,
                "ruta_id": self.ruta_a.id,
                "hora_fija": "04:30",
                "fecha_operativa": manana.isoformat(),
                "current_ruta_id": self.ruta_a.id,
                "current_fecha": manana.isoformat(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        salida = RegistroSalida.objects.get(
            vehiculo=self.vehiculo_2,
            fecha=manana,
            activo=True,
        )
        self.assertEqual(salida.ruta, self.ruta_a)
        self.assertEqual(timezone.localtime(salida.hora_salida).strftime("%H:%M"), "04:30")


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


class BootstrapYPurgaDemoTests(TestCase):
    def test_bootstrap_usa_nombre_neutro_por_defecto(self):
        out = StringIO()
        call_command(
            "bootstrap_inicial",
            admin_pass="ClaveAdmin123!",
            despachador_pass="ClaveDesp123!",
            stdout=out,
        )

        self.assertTrue(
            Empresa.objects.filter(nombre="Empresa Inicial Transporte").exists()
        )
        self.assertFalse(
            Empresa.objects.filter(nombre="Empresa Demo Transporte").exists()
        )

    def test_purgar_demo_audita_sin_borrar_por_defecto(self):
        empresa = Empresa.objects.create(nombre="Empresa Demo Transporte")
        User.objects.create_user(username="admin-demo", password="x")

        out = StringIO()
        call_command("purgar_demo", stdout=out)

        self.assertIn("Modo auditoria", out.getvalue())
        self.assertTrue(Empresa.objects.filter(pk=empresa.pk).exists())
        self.assertTrue(User.objects.filter(username="admin-demo").exists())

    def test_purgar_demo_borra_empresas_y_usuarios_confirmados(self):
        empresa = Empresa.objects.create(nombre="Empresa Demo Transporte")
        User.objects.create_user(username="despachador-demo", password="x")

        out = StringIO()
        call_command("purgar_demo", "--aplicar", stdout=out)

        self.assertIn("Purga completada", out.getvalue())
        self.assertFalse(Empresa.objects.filter(pk=empresa.pk).exists())
        self.assertFalse(User.objects.filter(username="despachador-demo").exists())


class PoblarEscalaTests(TestCase):
    def test_poblar_escala_crea_datos_multiempresa(self):
        out = StringIO()

        call_command(
            "poblar_escala",
            empresas=2,
            rutas=2,
            puntos=4,
            unidades=5,
            historico_gps=2,
            prefijo="Stress",
            stdout=out,
        )

        self.assertEqual(Empresa.objects.filter(nombre__startswith="Stress Empresa").count(), 2)
        self.assertEqual(Ruta.objects.filter(empresa__nombre__startswith="Stress Empresa").count(), 4)
        self.assertEqual(PuntoControl.objects.filter(ruta__empresa__nombre__startswith="Stress Empresa").count(), 16)
        self.assertEqual(Vehiculo.objects.filter(empresa__nombre__startswith="Stress Empresa").count(), 10)
        self.assertEqual(RegistroSalida.objects.filter(vehiculo__empresa__nombre__startswith="Stress Empresa", activo=True).count(), 10)
        self.assertEqual(SesionUnidad.objects.filter(vehiculo__empresa__nombre__startswith="Stress Empresa", activa=True).count(), 10)
        self.assertEqual(UbicacionVehiculo.objects.filter(vehiculo__empresa__nombre__startswith="Stress Empresa").count(), 10)
        self.assertEqual(GPSRegistro.objects.filter(sesion__vehiculo__empresa__nombre__startswith="Stress Empresa").count(), 20)
        self.assertTrue(
            User.objects.filter(username="stress_admin_01", perfil__empresa__nombre="Stress Empresa 01").exists()
        )
        self.assertTrue(
            User.objects.filter(username="stress_desp_02", perfil__empresa__nombre="Stress Empresa 02").exists()
        )

    def test_poblar_escala_permita_sembrar_sin_gps(self):
        call_command(
            "poblar_escala",
            empresas=1,
            rutas=1,
            puntos=3,
            unidades=3,
            historico_gps=5,
            prefijo="Seco",
            sin_gps=True,
        )

        self.assertEqual(Empresa.objects.filter(nombre="Seco Empresa 01").count(), 1)
        self.assertEqual(Vehiculo.objects.filter(empresa__nombre="Seco Empresa 01").count(), 3)
        self.assertEqual(UbicacionVehiculo.objects.filter(vehiculo__empresa__nombre="Seco Empresa 01").count(), 0)
        self.assertEqual(GPSRegistro.objects.filter(sesion__vehiculo__empresa__nombre="Seco Empresa 01").count(), 0)

    def test_simular_gps_actualiza_ubicaciones_y_historico(self):
        call_command(
            "poblar_escala",
            empresas=1,
            rutas=1,
            puntos=3,
            unidades=2,
            historico_gps=0,
            prefijo="SimGps",
        )

        gps_inicial = GPSRegistro.objects.filter(
            sesion__vehiculo__empresa__nombre="SimGps Empresa 01"
        ).count()

        call_command(
            "simular_gps",
            prefijo="SimGps",
            empresas=1,
            unidades=2,
            iteraciones=4,
            interval_seconds=5,
        )

        self.assertEqual(
            UbicacionVehiculo.objects.filter(vehiculo__empresa__nombre="SimGps Empresa 01").count(),
            2,
        )
        self.assertEqual(
            GPSRegistro.objects.filter(sesion__vehiculo__empresa__nombre="SimGps Empresa 01").count(),
            gps_inicial + 8,
        )
        self.assertEqual(
            SesionUnidad.objects.filter(
                vehiculo__empresa__nombre="SimGps Empresa 01",
                last_heartbeat__isnull=False,
            ).count(),
            2,
        )


class MarcacionToleranciaTests(BaseFlotaTestCase):
    def setUp(self):
        super().setUp()
        self.punto = PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="TEST",
            nombre="Punto Test",
            latitud=-16.401000,
            longitud=-71.501000,
            radio_metros=60,
            orden=1,
            offset_minutos=0,
            requiere_marcacion=True,
            activo=True,
        )
        self.salida = RegistroSalida.objects.create(
            vehiculo=self.vehiculo_1,
            ruta=self.ruta_a,
            fecha=timezone.localdate(),
            hora_salida=timezone.now(),
            activo=True,
            en_cola=False,
        )

    def test_mantiene_a_tiempo_dentro_de_tolerancia_de_30_segundos(self):
        programada = timezone.now().replace(microsecond=0)
        marcacion = MarcacionPunto(
            registro_salida=self.salida,
            punto=self.punto,
            hora_programada=programada,
            hora_marcada=programada + timedelta(seconds=29),
        )

        marcacion.evaluar_estado()

        self.assertEqual(marcacion.estado, "a_tiempo")
        self.assertEqual(marcacion.diferencia_minutos, 0)

    def test_marca_tarde_fuera_de_tolerancia_de_30_segundos(self):
        programada = timezone.now().replace(microsecond=0)
        marcacion = MarcacionPunto(
            registro_salida=self.salida,
            punto=self.punto,
            hora_programada=programada,
            hora_marcada=programada + timedelta(seconds=31),
        )

        marcacion.evaluar_estado()

        self.assertEqual(marcacion.estado, "tarde")
        self.assertEqual(marcacion.diferencia_minutos, 1)

    def test_marca_adelantado_fuera_de_tolerancia_de_30_segundos(self):
        programada = timezone.now().replace(microsecond=0)
        marcacion = MarcacionPunto(
            registro_salida=self.salida,
            punto=self.punto,
            hora_programada=programada,
            hora_marcada=programada - timedelta(seconds=31),
        )

        marcacion.evaluar_estado()

        self.assertEqual(marcacion.estado, "adelantado")
        self.assertEqual(marcacion.diferencia_minutos, -1)


class AuditoriaPreproduccionTests(TestCase):
    @override_settings(
        DEBUG=False,
        SECRET_KEY="ClaveSuperSeguraParaProduccion123!",
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "flota",
            }
        },
        SECURE_SSL_REDIRECT=True,
        CSRF_COOKIE_SECURE=True,
        SESSION_COOKIE_SECURE=True,
        FAIL_ON_SQLITE_IN_PRODUCTION=True,
        ALLOWED_HOSTS=["control-trasporte.onrender.com"],
        CSRF_TRUSTED_ORIGINS=["https://control-trasporte.onrender.com"],
        GPS_RETENTION_DAYS=15,
        PARADAS_RETENTION_DAYS=2,
        INACTIVE_SESSION_RETENTION_DAYS=7,
        MENSAJES_RETENTION_DAYS=30,
        MAPBOX_TOKEN="pk.test.token",
    )
    def test_auditar_preproduccion_detecta_postgresql_como_valido(self):
        command = Command()

        status, _, detail = command.check_database_engine()

        self.assertEqual(status, "OK")
        self.assertIn("postgresql", detail)

    @override_settings(
        DEBUG=True,
        SECRET_KEY="django-insecure-dev-key",
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        CSRF_COOKIE_SECURE=False,
        SESSION_COOKIE_SECURE=False,
    )
    def test_auditar_preproduccion_marca_fallos_criticos_basicos(self):
        command = Command()

        self.assertEqual(command.check_database_engine()[0], "FAIL")
        self.assertEqual(command.check_debug_disabled()[0], "FAIL")
        self.assertEqual(command.check_secret_key()[0], "FAIL")
        self.assertEqual(command.check_secure_cookies()[0], "FAIL")
