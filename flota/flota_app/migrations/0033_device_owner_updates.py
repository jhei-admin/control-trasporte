from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0029_comandodispositivo"),
    ]

    operations = [
        migrations.AddField(
            model_name="estadodispositivo",
            name="admin_app_version",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AddField(
            model_name="estadodispositivo",
            name="admin_app_version_code",
            field=models.CharField(blank=True, default="", max_length=30),
        ),
        migrations.AddField(
            model_name="estadodispositivo",
            name="admin_home_activo",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="estadodispositivo",
            name="admin_reportado_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="estadodispositivo",
            name="admin_ultimo_estado",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="estadodispositivo",
            name="device_owner_activo",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="comandodispositivo",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("FORZAR_SYNC", "Forzar sincronizacion"),
                    ("REPORTAR_ESTADO", "Reportar estado"),
                    ("REABRIR_APP", "Reabrir app"),
                    ("ACTIVAR_KIOSCO", "Activar kiosco"),
                    ("SALIR_KIOSCO", "Salir kiosco"),
                    ("ABRIR_WIFI_TECNICO", "Abrir WiFi tecnico"),
                    ("ACTUALIZAR_OPERATIVA", "Actualizar APK operativa"),
                    ("ACTUALIZAR_ADMIN", "Actualizar APK admin"),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
    ]
