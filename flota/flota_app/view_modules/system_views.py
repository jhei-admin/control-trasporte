from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET


@require_GET
def healthz(request):
    db_ok = True
    pending_migrations = 0
    db_error = None

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        pending_migrations = len(executor.migration_plan(targets))
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    status = 200 if db_ok and pending_migrations == 0 else 503
    payload = {
        "ok": db_ok and pending_migrations == 0,
        "database_ok": db_ok,
        "database_engine": connection.settings_dict.get("ENGINE"),
        "pending_migrations": pending_migrations,
        "timestamp": timezone.now().isoformat(),
    }
    if db_error:
        payload["database_error"] = db_error

    return JsonResponse(payload, status=status)
