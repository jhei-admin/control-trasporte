from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from flota_app.models import (
    ConfiguracionDespacho,
    GPSRegistro,
    Empresa,
    MarcacionPunto,
    PerfilUsuario,
    PuntoControl,
    RegistroSalida,
    Ruta,
    SesionUnidad,
    UbicacionVehiculo,
    Vehiculo,
)
from flota_app.services import recalcular_cola


class Command(BaseCommand):
    help = (
        "Genera datos de prueba para validar escala multiempresa con rutas, "
        "unidades, salidas, sesiones y GPS simulado."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--prefijo",
            default="Escala",
            help="Prefijo visible para empresas, rutas y usuarios de prueba.",
        )
        parser.add_argument(
            "--empresas",
            type=int,
            default=2,
            help="Cantidad de empresas a crear.",
        )
        parser.add_argument(
            "--rutas",
            type=int,
            default=3,
            help="Cantidad de rutas por empresa.",
        )
        parser.add_argument(
            "--puntos",
            type=int,
            default=10,
            help="Cantidad de puntos de control por ruta.",
        )
        parser.add_argument(
            "--unidades",
            type=int,
            default=70,
            help="Cantidad de unidades activas por empresa.",
        )
        parser.add_argument(
            "--historico-gps",
            type=int,
            default=3,
            help="Cantidad de registros GPS historicos por unidad.",
        )
        parser.add_argument(
            "--sin-gps",
            action="store_true",
            help="Crea empresas, rutas y salidas sin sembrar ubicaciones ni GPS.",
        )
        parser.add_argument(
            "--reset-passwords",
            action="store_true",
            help="Reasigna las contraseñas semilla aunque el usuario ya exista.",
        )

    def handle(self, *args, **options):
        prefijo = options["prefijo"].strip() or "Escala"
        total_empresas = max(options["empresas"], 1)
        total_rutas = max(options["rutas"], 1)
        total_puntos = max(options["puntos"], 2)
        total_unidades = max(options["unidades"], 1)
        historico_gps = max(options["historico_gps"], 0)
        sembrar_gps = not options["sin_gps"]
        reset_passwords = options["reset_passwords"]

        grupo_despachador, _ = Group.objects.get_or_create(name="despachador")

        resumen = {
            "empresas": 0,
            "rutas": 0,
            "puntos": 0,
            "unidades": 0,
            "salidas": 0,
            "sesiones": 0,
            "ubicaciones": 0,
            "gps": 0,
        }

        self.stdout.write(
            "Iniciando carga de escala "
            f"({total_empresas} empresas x {total_unidades} unidades)..."
        )

        for indice_empresa in range(1, total_empresas + 1):
            empresa, creada = Empresa.objects.get_or_create(
                nombre=f"{prefijo} Empresa {indice_empresa:02d}",
                defaults={"activa": True},
            )
            if creada:
                resumen["empresas"] += 1

            self._asegurar_usuarios(
                empresa=empresa,
                grupo_despachador=grupo_despachador,
                prefijo=prefijo.lower().replace(" ", ""),
                indice_empresa=indice_empresa,
                reset_passwords=reset_passwords,
            )
            self._asegurar_configuracion(empresa)

            rutas = []
            for indice_ruta in range(1, total_rutas + 1):
                ruta, ruta_creada = Ruta.objects.get_or_create(
                    empresa=empresa,
                    nombre=f"Ruta {indice_ruta:02d}",
                    defaults={
                        "geometria": self._build_geometria(
                            empresa_idx=indice_empresa,
                            ruta_idx=indice_ruta,
                            puntos=total_puntos,
                        )
                    },
                )
                rutas.append(ruta)
                if ruta_creada:
                    resumen["rutas"] += 1
                resumen["puntos"] += self._asegurar_puntos(
                    ruta=ruta,
                    total_puntos=total_puntos,
                    empresa_idx=indice_empresa,
                    ruta_idx=indice_ruta,
                )

            resultados = self._asegurar_operacion(
                empresa=empresa,
                rutas=rutas,
                total_unidades=total_unidades,
                historico_gps=historico_gps,
                sembrar_gps=sembrar_gps,
            )
            for clave, valor in resultados.items():
                resumen[clave] += valor

            recalcular_cola(empresa=empresa)

        self.stdout.write(self.style.SUCCESS("Carga de escala completada"))
        for clave, valor in resumen.items():
            self.stdout.write(f"{clave.capitalize()}: {valor}")

    def _asegurar_usuarios(
        self,
        empresa,
        grupo_despachador,
        prefijo,
        indice_empresa,
        reset_passwords=False,
    ):
        admin_username = f"{prefijo}_admin_{indice_empresa:02d}"
        admin_password = f"Admin{indice_empresa:02d}Segura!"
        admin_user, admin_created = User.objects.get_or_create(
            username=admin_username,
            defaults={
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        if admin_created or reset_passwords:
            admin_user.set_password(admin_password)
            admin_user.save(update_fields=["password"])

        desp_username = f"{prefijo}_desp_{indice_empresa:02d}"
        desp_password = f"Desp{indice_empresa:02d}Segura!"
        desp_user, desp_created = User.objects.get_or_create(
            username=desp_username,
            defaults={
                "is_staff": True,
                "is_superuser": False,
                "is_active": True,
            },
        )
        if desp_created or reset_passwords:
            desp_user.set_password(desp_password)
            desp_user.save(update_fields=["password"])
        desp_user.groups.add(grupo_despachador)

        admin_perfil, _ = PerfilUsuario.objects.get_or_create(user=admin_user)
        if admin_perfil.empresa_id != empresa.id:
            admin_perfil.empresa = empresa
            admin_perfil.save(update_fields=["empresa"])

        desp_perfil, _ = PerfilUsuario.objects.get_or_create(user=desp_user)
        if desp_perfil.empresa_id != empresa.id:
            desp_perfil.empresa = empresa
            desp_perfil.save(update_fields=["empresa"])

    def _asegurar_configuracion(self, empresa):
        activa = ConfiguracionDespacho.objects.filter(
            empresa=empresa,
            activa=True,
        ).first()
        if activa:
            return

        ConfiguracionDespacho.objects.filter(empresa=empresa).update(activa=False)
        ConfiguracionDespacho.objects.create(
            empresa=empresa,
            intervalo_fijo=None,
            activa=True,
        )

    def _asegurar_puntos(self, ruta, total_puntos, empresa_idx, ruta_idx):
        creados = 0
        for orden in range(1, total_puntos + 1):
            _, creado = PuntoControl.objects.get_or_create(
                ruta=ruta,
                orden=orden,
                defaults={
                    "codigo": f"P{orden:02d}",
                    "nombre": f"Punto {orden:02d}",
                    "latitud": -16.40 + (empresa_idx * 0.01) + (ruta_idx * 0.001) + (orden * 0.0003),
                    "longitud": -71.53 + (empresa_idx * 0.01) + (ruta_idx * 0.001) + (orden * 0.0003),
                    "radio_metros": 60,
                    "offset_minutos": (orden - 1) * 5,
                    "activo": True,
                },
            )
            if creado:
                creados += 1
        return creados

    def _asegurar_operacion(
        self,
        empresa,
        rutas,
        total_unidades,
        historico_gps,
        sembrar_gps,
    ):
        hoy = timezone.localdate()
        ahora = timezone.now()
        resultados = {
            "unidades": 0,
            "salidas": 0,
            "sesiones": 0,
            "ubicaciones": 0,
            "gps": 0,
        }

        for numero in range(1, total_unidades + 1):
            codigo = f"{numero:03d}"
            vehiculo, creado = Vehiculo.objects.get_or_create(
                empresa=empresa,
                codigo=codigo,
                defaults={
                    "activo": True,
                    "placa": f"ESC-{empresa.id:02d}{numero:03d}",
                },
            )
            if creado:
                resultados["unidades"] += 1

            ruta = rutas[(numero - 1) % len(rutas)]
            salida, salida_creada = RegistroSalida.objects.get_or_create(
                vehiculo=vehiculo,
                fecha=hoy,
                activo=True,
                defaults={
                    "ruta": ruta,
                    "hora_llegada": ahora - timedelta(minutes=numero),
                    "en_cola": True,
                    "orden_cola": numero,
                },
            )
            if salida.ruta_id != ruta.id:
                salida.ruta = ruta
                salida.save(update_fields=["ruta"])
            if salida_creada:
                resultados["salidas"] += 1

            self._asegurar_marcaciones(salida)

            sesion = SesionUnidad.objects.filter(
                vehiculo=vehiculo,
                activa=True,
            ).first()
            if sesion is None:
                sesion = SesionUnidad.objects.create(
                    vehiculo=vehiculo,
                    salida=salida,
                    activa=True,
                    last_heartbeat=ahora,
                )
                resultados["sesiones"] += 1
            elif sesion.salida_id != salida.id:
                sesion.salida = salida
                sesion.last_heartbeat = ahora
                sesion.save(update_fields=["salida", "last_heartbeat"])

            if sembrar_gps:
                resultados["ubicaciones"] += self._asegurar_ubicacion(
                    vehiculo=vehiculo,
                    numero=numero,
                    ahora=ahora,
                )
                resultados["gps"] += self._asegurar_gps(
                    sesion=sesion,
                    numero=numero,
                    historico_gps=historico_gps,
                    ahora=ahora,
                )

        return resultados

    def _asegurar_marcaciones(self, salida):
        puntos = PuntoControl.objects.filter(ruta=salida.ruta, activo=True).order_by("orden")
        for punto in puntos:
            MarcacionPunto.objects.get_or_create(
                registro_salida=salida,
                punto=punto,
            )

    def _asegurar_ubicacion(self, vehiculo, numero, ahora):
        defaults = {
            "latitud": -16.39 + (numero * 0.0002),
            "longitud": -71.52 + (numero * 0.0002),
            "velocidad": 18 + (numero % 12),
            "precision": 8 + (numero % 5),
            "updated_at": ahora,
        }
        _, creada = UbicacionVehiculo.objects.update_or_create(
            vehiculo=vehiculo,
            defaults=defaults,
        )
        return 1 if creada else 0

    def _asegurar_gps(self, sesion, numero, historico_gps, ahora):
        creados = 0
        for paso in range(historico_gps):
            timestamp = ahora - timedelta(minutes=paso * 2)
            existe = GPSRegistro.objects.filter(
                sesion=sesion,
                timestamp__gte=timestamp - timedelta(seconds=1),
                timestamp__lte=timestamp + timedelta(seconds=1),
            ).exists()
            if existe:
                continue

            gps = GPSRegistro.objects.create(
                sesion=sesion,
                lat=-16.39 + (numero * 0.0002) + (paso * 0.00005),
                lng=-71.52 + (numero * 0.0002) + (paso * 0.00005),
                velocidad=18 + (numero % 12),
                precision=8 + (numero % 5),
                bateria=70 + (numero % 20),
            )
            GPSRegistro.objects.filter(pk=gps.pk).update(timestamp=timestamp)
            creados += 1
        return creados

    def _build_geometria(self, empresa_idx, ruta_idx, puntos):
        geometria = []
        for orden in range(1, puntos + 1):
            geometria.append(
                [
                    round(-16.40 + (empresa_idx * 0.01) + (ruta_idx * 0.001) + (orden * 0.0004), 6),
                    round(-71.53 + (empresa_idx * 0.01) + (ruta_idx * 0.001) + (orden * 0.0004), 6),
                ]
            )
        return geometria
