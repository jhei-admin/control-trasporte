def calcular_metricas_salida(salida):

    total = salida.marcaciones.count()
    marcados = salida.marcaciones.exclude(
        hora_marcada__isnull=True
    ).count()

    porcentaje = int((marcados / total) * 100) if total > 0 else 0

    minutos = sum([
        m.diferencia_minutos or 0
        for m in salida.marcaciones.all()
        if m.hora_marcada
    ])

    return {
        "porcentaje": porcentaje,
        "minutos": minutos,
        "total_puntos": total,
        "marcados": marcados
    }