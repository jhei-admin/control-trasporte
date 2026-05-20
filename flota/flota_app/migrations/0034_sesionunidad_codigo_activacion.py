from django.db import migrations, models
import uuid


def poblar_codigos_activacion(apps, schema_editor):
    SesionUnidad = apps.get_model("flota_app", "SesionUnidad")

    usados = set(
        SesionUnidad.objects.exclude(codigo_activacion__isnull=True)
        .exclude(codigo_activacion__exact="")
        .values_list("codigo_activacion", flat=True)
    )

    for sesion in SesionUnidad.objects.filter(codigo_activacion__isnull=True):
        while True:
            candidato = uuid.uuid4().hex[:8].upper()
            if candidato not in usados:
                usados.add(candidato)
                sesion.codigo_activacion = candidato
                sesion.save(update_fields=["codigo_activacion"])
                break


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0033_device_owner_updates"),
    ]

    operations = [
        migrations.AddField(
            model_name="sesionunidad",
            name="codigo_activacion",
            field=models.CharField(
                blank=True,
                db_index=True,
                editable=False,
                max_length=12,
                null=True,
                unique=True,
            ),
        ),
        migrations.RunPython(
            poblar_codigos_activacion,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="sesionunidad",
            name="codigo_activacion",
            field=models.CharField(
                blank=True,
                db_index=True,
                editable=False,
                max_length=12,
                unique=True,
            ),
        ),
    ]
