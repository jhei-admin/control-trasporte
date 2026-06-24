from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0035_ubicacionvehiculo_rumbo"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehiculo",
            name="mensaje_suspension",
            field=models.CharField(
                blank=True,
                default="Servicio suspendido. Comuníquese con administración.",
                help_text="Mensaje visible en la APK cuando el servicio esta suspendido.",
                max_length=160,
            ),
        ),
        migrations.AddField(
            model_name="vehiculo",
            name="servicio_suspendido",
            field=models.BooleanField(
                default=False,
                help_text="Suspende la APK operativa sin cerrar la sesion.",
            ),
        ),
        migrations.AddIndex(
            model_name="vehiculo",
            index=models.Index(fields=["servicio_suspendido"], name="flota_app_v_servici_c00596_idx"),
        ),
    ]
