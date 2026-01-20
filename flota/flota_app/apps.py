from django.apps import AppConfig
import os


class FlotaAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"

    # ⚠️ NOMBRE REAL DE LA APP (DEBE COINCIDIR CON LA CARPETA)
    name = "flota_app"
    verbose_name = "Control de Flota"

    def ready(self):
        """
        Hook de inicialización de la app.

        ✔ Se ejecuta UNA SOLA VEZ cuando Django arranca
        ✔ Aquí se pueden registrar señales
        ✔ Aquí se puede crear el superusuario en Render
        """

        # 🔒 IMPORTAR AQUÍ (NO ARRIBA)
        # Evita errores de carga circular
        try:
            from django.contrib.auth import get_user_model
        except Exception:
            return

        username = os.getenv("DJANGO_SUPERUSER_USERNAME")
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD")
        email = os.getenv("DJANGO_SUPERUSER_EMAIL", "")

        # 👉 Si no hay variables de entorno, salir sin hacer nada
        if not username or not password:
            return

        User = get_user_model()

        try:
            if not User.objects.filter(username=username).exists():
                User.objects.create_superuser(
                    username=username,
                    password=password,
                    email=email,
                )
        except Exception:
            # 🔒 IMPORTANTE:
            # - Evita que el deploy falle
            # - Evita errores si las migraciones aún no terminaron
            # - Evita duplicados
            pass
