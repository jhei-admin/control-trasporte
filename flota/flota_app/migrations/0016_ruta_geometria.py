from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0015_registrosalida_flota_app_r_vehicul_eced5b_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="ruta",
            name="geometria",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Lista ordenada de coordenadas [lat, lng] para dibujar la ruta real",
            ),
        ),
    ]
