from django.utils import timezone

def evaluar_marcacion(marcacion):

    if not marcacion.hora_programada or not marcacion.hora_marcada:
        return None

    diff = int(
        (marcacion.hora_marcada - marcacion.hora_programada)
        .total_seconds() / 60
    )

    if diff < -2:
        estado = "ADELANTADO"
    elif diff <= 2:
        estado = "A_TIEMPO"
    else:
        estado = "TARDE"

    return {
        "estado": estado,
        "diferencia": diff
    }