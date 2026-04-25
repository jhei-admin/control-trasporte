from datetime import date

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from ..decorators import empresa_required
from ..models import Parada, PuntoControl, RegistroSalida, Vehiculo


def _construir_reporte_salidas_diarias_contexto(empresa, vehiculo_id, fecha_param):
    if fecha_param:
        try:
            fecha = date.fromisoformat(fecha_param)
        except ValueError:
            fecha = timezone.localdate()
    else:
        fecha = timezone.localdate()

    vehiculo = get_object_or_404(
        Vehiculo,
        id=vehiculo_id,
        empresa=empresa,
    )

    vehiculos = Vehiculo.objects.for_empresa(empresa)

    salidas = list(
        RegistroSalida.objects.for_empresa(empresa)
        .filter(
            vehiculo=vehiculo,
            fecha=fecha,
        )
        .order_by("hora_salida", "creado_en")
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
                activo=True,
                requiere_marcacion=True,
            ).count()

        puntos_marcados = salida.marcaciones.exclude(
            hora_marcada__isnull=True
        ).count()

        porcentaje = (
            int((puntos_marcados / total_puntos) * 100)
            if total_puntos > 0
            else 0
        )

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
                inicio__lt=fin,
            )

            for parada in paradas:
                minutos += int(parada.duracion_segundos / 60)

        resultado.append(
            {
                "hora": salida.hora_salida,
                "ruta": salida.ruta.nombre if salida.ruta else "SIN RUTA",
                "vuelta": vuelta,
                "porcentaje": porcentaje,
                "minutos": minutos,
                "salida_id": salida.id,
            }
        )

    total_vueltas = len(resultado)
    promedio_marcacion = (
        int(sum(s["porcentaje"] for s in resultado) / total_vueltas)
        if total_vueltas > 0
        else 0
    )
    minutos_totales = sum(s["minutos"] for s in resultado)

    alertas = []
    if total_vueltas > 0 and promedio_marcacion < 90:
        alertas.append("Marcacion promedio baja")
    if minutos_totales > 15:
        alertas.append("Exceso de minutos por paradas prolongadas")

    return {
        "vehiculo": vehiculo,
        "vehiculos": vehiculos,
        "fecha": fecha,
        "salidas": resultado,
        "total_vueltas": total_vueltas,
        "promedio_marcacion": promedio_marcacion,
        "minutos_totales": minutos_totales,
        "alertas": alertas,
    }


@login_required
@empresa_required
def reporte_salidas_diarias(request, vehiculo_id):
    empresa = request.empresa
    context = _construir_reporte_salidas_diarias_contexto(
        empresa=empresa,
        vehiculo_id=vehiculo_id,
        fecha_param=request.GET.get("fecha"),
    )

    return render(
        request,
        "reportes/salidas_diarias.html",
        context,
    )


__all__ = ["_construir_reporte_salidas_diarias_contexto", "reporte_salidas_diarias"]
