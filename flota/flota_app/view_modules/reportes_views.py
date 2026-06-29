from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, time

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from ..decorators import empresa_required
from ..models import MarcacionPunto, Parada, PuntoControl, RegistroSalida, Vehiculo


def _clave_orden_vehiculo(vehiculo):
    codigo = str(vehiculo.codigo or vehiculo.numero or "").strip()
    if codigo.isdigit():
        return (0, int(codigo), codigo)
    return (1, codigo.upper(), codigo)




def _fecha_parametro(valor, respaldo):
    if valor:
        try:
            return date.fromisoformat(valor)
        except ValueError:
            return respaldo
    return respaldo


def _periodo_ranking(request):
    hoy = timezone.localdate()
    periodo = request.GET.get("periodo", "diario")
    if periodo not in {"diario", "mensual", "anual"}:
        periodo = "diario"

    fecha = _fecha_parametro(request.GET.get("fecha"), hoy)

    try:
        anio = int(request.GET.get("anio", fecha.year))
    except (TypeError, ValueError):
        anio = fecha.year

    try:
        mes = int(request.GET.get("mes", fecha.month))
    except (TypeError, ValueError):
        mes = fecha.month

    mes = min(12, max(1, mes))

    if periodo == "anual":
        inicio = date(anio, 1, 1)
        fin = date(anio, 12, 31)
        etiqueta = f"Anual {anio}"
    elif periodo == "mensual":
        ultimo_dia = monthrange(anio, mes)[1]
        inicio = date(anio, mes, 1)
        fin = date(anio, mes, ultimo_dia)
        etiqueta = f"Mensual {inicio.strftime('%m/%Y')}"
    else:
        inicio = fecha
        fin = fecha
        etiqueta = f"Diario {fecha.strftime('%d/%m/%Y')}"

    return periodo, anio, mes, fecha, inicio, fin, etiqueta


def _rango_datetime_local(inicio, fin):
    zona = timezone.get_current_timezone()
    desde = timezone.make_aware(datetime.combine(inicio, time.min), zona)
    hasta = timezone.make_aware(datetime.combine(fin, time.max), zona)
    return desde, hasta


