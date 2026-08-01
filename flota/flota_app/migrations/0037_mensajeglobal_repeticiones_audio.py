from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0036_vehiculo_servicio_suspendido"),
    ]

    operations = [
        migrations.AddField(
            model_name="mensajeglobal",
            name="repeticiones_audio",
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text="Cantidad de veces que la app lee el comunicado por audio.",
            ),
        ),
    ]
