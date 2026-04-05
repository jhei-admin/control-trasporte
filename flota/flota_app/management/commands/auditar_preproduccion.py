from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


DEFAULT_INSECURE_KEYS = {
    "",
    "django-insecure-dev-key",
    "changeme",
    "secret",
}


class Command(BaseCommand):
    help = (
        "Audita si la configuracion actual esta lista para preproduccion "
        "empresarial."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-warnings",
            action="store_true",
            help="No falla por advertencias; solo falla si hay errores criticos.",
        )

    def handle(self, *args, **options):
        allow_warnings = options["allow_warnings"]
        checks = [
            self.check_database_engine(),
            self.check_pending_migrations(),
            self.check_debug_disabled(),
            self.check_secret_key(),
            self.check_ssl_redirect(),
            self.check_secure_cookies(),
            self.check_sqlite_blocker(),
            self.check_allowed_hosts(),
            self.check_csrf_trusted_origins(),
            self.check_retention_windows(),
            self.check_mapbox_token(),
        ]

        failures = [check for check in checks if check[0] == "FAIL"]
        warnings = [check for check in checks if check[0] == "WARN"]

        self.stdout.write("Auditoria de preproduccion empresarial")
        self.stdout.write("=" * 40)
        for status, title, detail in checks:
            self.stdout.write(f"[{status}] {title}: {detail}")

        self.stdout.write("")
        self.stdout.write(
            f"Resumen: {len(failures)} fallas, {len(warnings)} advertencias, "
            f"{len(checks) - len(failures) - len(warnings)} ok"
        )

        if failures:
            raise CommandError(
                "La configuracion aun no esta lista para preproduccion empresarial."
            )

        if warnings and not allow_warnings:
            raise CommandError(
                "La auditoria quedo sin fallas criticas, pero aun hay advertencias."
            )

    def check_database_engine(self):
        engine = settings.DATABASES["default"]["ENGINE"]
        if "postgresql" not in engine:
            return (
                "FAIL",
                "Base de datos",
                f"Motor actual: {engine}. En preproduccion empresarial debe ser PostgreSQL.",
            )
        return ("OK", "Base de datos", f"Motor actual: {engine}.")

    def check_pending_migrations(self):
        connection = connections["default"]
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        pending = len(executor.migration_plan(targets))
        if pending:
            return (
                "FAIL",
                "Migraciones",
                f"Hay {pending} migraciones pendientes.",
            )
        return ("OK", "Migraciones", "No hay migraciones pendientes.")

    def check_debug_disabled(self):
        if settings.DEBUG:
            return ("FAIL", "DEBUG", "DEBUG debe estar en false.")
        return ("OK", "DEBUG", "DEBUG esta desactivado.")

    def check_secret_key(self):
        secret_key = getattr(settings, "SECRET_KEY", "")
        if secret_key in DEFAULT_INSECURE_KEYS or secret_key.startswith("django-insecure"):
            return (
                "FAIL",
                "SECRET_KEY",
                "SECRET_KEY sigue usando un valor inseguro o de desarrollo.",
            )
        return ("OK", "SECRET_KEY", "SECRET_KEY no parece de desarrollo.")

    def check_ssl_redirect(self):
        if not getattr(settings, "SECURE_SSL_REDIRECT", False):
            return (
                "WARN",
                "HTTPS",
                "SECURE_SSL_REDIRECT esta desactivado; conviene activarlo en Render.",
            )
        return ("OK", "HTTPS", "SECURE_SSL_REDIRECT esta activado.")

    def check_secure_cookies(self):
        csrf_secure = getattr(settings, "CSRF_COOKIE_SECURE", False)
        session_secure = getattr(settings, "SESSION_COOKIE_SECURE", False)
        if not csrf_secure or not session_secure:
            return (
                "FAIL",
                "Cookies seguras",
                "CSRF_COOKIE_SECURE y SESSION_COOKIE_SECURE deben estar en true.",
            )
        return ("OK", "Cookies seguras", "Las cookies sensibles estan en modo seguro.")

    def check_sqlite_blocker(self):
        if not getattr(settings, "FAIL_ON_SQLITE_IN_PRODUCTION", False):
            return (
                "WARN",
                "Bloqueo de SQLite",
                "FAIL_ON_SQLITE_IN_PRODUCTION esta desactivado.",
            )
        return ("OK", "Bloqueo de SQLite", "SQLite queda bloqueado en produccion.")

    def check_allowed_hosts(self):
        hosts = [host for host in getattr(settings, "ALLOWED_HOSTS", []) if host]
        if not hosts:
            return ("FAIL", "ALLOWED_HOSTS", "No hay hosts permitidos configurados.")
        return ("OK", "ALLOWED_HOSTS", f"Hosts configurados: {', '.join(hosts)}")

    def check_csrf_trusted_origins(self):
        origins = [origin for origin in getattr(settings, "CSRF_TRUSTED_ORIGINS", []) if origin]
        if not origins:
            return (
                "FAIL",
                "CSRF_TRUSTED_ORIGINS",
                "No hay origenes confiables configurados para CSRF.",
            )
        return (
            "OK",
            "CSRF_TRUSTED_ORIGINS",
            f"Origenes configurados: {', '.join(origins)}",
        )

    def check_retention_windows(self):
        gps_days = getattr(settings, "GPS_RETENTION_DAYS", 0)
        paradas_days = getattr(settings, "PARADAS_RETENTION_DAYS", 0)
        inactive_days = getattr(settings, "INACTIVE_SESSION_RETENTION_DAYS", 0)
        mensajes_days = getattr(settings, "MENSAJES_RETENTION_DAYS", 0)
        if min(gps_days, paradas_days, inactive_days, mensajes_days) <= 0:
            return (
                "FAIL",
                "Retencion de datos",
                "Las ventanas de limpieza deben ser mayores que 0.",
            )
        return (
            "OK",
            "Retencion de datos",
            (
                f"GPS={gps_days}d, Paradas={paradas_days}d, "
                f"Sesiones={inactive_days}d, Mensajes={mensajes_days}d"
            ),
        )

    def check_mapbox_token(self):
        token = getattr(settings, "MAPBOX_TOKEN", None)
        if not token:
            return (
                "WARN",
                "MAPBOX_TOKEN",
                "No hay token configurado; el mapa puede fallar en despliegue real.",
            )
        return ("OK", "MAPBOX_TOKEN", "Hay token configurado para el mapa.")
