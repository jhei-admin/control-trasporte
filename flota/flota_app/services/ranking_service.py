from ..models import RegistroSalida


def ranking_unidades(empresa):

    salidas = RegistroSalida.objects.filter(
        empresa=empresa,
        activo=False
    ).prefetch_related("marcaciones")

    ranking = []

    for salida in salidas:

        total = salida.marcaciones.count()

        if total == 0:
            continue

        score = 0

        for m in salida.marcaciones.all():
            if not m.hora_marcada:
                continue

            if m.diferencia_minutos is None:
                continue

            # 🎯 lógica de puntuación
            if abs(m.diferencia_minutos) <= 2:
                score += 2
            elif abs(m.diferencia_minutos) <= 5:
                score += 1

        ranking.append({
            "unidad": salida.vehiculo.codigo,
            "score": score
        })

    ranking.sort(key=lambda x: x["score"], reverse=True)

    return ranking[:10]