from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from flota_app.models import MarcacionPunto, RegistroSalida


class Command(BaseCommand):
    help = "Recalcula diferencia, estado y audio_flag de marcaciones ya registradas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fecha",
            type=str,
            default=None,
            help="Fecha operativa en formato YYYY-MM-DD. Por defecto usa hoy.",
        )
        parser.add_argument(
            "--salida-id",
            type=int,
            default=None,
            help="Recalcula solo una salida específica.",
        )

    def handle(self, *args, **options):
        fecha_raw = options.get("fecha")
        salida_id = options.get("salida_id")

        fecha_operativa = timezone.localdate()
        if fecha_raw:
            try:
                fecha_operativa = date.fromisoformat(fecha_raw)
            except ValueError as exc:
                raise CommandError("La fecha debe estar en formato YYYY-MM-DD.") from exc

        marcaciones_qs = MarcacionPunto.objects.filter(hora_marcada__isnull=False).select_related(
            "registro_salida", "punto"
        )

        if salida_id:
            salida = (
                RegistroSalida.objects.select_related("vehiculo", "ruta")
                .filter(id=salida_id)
                .first()
            )
            if not salida:
                raise CommandError(f"No existe la salida con id={salida_id}.")
            marcaciones_qs = marcaciones_qs.filter(registro_salida_id=salida_id)
            contexto = f"salida {salida_id} / unidad {salida.vehiculo.codigo}"
        else:
            marcaciones_qs = marcaciones_qs.filter(registro_salida__fecha=fecha_operativa)
            contexto = f"fecha {fecha_operativa.isoformat()}"

        total = 0
        for marcacion in marcaciones_qs:
            marcacion.save()
            total += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Recalculo completado para {total} marcaciones ({contexto})."
            )
        )
