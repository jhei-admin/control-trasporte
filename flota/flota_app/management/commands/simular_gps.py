from datetime import timedelta
import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from flota_app.models import GPSRegistro, PuntoControl, SesionUnidad, UbicacionVehiculo


class Command(BaseCommand):
    help = (
        "Simula trafico GPS para sesiones activas sembrando heartbeat, "
        "ubicacion actual e historico en varias iteraciones."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--prefijo",
            default="Escala",
            help="Prefijo de empresas sembradas con poblar_escala.",
        )
        parser.add_argument(
            "--empresas",
            type=int,
            default=2,
            help="Cantidad de empresas a incluir desde el prefijo dado.",
        )
        parser.add_argument(
            "--unidades",
            type=int,
            default=70,
            help="Maximo de unidades activas por empresa a simular.",
        )
        parser.add_argument(
            "--iteraciones",
            type=int,
            default=12,
            help="Cantidad de pasos de simulacion por unidad.",
        )
        parser.add_argument(
            "--interval-seconds",
            type=int,
            default=5,
            help="Separacion simulada entre iteraciones.",
        )
        parser.add_argument(
            "--sin-historico",
            action="store_true",
            help="Actualiza solo ubicacion actual y heartbeat sin guardar GPS historico.",
        )
        parser.add_argument(
            "--sleep-real",
            action="store_true",
            help="Espera realmente entre iteraciones para pruebas prolongadas.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=200,
            help="Tamano de lote para inserts y updates masivos.",
        )

    def handle(self, *args, **options):
        prefijo = options["prefijo"].strip() or "Escala"
        total_empresas = max(options["empresas"], 1)
        max_unidades = max(options["unidades"], 1)
        iteraciones = max(options["iteraciones"], 1)
        interval_seconds = max(options["interval_seconds"], 1)
        guardar_historico = not options["sin_historico"]
        sleep_real = options["sleep_real"]
        batch_size = max(options["batch_size"], 1)

        base_time = timezone.now()
        resumen = {
            "sesiones": 0,
            "iteraciones": 0,
            "ubicaciones": 0,
            "gps": 0,
            "heartbeats": 0,
        }

        self.stdout.write(
            "Iniciando simulacion GPS "
            f"({total_empresas} empresas, {max_unidades} unidades, {iteraciones} iteraciones)..."
        )

        sesiones = self._obtener_sesiones(
            prefijo=prefijo,
            total_empresas=total_empresas,
            max_unidades=max_unidades,
        )
        if not sesiones:
            self.stdout.write(
                self.style.WARNING(
                    "No se encontraron sesiones activas para el prefijo indicado."
                )
            )
            return

        resumen["sesiones"] = len(sesiones)
        rutas_cache = self._cargar_puntos_por_ruta(sesiones)
        ubicaciones_existentes = self._cargar_ubicaciones_existentes(sesiones)

        for iteracion in range(iteraciones):
            instante = base_time + timedelta(seconds=iteracion * interval_seconds)
            ubicaciones_nuevas = []
            ubicaciones_actualizar = []
            sesiones_actualizar = []
            gps_registros = []

            for indice, sesion in enumerate(sesiones):
                salida = sesion.salida
                if salida is None:
                    continue

                lat, lng, velocidad, precision = self._calcular_posicion(
                    salida=salida,
                    paso=iteracion,
                    offset=indice,
                    rutas_cache=rutas_cache,
                )

                ubicacion = ubicaciones_existentes.get(sesion.vehiculo_id)
                if ubicacion is None:
                    ubicacion = UbicacionVehiculo(
                        vehiculo=sesion.vehiculo,
                        latitud=lat,
                        longitud=lng,
                        velocidad=velocidad,
                        precision=precision,
                        updated_at=instante,
                    )
                    ubicaciones_existentes[sesion.vehiculo_id] = ubicacion
                    ubicaciones_nuevas.append(ubicacion)
                else:
                    ubicacion.latitud = lat
                    ubicacion.longitud = lng
                    ubicacion.velocidad = velocidad
                    ubicacion.precision = precision
                    ubicacion.updated_at = instante
                    ubicaciones_actualizar.append(ubicacion)

                sesion.last_heartbeat = instante
                sesiones_actualizar.append(sesion)

                if guardar_historico:
                    gps_registros.append(
                        GPSRegistro(
                            sesion=sesion,
                            lat=lat,
                            lng=lng,
                            velocidad=velocidad,
                            precision=precision,
                            bateria=80 - (iteracion % 15),
                            timestamp=instante,
                        )
                    )
                    resumen["gps"] += 1

                resumen["ubicaciones"] += 1
                resumen["heartbeats"] += 1
                resumen["iteraciones"] += 1

            with transaction.atomic():
                if ubicaciones_nuevas:
                    UbicacionVehiculo.objects.bulk_create(
                        ubicaciones_nuevas,
                        batch_size=batch_size,
                    )
                if ubicaciones_actualizar:
                    UbicacionVehiculo.objects.bulk_update(
                        ubicaciones_actualizar,
                        ["latitud", "longitud", "velocidad", "precision", "updated_at"],
                        batch_size=batch_size,
                    )
                if sesiones_actualizar:
                    SesionUnidad.objects.bulk_update(
                        sesiones_actualizar,
                        ["last_heartbeat"],
                        batch_size=batch_size,
                    )
                if gps_registros:
                    GPSRegistro.objects.bulk_create(
                        gps_registros,
                        batch_size=batch_size,
                    )

            if sleep_real and iteracion + 1 < iteraciones:
                time.sleep(interval_seconds)

        self.stdout.write(self.style.SUCCESS("Simulacion GPS completada"))
        for clave, valor in resumen.items():
            self.stdout.write(f"{clave.capitalize()}: {valor}")

    def _obtener_sesiones(self, prefijo, total_empresas, max_unidades):
        sesiones = []
        hoy = timezone.localdate()

        for indice_empresa in range(1, total_empresas + 1):
            empresa_nombre = f"{prefijo} Empresa {indice_empresa:02d}"
            qs = (
                SesionUnidad.objects.select_related("vehiculo", "salida", "vehiculo__empresa")
                .filter(
                    activa=True,
                    vehiculo__empresa__nombre=empresa_nombre,
                    salida__fecha=hoy,
                    salida__activo=True,
                )
                .order_by("vehiculo__codigo")[:max_unidades]
            )
            sesiones.extend(list(qs))

        return sesiones

    def _cargar_puntos_por_ruta(self, sesiones):
        ruta_ids = {
            sesion.salida.ruta_id
            for sesion in sesiones
            if sesion.salida is not None and sesion.salida.ruta_id is not None
        }
        puntos_por_ruta = {ruta_id: [] for ruta_id in ruta_ids}

        if not ruta_ids:
            return puntos_por_ruta

        puntos = (
            PuntoControl.objects.filter(ruta_id__in=ruta_ids, activo=True)
            .order_by("ruta_id", "orden")
        )
        for punto in puntos:
            puntos_por_ruta.setdefault(punto.ruta_id, []).append(punto)

        return puntos_por_ruta

    def _cargar_ubicaciones_existentes(self, sesiones):
        vehiculo_ids = [sesion.vehiculo_id for sesion in sesiones]
        if not vehiculo_ids:
            return {}

        return {
            ubicacion.vehiculo_id: ubicacion
            for ubicacion in UbicacionVehiculo.objects.filter(vehiculo_id__in=vehiculo_ids)
        }

    def _calcular_posicion(self, salida, paso, offset, rutas_cache):
        puntos = rutas_cache.get(salida.ruta_id, [])
        if puntos:
            punto = puntos[(paso + offset) % len(puntos)]
            lat = float(punto.latitud) + ((offset % 3) * 0.00002)
            lng = float(punto.longitud) + ((paso % 3) * 0.00002)
        else:
            lat = -16.39 + (offset * 0.0001)
            lng = -71.52 + (paso * 0.0001)

        velocidad = 12 + ((paso + offset) % 24)
        precision = 6 + ((paso + offset) % 5)
        return lat, lng, velocidad, precision
