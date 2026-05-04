from datetime import datetime, timedelta
import math

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from flota_app.models import GPSRegistro, MarcacionPunto, RegistroSalida, Ruta, SesionUnidad
from flota_app.utils import distancia_metros
from flota_app.view_modules.api_views import (
    _construir_audio_texto_marcacion,
    _es_punto_evento_confirmado,
    _obtener_punto_siguiente,
    _serializar_puntos_ruta,
)


POINT_LABELS = {
    "SALI": "Salida",
    "COLE": "Colegio",
    "APIP": "Entrada Apipa",
    "ZAMA": "Zamacola",
    "PESQ": "Pesquero",
    "PLAZ": "Plaza Norte",
    "LLAM": "Llamagas",
    "MUNI": "Entrada Municipal",
}


class Command(BaseCommand):
    help = (
        "Reconstruye los audios esperados por unidad para una ruta y fecha "
        "usando marcaciones y GPS historico reales."
    )

    def add_arguments(self, parser):
        parser.add_argument("--fecha", required=True, help="Fecha en formato YYYY-MM-DD.")
        parser.add_argument(
            "--ruta",
            required=True,
            help="Nombre exacto de la ruta, por ejemplo: Ruta A o Ruta Zamacola.",
        )
        parser.add_argument(
            "--unidades",
            required=True,
            help="Lista de codigos separada por coma, por ejemplo: 01,31,35",
        )
        parser.add_argument(
            "--empresa",
            help="Nombre exacto de la empresa para filtrar la ruta si hace falta.",
        )

    def handle(self, *args, **options):
        try:
            fecha = datetime.strptime(options["fecha"], "%Y-%m-%d").date()
        except ValueError as exc:
            raise CommandError("La fecha debe ir en formato YYYY-MM-DD.") from exc

        ruta_nombre = (options["ruta"] or "").strip()
        if not ruta_nombre:
            raise CommandError("Debes indicar --ruta.")

        unidad_codigos = [
            item.strip()
            for item in (options["unidades"] or "").split(",")
            if item.strip()
        ]
        if not unidad_codigos:
            raise CommandError("Debes indicar al menos una unidad en --unidades.")

        rutas_qs = Ruta.objects.filter(nombre=ruta_nombre)
        empresa_nombre = (options.get("empresa") or "").strip()
        if empresa_nombre:
            rutas_qs = rutas_qs.filter(empresa__nombre=empresa_nombre)

        ruta = rutas_qs.select_related("empresa").first()
        if not ruta:
            raise CommandError("No se encontro la ruta indicada.")

        salidas = list(
            RegistroSalida.objects.select_related("vehiculo", "ruta")
            .filter(
                ruta=ruta,
                fecha=fecha,
                vehiculo__codigo__in=unidad_codigos,
            )
            .order_by("hora_salida", "vehiculo__codigo")
        )
        if len(salidas) != len(unidad_codigos):
            encontrados = {salida.vehiculo.codigo for salida in salidas}
            faltantes = [codigo for codigo in unidad_codigos if codigo not in encontrados]
            raise CommandError(
                "Faltan salidas para estas unidades en esa fecha/ruta: "
                + ", ".join(faltantes)
            )

        sesiones_map = {
            sesion.vehiculo_id: sesion
            for sesion in SesionUnidad.objects.select_related("vehiculo").filter(
                vehiculo_id__in=[salida.vehiculo_id for salida in salidas]
            )
        }
        gps_por_sesion = {}
        for sesion in sesiones_map.values():
            gps_por_sesion[sesion.id] = list(
                GPSRegistro.objects.filter(sesion=sesion)
                .order_by("timestamp")
                .only("lat", "lng", "velocidad", "precision", "timestamp")
            )

        marcaciones_por_salida = {}
        eventos = []
        for salida in salidas:
            marcaciones = list(
                MarcacionPunto.objects.filter(
                    registro_salida=salida,
                    hora_marcada__isnull=False,
                )
                .select_related("punto", "registro_salida__vehiculo")
                .order_by("hora_marcada", "punto__orden")
            )
            marcaciones_por_salida[salida.id] = marcaciones
            for marcacion in marcaciones:
                eventos.append(marcacion)

        if not eventos:
            raise CommandError("No hay marcaciones registradas para esas unidades en esa fecha.")

        eventos.sort(key=lambda item: (item.hora_marcada, item.registro_salida.vehiculo.codigo))

        puntos_ruta = _serializar_puntos_ruta(ruta)
        puntos_marcacion = [punto for punto in puntos_ruta if punto["requiere_marcacion"]]
        puntos_evento = [punto for punto in puntos_ruta if _es_punto_evento_confirmado(punto)]
        puntos_por_codigo = {punto["codigo"]: punto for punto in puntos_evento}
        gps_max_delay = timedelta(seconds=60)
        velocidad_promedio = 25

        self.stdout.write(
            self.style.SUCCESS(
                f"Replay de audios | fecha={fecha} | ruta={ruta.nombre} | unidades={', '.join(unidad_codigos)}"
            )
        )
        self.stdout.write("")

        def gps_en_instante(salida, instante):
            sesion = sesiones_map.get(salida.vehiculo_id)
            if not sesion:
                return None
            registros = gps_por_sesion.get(sesion.id, [])
            ultimo = None
            for registro in registros:
                if registro.timestamp <= instante:
                    ultimo = registro
                else:
                    break
            return ultimo

        def ultimo_marcado_hasta(salida, instante):
            ultimo = None
            for marcacion in marcaciones_por_salida.get(salida.id, []):
                if marcacion.hora_marcada and marcacion.hora_marcada <= instante:
                    ultimo = marcacion
                else:
                    break
            return ultimo

        def punto_evento_en_gps(gps, orden_minimo):
            if not gps:
                return None

            candidatos = []
            for punto in puntos_evento:
                if punto["orden"] < max(orden_minimo, 1):
                    continue
                distancia = distancia_metros(
                    gps.lat,
                    gps.lng,
                    punto["lat"],
                    punto["lng"],
                )
                if distancia <= punto["radio"]:
                    candidatos.append((punto["orden"], distancia, punto))

            if not candidatos:
                return None

            _, _, punto = max(candidatos, key=lambda item: (item[0], -item[1]))
            return punto

        def referencia_actual(salida, instante, estado_eventos):
            ultimo = ultimo_marcado_hasta(salida, instante)
            ultimo_codigo = ultimo.punto.codigo if ultimo else None
            ultimo_orden = ultimo.punto.orden if ultimo else 0
            gps = gps_en_instante(salida, instante)

            referencia_codigo = ultimo_codigo
            referencia_orden = ultimo_orden
            evento_confirmado = estado_eventos.get(salida.id, {})
            if (evento_confirmado.get("orden") or 0) > referencia_orden:
                referencia_orden = evento_confirmado["orden"]
                referencia_codigo = evento_confirmado.get("codigo")

            return {
                "codigo": referencia_codigo,
                "audio_codigo": referencia_codigo,
                "orden_confirmado": referencia_orden,
                "gps": gps,
                "ultimo_codigo": ultimo_codigo,
                "ultimo_orden": ultimo_orden,
            }

        def progreso_clave(salida, instante, estado_eventos):
            referencia = referencia_actual(salida, instante, estado_eventos)
            referencia_orden = referencia["orden_confirmado"] or 0
            hora_base = salida.hora_real_salida or salida.hora_salida or salida.hora_llegada
            hora_key = -float(hora_base.timestamp()) if hora_base else 0.0
            if not salida.hora_real_salida or salida.hora_real_salida > instante:
                hora_programada = salida.hora_salida or salida.hora_llegada
                programada_key = -float(hora_programada.timestamp()) if hora_programada else 0.0
                return (-1, 0.0, programada_key)

            return (1, float(referencia_orden), hora_key)

        def minutos_entre(origen, destino, instante, estado_eventos):
            ref_origen = referencia_actual(origen, instante, estado_eventos)
            ref_destino = referencia_actual(destino, instante, estado_eventos)
            gps_origen = ref_origen["gps"]
            gps_destino = ref_destino["gps"]
            if not gps_origen or not gps_destino:
                return None
            if instante - gps_origen.timestamp > gps_max_delay:
                return None
            if instante - gps_destino.timestamp > gps_max_delay:
                return None
            distancia = distancia_metros(gps_origen.lat, gps_origen.lng, gps_destino.lat, gps_destino.lng)
            velocidad = gps_destino.velocidad or velocidad_promedio
            metros_min = (velocidad * 1000) / 60
            if metros_min <= 0:
                return None
            return max(int(round(distancia / metros_min)), 0)

        def label_punto(codigo):
            if not codigo:
                return "Punto"
            return POINT_LABELS.get(codigo, codigo.title())

        def texto_estado(marcacion):
            hora = timezone.localtime(marcacion.hora_marcada).strftime("%H:%M") if marcacion.hora_marcada else None
            if marcacion.estado == "a_tiempo":
                return f"{label_punto(marcacion.punto.codigo)}, {hora}, en hora."
            if marcacion.estado == "adelantado":
                return f"{label_punto(marcacion.punto.codigo)}, {hora}, adelantado, menos {abs(marcacion.diferencia_minutos or 0)}."
            if marcacion.estado == "tarde":
                return f"{label_punto(marcacion.punto.codigo)}, {hora}, tarde, mas {abs(marcacion.diferencia_minutos or 0)}."
            return f"{label_punto(marcacion.punto.codigo)}, {hora}."

        def texto_relativo(unidad_codigo, label, minutos):
            if not unidad_codigo or minutos is None:
                return None
            sufijo = "minuto" if minutos == 1 else "minutos"
            return f"Unidad {unidad_codigo} {label} a {minutos} {sufijo}."

        eventos_normalizados = []
        for evento in eventos:
            finalizada = _obtener_punto_siguiente(
                puntos_marcacion,
                evento.punto.orden,
            ) is None
            eventos_normalizados.append({
                "tipo": "marcacion",
                "instante": evento.hora_marcada,
                "salida": evento.registro_salida,
                "marcacion": evento,
                "finalizada": finalizada,
            })

        estado_eventos_detectados = {
            salida.id: {"codigo": None, "orden": 0}
            for salida in salidas
        }
        for salida in salidas:
            sesion = sesiones_map.get(salida.vehiculo_id)
            if not sesion:
                continue
            registros = gps_por_sesion.get(sesion.id, [])
            for registro in registros:
                instante = registro.timestamp
                if not salida.hora_real_salida or salida.hora_real_salida > instante:
                    continue

                ultimo_marcado = ultimo_marcado_hasta(salida, instante)
                ultimo_codigo = ultimo_marcado.punto.codigo if ultimo_marcado else None
                ultimo_orden = ultimo_marcado.punto.orden if ultimo_marcado else 0
                punto_evento = punto_evento_en_gps(registro, ultimo_orden)
                if not punto_evento:
                    continue

                codigo_actual = punto_evento["codigo"]
                orden_actual = punto_evento["orden"]
                estado_previo = estado_eventos_detectados[salida.id]
                if orden_actual <= max(ultimo_orden, estado_previo["orden"]):
                    continue

                estado_eventos_detectados[salida.id] = {
                    "codigo": codigo_actual,
                    "orden": orden_actual,
                }

                if punto_evento["requiere_marcacion"] or codigo_actual == ultimo_codigo:
                    continue

                siguiente_pendiente = _obtener_punto_siguiente(puntos_marcacion, ultimo_orden)
                orden_esperado = siguiente_pendiente["orden"] if siguiente_pendiente else math.inf

                if orden_actual <= orden_esperado:
                    continue

                eventos_normalizados.append({
                    "tipo": "bloqueado",
                    "instante": instante,
                    "salida": salida,
                    "codigo": codigo_actual,
                    "orden": orden_actual,
                })

        eventos_normalizados.sort(
            key=lambda item: (
                item["instante"],
                0 if item["tipo"] == "marcacion" else 1,
                item["salida"].vehiculo.codigo,
            )
        )

        estado_eventos_confirmados = {
            salida.id: {"codigo": None, "orden": 0}
            for salida in salidas
        }
        for evento in eventos_normalizados:
            instante = evento["instante"]
            salida_actual = evento["salida"]
            if evento["tipo"] == "marcacion":
                estado_eventos_confirmados[salida_actual.id] = {
                    "codigo": evento["marcacion"].punto.codigo,
                    "orden": evento["marcacion"].punto.orden,
                }
            else:
                estado_eventos_confirmados[salida_actual.id] = {
                    "codigo": evento["codigo"],
                    "orden": evento["orden"],
                }

            ordenadas = sorted(
                salidas,
                key=lambda salida: progreso_clave(salida, instante, estado_eventos_confirmados),
                reverse=True,
            )
            indice = ordenadas.index(salida_actual)
            adelante = ordenadas[:indice][-1] if indice > 0 else None
            atras = ordenadas[indice + 1] if indice + 1 < len(ordenadas) else None

            if evento["tipo"] == "marcacion":
                if evento.get("finalizada"):
                    partes = [
                        _construir_audio_texto_marcacion(
                            evento["marcacion"],
                            finalizada=True,
                        ).replace(". RUTA FINALIZADA. BUEN TRABAJO", ". Ruta finalizada. Buen trabajo.")
                    ]
                else:
                    partes = [texto_estado(evento["marcacion"])]
                titulo = (
                    f"[{timezone.localtime(instante).strftime('%H:%M:%S')}] "
                    f"Unidad {salida_actual.vehiculo.codigo} marca {evento['marcacion'].punto.codigo}"
                )
            else:
                partes = [f"{label_punto(evento['codigo'])}."]
                titulo = (
                    f"[{timezone.localtime(instante).strftime('%H:%M:%S')}] "
                    f"Unidad {salida_actual.vehiculo.codigo} toca radio bloqueado {evento['codigo']}"
                )
            if adelante and not evento.get("finalizada"):
                partes.append(
                    texto_relativo(
                        adelante.vehiculo.codigo,
                        "adelante",
                        minutos_entre(salida_actual, adelante, instante, estado_eventos_confirmados),
                    )
                )
            if atras and not evento.get("finalizada"):
                partes.append(
                    texto_relativo(
                        atras.vehiculo.codigo,
                        "atras",
                        minutos_entre(salida_actual, atras, instante, estado_eventos_confirmados),
                    )
                )

            self.stdout.write(titulo)
            self.stdout.write(f"  {salida_actual.vehiculo.codigo} oye: {' '.join([p for p in partes if p])}")

            if adelante and not evento.get("finalizada"):
                self.stdout.write(
                    "  "
                    f"{adelante.vehiculo.codigo} oye: "
                    f"{texto_relativo(salida_actual.vehiculo.codigo, 'atras', minutos_entre(adelante, salida_actual, instante, estado_eventos_confirmados))}"
                )
            if atras and not evento.get("finalizada"):
                self.stdout.write(
                    "  "
                    f"{atras.vehiculo.codigo} oye: "
                    f"{texto_relativo(salida_actual.vehiculo.codigo, 'adelante', minutos_entre(atras, salida_actual, instante, estado_eventos_confirmados))}"
                )
            self.stdout.write("")
