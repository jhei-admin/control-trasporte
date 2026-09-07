from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from flota_app.models import EndpointUsageHourly


class Command(BaseCommand):
    help = "Muestra el consumo aproximado agrupado por endpoint."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Horas hacia atras a revisar",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Cantidad de endpoints a mostrar",
        )
        parser.add_argument(
            "--path",
            default="",
            help="Filtro parcial de ruta, por ejemplo gps/conductor",
        )

    def handle(self, *args, **options):
        hours = max(1, options["hours"])
        limit = max(1, options["limit"])
        path_filter = (options["path"] or "").strip()
        desde = timezone.now() - timedelta(hours=hours)

        qs = EndpointUsageHourly.objects.filter(bucket_start__gte=desde)
        if path_filter:
            qs = qs.filter(path__icontains=path_filter)

        rows = (
            qs.values("method", "path")
            .annotate(
                requests=Sum("request_count"),
                bytes=Sum("bytes_sent"),
            )
            .order_by("-bytes", "-requests")[:limit]
        )

        total = qs.aggregate(
            requests=Sum("request_count"),
            bytes=Sum("bytes_sent"),
        )
        total_requests = total["requests"] or 0
        total_bytes = total["bytes"] or 0

        self.stdout.write(f"Resumen ultimas {hours} horas")
        self.stdout.write(f"Requests medidos: {total_requests}")
        self.stdout.write(f"Consumo aprox: {total_bytes / 1024 / 1024:.2f} MB")
        self.stdout.write("")
        self.stdout.write(f"{'METHOD':<7} {'REQ':>10} {'MB':>10} PATH")
        self.stdout.write("-" * 90)

        for row in rows:
            req = row["requests"] or 0
            byte_count = row["bytes"] or 0
            mb = byte_count / 1024 / 1024
            self.stdout.write(f"{row['method']:<7} {req:>10} {mb:>10.2f} {row['path']}")
