import logging
import threading
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError
from django.db.models import F
from django.utils import timezone


logger = logging.getLogger("flota_app")

_lock = threading.Lock()
_pending = defaultdict(lambda: {"requests": 0, "bytes": 0})
_last_flush = timezone.now()


def _bucket_start(dt):
    local_dt = timezone.localtime(dt)
    return local_dt.replace(minute=0, second=0, microsecond=0)


def _response_bytes(response):
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            return max(0, int(content_length))
        except (TypeError, ValueError):
            pass

    if getattr(response, "streaming", False):
        return 0

    content = getattr(response, "content", b"")
    try:
        return len(content)
    except TypeError:
        return 0


def _should_track(path):
    if not path:
        return False
    excluded_prefixes = getattr(
        settings,
        "ENDPOINT_USAGE_EXCLUDED_PREFIXES",
        ("/static/", "/favicon.ico"),
    )
    return not any(path.startswith(prefix) for prefix in excluded_prefixes)


def _flush_pending(force=False):
    global _last_flush

    now = timezone.now()
    flush_seconds = max(5, int(getattr(settings, "ENDPOINT_USAGE_FLUSH_SECONDS", 60)))
    flush_count = max(10, int(getattr(settings, "ENDPOINT_USAGE_FLUSH_COUNT", 100)))

    with _lock:
        pending_count = sum(item["requests"] for item in _pending.values())
        if not force and pending_count < flush_count and now - _last_flush < timedelta(seconds=flush_seconds):
            return

        snapshot = dict(_pending)
        _pending.clear()
        _last_flush = now

    if not snapshot:
        return

    from flota_app.models import EndpointUsageHourly

    for (bucket, method, path, status_code), item in snapshot.items():
        try:
            obj, created = EndpointUsageHourly.objects.get_or_create(
                bucket_start=bucket,
                method=method,
                path=path,
                status_code=status_code,
                defaults={
                    "request_count": item["requests"],
                    "bytes_sent": item["bytes"],
                    "latest_seen": now,
                },
            )
            if not created:
                EndpointUsageHourly.objects.filter(pk=obj.pk).update(
                    request_count=F("request_count") + item["requests"],
                    bytes_sent=F("bytes_sent") + item["bytes"],
                    latest_seen=now,
                )
        except DatabaseError:
            logger.exception("No se pudo registrar uso del endpoint %s %s", method, path)


class EndpointUsageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        path = (getattr(request, "path_info", "") or getattr(request, "path", "") or "")[:255]
        if _should_track(path):
            now = timezone.now()
            key = (
                _bucket_start(now),
                (request.method or "GET")[:10],
                path,
                int(getattr(response, "status_code", 0) or 0),
            )
            with _lock:
                _pending[key]["requests"] += 1
                _pending[key]["bytes"] += _response_bytes(response)

            try:
                _flush_pending()
            except Exception:
                logger.exception("No se pudo vaciar metricas de endpoints")

        return response
