from django.db import migrations, models


def poblar_fase_inicial(apps, schema_editor):
    PuntoControl = apps.get_model("flota_app", "PuntoControl")

    PuntoControl.objects.filter(es_contexto_interno=True).update(fase="CTX")
    PuntoControl.objects.filter(
        es_contexto_interno=False,
        orden__gt=11,
    ).update(fase="RET")
    PuntoControl.objects.filter(
        es_contexto_interno=False,
        orden__lte=11,
    ).update(fase="IDA")


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0025_alter_marcacionpunto_estado"),
    ]

    operations = [
        migrations.AddField(
            model_name="puntocontrol",
            name="fase",
            field=models.CharField(
                choices=[("IDA", "Ida"), ("RET", "Retorno"), ("CTX", "Contexto")],
                default="IDA",
                help_text="Clasifica el punto dentro de la ruta sin cambiar como se muestra en pantalla.",
                max_length=3,
            ),
        ),
        migrations.RunPython(poblar_fase_inicial, migrations.RunPython.noop),
    ]
