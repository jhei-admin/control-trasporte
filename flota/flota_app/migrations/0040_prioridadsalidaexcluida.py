from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0039_endpointusagehourly"),
    ]

    operations = [
        migrations.CreateModel(
            name="PrioridadSalidaExcluida",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("creada_en", models.DateTimeField(auto_now_add=True)),
                ("excluida_por", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="prioridades_excluidas", to=settings.AUTH_USER_MODEL)),
                ("salida", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="exclusion_prioridad", to="flota_app.registrosalida")),
            ],
            options={
                "verbose_name": "Prioridad excluida",
                "verbose_name_plural": "Prioridades excluidas",
                "ordering": ["-creada_en"],
            },
        ),
    ]
