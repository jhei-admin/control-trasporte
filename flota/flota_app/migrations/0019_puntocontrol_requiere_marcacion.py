from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0018_mensajeglobal_empresa_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="puntocontrol",
            name="requiere_marcacion",
            field=models.BooleanField(
                default=True,
                help_text="Desactive para usar este punto solo como referencia visual en el mapa.",
            ),
        ),
    ]
