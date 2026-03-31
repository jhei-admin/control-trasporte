from django.core.management.base import BaseCommand, CommandError

from flota_app.models import Ruta
from flota_app.route_geometry import GEOMETRIA_ZAMACOLA


class Command(BaseCommand):
    help = "Carga la geometria actual de ZAMACOLA en Ruta.geometria"

    def add_arguments(self, parser):
        parser.add_argument(
            "--ruta-id",
            type=int,
            help="ID de la ruta a actualizar",
        )
        parser.add_argument(
            "--empresa-id",
            type=int,
            help="Empresa a la que pertenece la ruta",
        )
        parser.add_argument(
            "--forzar",
            action="store_true",
            help="Sobrescribe la geometria aunque ya exista",
        )

    def handle(self, *args, **options):
        ruta_id = options.get("ruta_id")
        empresa_id = options.get("empresa_id")
        forzar = options.get("forzar", False)

        rutas = Ruta.objects.all()
        if empresa_id:
            rutas = rutas.filter(empresa_id=empresa_id)

        if ruta_id:
            ruta = rutas.filter(id=ruta_id).first()
        else:
            ruta = rutas.filter(nombre__iexact="ZAMACOLA").order_by("id").first()

        if not ruta:
            raise CommandError("No se encontro la ruta ZAMACOLA con los filtros indicados.")

        if ruta.geometria and not forzar:
            self.stdout.write(
                self.style.WARNING(
                    f"La ruta {ruta.nombre} ya tiene geometria. Usa --forzar si deseas reemplazarla."
                )
            )
            return

        ruta.geometria = GEOMETRIA_ZAMACOLA
        ruta.save(update_fields=["geometria"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Geometria cargada correctamente en la ruta {ruta.nombre} (id={ruta.id})."
            )
        )
