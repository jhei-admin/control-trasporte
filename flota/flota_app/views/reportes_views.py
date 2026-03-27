from datetime import date
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.contrib.auth.decorators import login_required

from ..models import (
    RegistroSalida,
    Vehiculo,
    PuntoControl,
    Parada,
)
from ..decorators import empresa_required


@login_required
@empresa_required
def reporte_salidas_diarias(request, vehiculo_id):

    fecha_param = request.GET.get("fecha")

    if fecha_param:
        try:
            fecha = date.fromisoformat(fecha_param)
        except ValueError:
            fecha = timezone.localdate()
    else:
        fecha = timezone.localdate()

    empresa = request.empresa

    vehiculo = get_object_or_404(
        Vehiculo,
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

    for index, salida in enumerate(salidas):

        total_puntos = PuntoControl.objects.for_empresa(empresa).filter(
            ruta=salida.ruta,
            activo=True
        ).count() if salida.ruta else 0

        puntos_marcados = salida.marcaciones.exclude(
            hora_marcada__isnull=True
        ).count()

        porcentaje = int(
            (puntos_marcados / total_puntos) * 100
        ) if total_puntos > 0 else 0

        resultado.append({
            "hora": salida.hora_salida,
            "ruta": salida.ruta.nombre if salida.ruta else "SIN RUTA",
            "porcentaje": porcentaje,
        })

    return render(
        request,
        "reportes/salidas_diarias.html",
        {
            "vehiculo": vehiculo,
            "vehiculos": vehiculos,
            "fecha": fecha,
            "salidas": resultado,
        }
    )