def _resumen_salidas_periodo(empresa, inicio, fin):
    vehiculos = list(sorted(Vehiculo.objects.for_empresa(empresa), key=_clave_orden_vehiculo))
    salidas = list(
        RegistroSalida.objects.for_empresa(empresa)
        .filter(fecha__range=(inicio, fin))
        .select_related("vehiculo", "ruta")
        .order_by("vehiculo__codigo", "fecha", "hora_salida", "creado_en")
    )

    marcados_por_salida = defaultdict(int)
    estados_por_salida = defaultdict(lambda: defaultdict(int))
    for row in (
        MarcacionPunto.objects.filter(registro_salida__in=salidas)
        .values("registro_salida_id", "estado")
        .annotate(total=Count("id"), marcadas=Count("hora_marcada"))
    ):
        salida_id = row["registro_salida_id"]
        marcados_por_salida[salida_id] += row["marcadas"] or 0
        if row["estado"]:
            estados_por_salida[salida_id][row["estado"]] += row["marcadas"] or 0

    total_puntos_por_ruta = {
        row["ruta_id"]: row["total"]
        for row in PuntoControl.objects.for_empresa(empresa)
        .filter(activo=True, requiere_marcacion=True)
        .values("ruta_id")
        .annotate(total=Count("id"))
    }

    desde_dt, hasta_dt = _rango_datetime_local(inicio, fin)
    minutos_parada_por_vehiculo = defaultdict(int)

    for parada in Parada.objects.for_empresa(empresa).filter(
        es_prolongada=True,
        inicio__gte=desde_dt,
        inicio__lte=hasta_dt,
    ).only("vehiculo_id", "duracion_segundos"):
        minutos_parada_por_vehiculo[parada.vehiculo_id] += int((parada.duracion_segundos or 0) / 60)

    acumulado = {
        vehiculo.id: {
            "vehiculo": vehiculo,
            "codigo": vehiculo.codigo or vehiculo.numero,
            "placa": vehiculo.placa or "Sin placa",
            "vueltas_validas": 0,
            "anuladas": 0,
            "sin_hora": 0,
            "puntos_total": 0,
            "puntos_marcados": 0,
            "a_tiempo": 0,
            "tarde": 0,
            "adelantado": 0,
            "omitido": 0,
            "minutos_parada": minutos_parada_por_vehiculo.get(vehiculo.id, 0),
        }
        for vehiculo in vehiculos
    }

    for salida in salidas:
        total_puntos = total_puntos_por_ruta.get(salida.ruta_id, 0)
        puntos_marcados = marcados_por_salida.get(salida.id, 0)
        anulada = (
            not salida.activo
            and not salida.hora_real_salida
            and (total_puntos == 0 or puntos_marcados < total_puntos)
        )
        sin_hora = not salida.hora_salida and not anulada
        contable = not anulada and not sin_hora

        item = acumulado.get(salida.vehiculo_id)
        if not item:
            continue

        if anulada:
            item["anuladas"] += 1
            continue
        if sin_hora:
            item["sin_hora"] += 1
            continue
        if not contable:
            continue

        item["vueltas_validas"] += 1
        item["puntos_total"] += total_puntos
        item["puntos_marcados"] += puntos_marcados
        estados = estados_por_salida.get(salida.id, {})
        item["a_tiempo"] += estados.get("a_tiempo", 0)
        item["tarde"] += estados.get("tarde", 0)
        item["adelantado"] += estados.get("adelantado", 0)
        item["omitido"] += estados.get("omitido", 0)

    ranking = []
    for item in acumulado.values():
        puntos_total = item["puntos_total"]
        puntos_marcados = item["puntos_marcados"]
        marcacion = int((puntos_marcados / puntos_total) * 100) if puntos_total else 0
        evaluadas = item["a_tiempo"] + item["tarde"] + item["adelantado"] + item["omitido"]
        puntualidad = int((item["a_tiempo"] / evaluadas) * 100) if evaluadas else 0
        actividad = item["vueltas_validas"] + item["anuladas"] + item["sin_hora"]
        vueltas_score = min(100, item["vueltas_validas"] * 8)
        descuento_anuladas = min(25, item["anuladas"] * 5)
        descuento_paradas = min(20, item["minutos_parada"] // 5)
        puntaje = 0
        if actividad > 0:
            puntaje = round(
                (marcacion * 0.45)
                + (puntualidad * 0.25)
                + (vueltas_score * 0.20)
                + 10
                - descuento_anuladas
                - descuento_paradas,
                1,
            )
            puntaje = max(0, min(100, puntaje))
        item.update(
            {
                "marcacion": marcacion,
                "puntualidad": puntualidad,
                "puntaje": puntaje,
                "actividad": actividad,
            }
        )
        ranking.append(item)

    ranking.sort(key=lambda row: (-row["puntaje"], -row["vueltas_validas"], _clave_orden_vehiculo(row["vehiculo"])))
    for index, item in enumerate(ranking, start=1):
        item["puesto"] = index

    activos = [item for item in ranking if item["actividad"] > 0]
    resumen = {
        "unidades": len(ranking),
        "unidades_con_movimiento": len(activos),
        "vueltas_validas": sum(item["vueltas_validas"] for item in ranking),
        "anuladas": sum(item["anuladas"] for item in ranking),
        "promedio_puntaje": round(sum(item["puntaje"] for item in activos) / len(activos), 1) if activos else 0,
        "promedio_marcacion": int(sum(item["marcacion"] for item in activos) / len(activos)) if activos else 0,
    }
    return ranking, resumen

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




@login_required
@empresa_required
def ranking_unidades(request):
    empresa = request.empresa
    periodo, anio, mes, fecha, inicio, fin, etiqueta = _periodo_ranking(request)
    ranking, resumen = _resumen_salidas_periodo(empresa, inicio, fin)
    anios = list(range(timezone.localdate().year, timezone.localdate().year - 6, -1))
    meses = [
        (1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"),
        (5, "Mayo"), (6, "Junio"), (7, "Julio"), (8, "Agosto"),
        (9, "Setiembre"), (10, "Octubre"), (11, "Noviembre"), (12, "Diciembre"),
    ]

    return render(
        request,
        "reportes/ranking_unidades.html",
        {
            "periodo": periodo,
            "anio": anio,
            "mes": mes,
            "fecha": fecha,
            "anios": anios,
            "meses": meses,
            "etiqueta": etiqueta,
            "inicio": inicio,
            "fin": fin,
            "ranking": ranking,
            "resumen": resumen,
        },
    )

__all__ = ["_construir_reporte_salidas_diarias_contexto", "ranking_unidades", "reporte_salidas_diarias"]





