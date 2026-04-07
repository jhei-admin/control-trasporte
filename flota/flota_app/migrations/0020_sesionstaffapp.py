from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0019_puntocontrol_requiere_marcacion"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SesionStaffApp",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ("activa", models.BooleanField(db_index=True, default=True)),
                ("creada_en", models.DateTimeField(auto_now_add=True)),
                ("expira_en", models.DateTimeField(blank=True, null=True)),
                ("ultimo_acceso", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sesiones_staff_app", to="flota_app.empresa")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sesiones_staff_app", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name="sesionstaffapp",
            index=models.Index(fields=["user", "activa"], name="flota_app_s_user_ac_b4d52b_idx"),
        ),
        migrations.AddIndex(
            model_name="sesionstaffapp",
            index=models.Index(fields=["empresa", "activa"], name="flota_app_s_empresa_84584b_idx"),
        ),
        migrations.AddIndex(
            model_name="sesionstaffapp",
            index=models.Index(fields=["token", "activa"], name="flota_app_s_token_3be5b0_idx"),
        ),
        migrations.AddIndex(
            model_name="sesionstaffapp",
            index=models.Index(fields=["expira_en"], name="flota_app_s_expira_e6da82_idx"),
        ),
        migrations.AddIndex(
            model_name="sesionstaffapp",
            index=models.Index(fields=["ultimo_acceso"], name="flota_app_s_ultimo__f7e69f_idx"),
        ),
    ]
