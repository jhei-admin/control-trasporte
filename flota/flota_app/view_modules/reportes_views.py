from datetime import date

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from ..decorators import empresa_required
from ..models import Parada, PuntoControl, RegistroSalida, Vehiculo


def _clave_orden_vehiculo(vehiculo):
    codigo = str(vehiculo.codigo or vehiculo.numero or "").strip()
    if codigo.isdigit():
        return (0, int(codigo), codigo)
    return (1, codigo.upper(), codigo)


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

    vehiculos = sorted(Vehiculo.objects.for_empresa(empresa), key=_clave_orden_vehiculo)

    salidas = list(
        RegistroSalida.objects.for_empresa(empresa)
        .filter(
            vehiculo=vehiculo,
            fecha=fecha,
        )
        .order_by("hora_salida", "creado_en")
    )

    puntos_marcados_por_salida = {
        salida.id: salida.marcaciones.exclude(hora_marcada__isnull=True).count()
        for salida in salidas
    }
    total_puntos_por_ruta = {}

    def total_puntos_salida(salida):
        if not salida.ruta_id:
            return 0

        if salida.ruta_id not in total_puntos_por_ruta:
            total_puntos_por_ruta[salida.ruta_id] = PuntoControl.objects.for_empresa(empresa).filter(
                ruta=salida.ruta,
                activo=True,
                requiere_marcacion=True,
            ).count()

        return total_puntos_por_ruta[salida.ruta_id]

    def salida_anulada(salida):
        total_puntos = total_puntos_salida(salida)
        puntos_marcados = puntos_marcados_por_salida.get(salida.id, 0)

        return (
            not salida.activo
            and not salida.hora_real_salida
            and (total_puntos == 0 or puntos_marcados < total_puntos)
        )

    def salida_contable(salida):
        return not salida_anulada(salida) and bool(salida.hora_salida)

    salidas_validas = [salida for salida in salidas if salida_contable(salida)]
    siguiente_salida_valida = {
        salida.id: (
            salidas_validas[index + 1].hora_salida
            if index + 1 < len(salidas_validas)
            else None
        )
        for index, salida in enumerate(salidas_validas)
    }

    resultado = []
    vuelta_actual = 0

    for salida in salidas:
        anulada = salida_anulada(salida)
        sin_hora = not salida.hora_salida and not anulada
        contable = not anulada and not sin_hora
        if not contable:
            vuelta = None
        else:
            vuelta_actual += 1
            vuelta = vuelta_actual

        total_puntos = total_puntos_salida(salida)
        puntos_marcados = puntos_marcados_por_salida.get(salida.id, 0)

        porcentaje = (
            int((puntos_marcados / total_puntos) * 100)
            if total_puntos > 0
            else 0
        )

        inicio = salida.hora_salida
        fin = siguiente_salida_valida.get(salida.id) or salida.hora_real_salida or timezone.now()

        minutos = 0

        if inicio and fin and contable:
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
                "anulada": anulada,
                "sin_hora": sin_hora,
                "contable": contable,
                "porcentaje": porcentaje,
                "minutos": minutos,
                "salida_id": salida.id,
            }
        )

    salidas_contables = [salida for salida in resultado if salida["contable"]]
    total_vueltas = len(salidas_contables)
    promedio_marcacion = (
        int(sum(s["porcentaje"] for s in salidas_contables) / total_vueltas)
        if total_vueltas > 0
        else 0
    )
    minutos_totales = sum(s["minutos"] for s in salidas_contables)

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
