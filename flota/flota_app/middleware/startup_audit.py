import logging
import os
import threading


logger = logging.getLogger(__name__)
_audit_lock = threading.Lock()
_audit_done = False


class StartupAuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._run_audit_once()
        return self.get_response(request)

    def _run_audit_once(self):
        global _audit_done

        if _audit_done:
            return

        if os.getenv("RUN_STARTUP_AUDIT", "").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            _audit_done = True
            return

        with _audit_lock:
            if _audit_done:
                return

            try:
                from flota_app.management.commands.auditar_preproduccion import Command

                command = Command()
                checks = [
                    command.check_database_engine(),
                    command.check_pending_migrations(),
                    command.check_debug_disabled(),
                    command.check_secret_key(),
                    command.check_ssl_redirect(),
                    command.check_secure_cookies(),
                    command.check_sqlite_blocker(),
                    command.check_allowed_hosts(),
                    command.check_csrf_trusted_origins(),
                    command.check_retention_windows(),
                    command.check_mapbox_token(),
                ]

                logger.info("=== Auditoria automatica de preproduccion ===")
                for status, title, detail in checks:
                    message = f"[{status}] {title}: {detail}"
                    if status == "FAIL":
                        logger.error(message)
                    elif status == "WARN":
                        logger.warning(message)
                    else:
                        logger.info(message)

                failures = [check for check in checks if check[0] == "FAIL"]
                warnings = [check for check in checks if check[0] == "WARN"]
                logger.info(
                    "Resumen auditoria automatica: %s fallas, %s advertencias, %s ok",
                    len(failures),
                    len(warnings),
                    len(checks) - len(failures) - len(warnings),
                )
            except Exception as exc:
                logger.exception(
                    "No se pudo ejecutar la auditoria automatica de preproduccion: %s",
                    exc,
                )
            finally:
                _audit_done = True
