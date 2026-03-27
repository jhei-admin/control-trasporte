from django.utils import timezone
from datetime import date
from ..models import RegistroSalida, PuntoControl, Parada, Vehiculo


def get_reporte_salidas(empresa, vehiculo_id, fecha):

    vehiculo = Vehiculo.objects.get(
        id=vehiculo_id,
        empresa=empresa
    )

    vehiculos = Vehiculo.objects.for_empresa(empresa)

    salidas = list(
        RegistroSalida.objects.for_empresa(empresa).filter(
            vehiculo=vehiculo,
            fecha=fecha
        ).order_by("hora_salida", "creado_en")
    )

    resultado = []
    total_salidas = len(salidas)

    for index, salida in enumerate(salidas):

        vuelta = index + 1

        if not salida.ruta:
            total_puntos = 0
        else:
            total_puntos = PuntoControl.objects.for_empresa(empresa).filter(
                ruta=salida.ruta,
                activo=True
            ).count()

        puntos_marcados = salida.marcaciones.exclude(
            hora_marcada__isnull=True
        ).count()

        porcentaje = int(
            (puntos_marcados / total_puntos) * 100
        ) if total_puntos > 0 else 0

        inicio = salida.hora_salida

        if index + 1 < total_salidas:
            fin = salidas[index + 1].hora_salida
        else:
            fin = salida.hora_real_salida or timezone.now()

        minutos = 0

        if inicio and fin:
            paradas = Parada.objects.for_empresa(empresa).filter(
                vehiculo=vehiculo,
                es_prolongada=True,
                inicio__gte=inicio,
                inicio__lt=fin
            )

            for p in paradas:
                minutos += int(p.duracion_segundos / 60)

        resultado.append({
            "hora": salida.hora_salida,
            "ruta": salida.ruta.nombre if salida.ruta else "SIN RUTA",
            "vuelta": vuelta,
            "porcentaje": porcentaje,
            "minutos": minutos,
            "salida_id": salida.id,
        })

    total_vueltas = len(resultado)

    promedio_marcacion = (
        int(sum(s["porcentaje"] for s in resultado) / total_vueltas)
        if total_vueltas > 0 else 0
    )

    minutos_totales = sum(s["minutos"] for s in resultado)

    alertas = []

    if total_vueltas > 0 and promedio_marcacion < 90:
        alertas.append("Marcación promedio baja")

    if minutos_totales > 15:
        alertas.append("Exceso de minutos por paradas prolongadas")

    return {
        "vehiculo": vehiculo,
        "vehiculos": vehiculos,
        "salidas": resultado,
        "total_vueltas": total_vueltas,
        "promedio_marcacion": promedio_marcacion,
        "minutos_totales": minutos_totales,
        "alertas": alertas,
    }