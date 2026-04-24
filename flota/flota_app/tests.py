import json
from io import StringIO
from datetime import timedelta

from django.core import signing
from django.core.management import call_command
from django.contrib.auth.models import User
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

    def test_api_puntos_control_expone_puntos_referenciales(self):
        PuntoControl.objects.create(
            ruta=self.ruta_a,
            codigo="CTRL",
            nombre="Control",
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

        user = User.objects.create_user(username="desp_puntos", password="x")
        user.perfil.empresa = self.empresa
        user.perfil.save(update_fields=["empresa"])
        self.client.force_login(user)

        response = self.client.get(reverse("api_puntos_control"))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertTrue(any(p["requiere_marcacion"] is False for p in data))


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
