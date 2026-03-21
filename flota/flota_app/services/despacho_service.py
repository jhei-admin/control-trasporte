# flota_app/services/despacho_service.py

from ..models import RegistroSalida


def iniciar_salida_segura(*args, **kwargs):
    # Puedes dejarlo vacío por ahora si ya lo tienes en otro lado
    pass


def recalcular_cola(empresa):
    salidas = (
        RegistroSalida.objects
        .filter(
            empresa=empresa,
            activo=True,
            en_cola=True
        )
        .order_by("hora_salida")
    )

    for i, salida in enumerate(salidas, start=1):
        salida.orden_cola = i
        salida.save(update_fields=["orden_cola"])