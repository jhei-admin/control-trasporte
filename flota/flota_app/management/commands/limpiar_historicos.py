from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from flota_app.models import GPSRegistro, Parada, RegistroSalida


class Command(BaseCommand):
    help = (
        "Limpia historicos operativos:\n"
        "- Cierra salidas activas de dias anteriores\n"
        "- Elimina paradas antiguas\n"
        "- Elimina GPS historico antiguo"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--gps-days",
            type=int,
            default=settings.GPS_RETENTION_DAYS,
            help="Dias de retencion para GPSRegistro",
        )
        parser.add_argument(
            "--paradas-days",
            type=int,
            default=settings.PARADAS_RETENTION_DAYS,
            help="Dias de retencion para Parada",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=settings.CLEANUP_BATCH_SIZE,
            help="Cantidad de registros GPS a eliminar por lote",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra que se eliminaria sin cambiar datos",
        )

    def handle(self, *args, **options):
        ahora = timezone.now()
        hoy = timezone.localdate()
        gps_days = max(options["gps_days"], 1)
        paradas_days = max(options["paradas_days"], 1)
        batch_size = max(options["batch_size"], 100)
        dry_run = options["dry_run"]

        self.stdout.write("Iniciando limpieza de historicos...")

        salidas_activas_antiguas = RegistroSalida.objects.filter(
            activo=True,
            fecha__lt=hoy,
        )
        total_salidas = salidas_activas_antiguas.count()
        self.stdout.write(
            f"Cerrando {total_salidas} salidas activas antiguas"
        )
        if not dry_run:
            salidas_activas_antiguas.update(activo=False, en_cola=False)

        limite_paradas = ahora - timedelta(days=paradas_days)
        paradas_antiguas = Parada.objects.filter(inicio__lt=limite_paradas)
        total_paradas = paradas_antiguas.count()
        self.stdout.write(
            f"Eliminando {total_paradas} paradas antiguas"
        )
        if not dry_run:
            paradas_antiguas.delete()

        limite_gps = ahora - timedelta(days=gps_days)
        total_gps = GPSRegistro.objects.filter(timestamp__lt=limite_gps).count()
        self.stdout.write(
            f"Eliminando {total_gps} registros GPS anteriores a {limite_gps:%Y-%m-%d %H:%M:%S}"
        )

        gps_eliminados = 0
        if not dry_run and total_gps:
            while True:
                ids = list(
                    GPSRegistro.objects.filter(timestamp__lt=limite_gps)
                    .order_by("timestamp")
                    .values_list("id", flat=True)[:batch_size]
                )
                if not ids:
                    break

                eliminados, _ = GPSRegistro.objects.filter(id__in=ids).delete()
                gps_eliminados += eliminados
                self.stdout.write(
                    f"Lote eliminado: {gps_eliminados}/{total_gps}"
                )

        resumen = (
            f"Salidas: {total_salidas}, "
            f"Paradas: {total_paradas}, "
            f"GPS: {total_gps if dry_run else gps_eliminados}"
        )
        if dry_run:
            resumen = f"Dry run completado. {resumen}"
        else:
            resumen = f"Limpieza completada correctamente. {resumen}"

        self.stdout.write(self.style.SUCCESS(resumen))
