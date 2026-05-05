from django.db import migrations, models


def poblar_confirma_avance_inicial(apps, schema_editor):
    PuntoControl = apps.get_model("flota_app", "PuntoControl")

    PuntoControl.objects.filter(fase="CTX").update(confirma_avance=False)
    PuntoControl.objects.exclude(fase="CTX").update(confirma_avance=True)


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0026_puntocontrol_fase"),
    ]

    operations = [
        migrations.AddField(
            model_name="puntocontrol",
            name="confirma_avance",
            field=models.BooleanField(
                default=True,
                help_text="Permite usar este punto para confirmar avance real y reordenar unidades adelante/atras sin volverlo control de puntualidad.",
            ),
        ),
        migrations.RunPython(poblar_confirma_avance_inicial, migrations.RunPython.noop),
    ]
