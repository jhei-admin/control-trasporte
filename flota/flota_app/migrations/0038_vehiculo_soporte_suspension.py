from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0037_mensajeglobal_repeticiones_audio"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehiculo",
            name="soporte_suspension",
            field=models.CharField(
                blank=True,
                default="Soporte: 970 183 281",
                help_text="Linea de soporte visible en la APK cuando el servicio esta suspendido.",
                max_length=120,
            ),
        ),
    ]
