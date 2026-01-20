from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from flota_app.models import RegistroSalida, Parada


class Command(BaseCommand):
    help = (
        "Limpia datos antiguos e inconsistentes:\n"
        "- Cierra salidas activas de días anteriores\n"
        "- Elimina paradas antiguas"
    )

    def handle(self, *args, **options):
        ahora = timezone.now()
        hoy = timezone.localdate()

        self.stdout.write("🧹 Iniciando limpieza de datos...")

        # =================================================
        # 1️⃣ CERRAR SALIDAS ACTIVAS ANTIGUAS
        # =================================================
        salidas_activas_antiguas = RegistroSalida.objects.filter(
            activo=True,
            fecha__lt=hoy
        )

        total_salidas = salidas_activas_antiguas.count()

        self.stdout.write(
            f"➡️ Cerrando {total_salidas} salidas activas antiguas"
        )

        for salida in salidas_activas_antiguas:
            salida.activo = False
            salida.en_cola = False
            salida.save(update_fields=["activo", "en_cola"])

        # =================================================
        # 2️⃣ ELIMINAR PARADAS ANTIGUAS
        # =================================================
        limite = ahora - timedelta(days=2)

        paradas_antiguas = Parada.objects.filter(
            inicio__lt=limite
        )

        total_paradas = paradas_antiguas.count()

        self.stdout.write(
            f"➡️ Eliminando {total_paradas} paradas antiguas"
        )

        paradas_antiguas.delete()

        # =================================================
        # ✅ FIN
        # =================================================
        self.stdout.write(
            self.style.SUCCESS(
                "✅ Limpieza completada correctamente"
            )
        )
