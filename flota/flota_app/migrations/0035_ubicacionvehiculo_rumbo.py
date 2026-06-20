from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0034_sesionunidad_codigo_activacion"),
    ]

    operations = [
        migrations.AddField(
            model_name="ubicacionvehiculo",
            name="rumbo",
            field=models.FloatField(blank=True, default=0, null=True),
        ),
    ]
