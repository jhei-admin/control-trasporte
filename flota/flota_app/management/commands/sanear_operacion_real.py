from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q

from flota_app.models import Empresa, PerfilUsuario, Vehiculo


class Command(BaseCommand):
    help = (
        "Conserva solo la empresa operativa real y ayuda a depurar datos demo o "
        "de escala. Por defecto solo audita; use --aplicar para ejecutar cambios."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--empresa-real",
            required=True,
            help="Nombre exacto de la empresa real que debe conservarse.",
        )
        parser.add_argument(
            "--unidades-esperadas",
            type=int,
            default=None,
            help="Cantidad esperada de unidades activas de la empresa real.",
        )
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Aplica la depuracion. Sin este flag solo muestra el impacto.",
        )
        parser.add_argument(
            "--confirmar-empresa",
            default="",
            help="Repita exactamente el nombre de la empresa real para confirmar el borrado.",
        )
        parser.add_argument(
            "--eliminar-usuarios-huerfanos",
            action="store_true",
            help="Elimina usuarios staff/perfiles que queden sin empresa valida despues de la depuracion.",
        )
        parser.add_argument(
            "--eliminar-grupos-demo",
            action="store_true",
            help="Elimina grupos demo/test/example que ya no se usen.",
        )

    def handle(self, *args, **options):
        empresa_real_nombre = options["empresa_real"].strip()
        if not empresa_real_nombre:
            raise CommandError("Debe indicar --empresa-real con el nombre exacto a conservar.")

        empresa_real = (
            Empresa.objects
            .annotate(total_vehiculos=Count("vehiculos"))
            .filter(nombre=empresa_real_nombre)
            .first()
        )
        if empresa_real is None:
            raise CommandError(
                f"No se encontro una empresa exacta con nombre '{empresa_real_nombre}'."
            )

        unidades_esperadas = options["unidades_esperadas"]
        vehiculos_activos_real = Vehiculo.objects.filter(
            empresa=empresa_real,
            activo=True,
        ).count()
        vehiculos_totales_real = Vehiculo.objects.filter(
            empresa=empresa_real,
        ).count()

        if (
            unidades_esperadas is not None
            and vehiculos_activos_real != unidades_esperadas
        ):
            raise CommandError(
                "La empresa real no coincide con las unidades esperadas: "
                f"activas={vehiculos_activos_real}, esperadas={unidades_esperadas}."
            )

        empresas_extra = Empresa.objects.exclude(id=empresa_real.id).order_by("nombre")
        perfiles_validos = PerfilUsuario.objects.filter(empresa=empresa_real)
        usuarios_validos_ids = set(perfiles_validos.values_list("user_id", flat=True))

        usuarios_huerfanos = User.objects.filter(
            Q(perfil__empresa__isnull=True) | ~Q(id__in=usuarios_validos_ids),
            is_superuser=False,
        ).distinct().order_by("username")
        grupos_demo = Group.objects.filter(
            Q(name__icontains="demo")
            | Q(name__icontains="test")
            | Q(name__icontains="example")
            | Q(name__icontains="sample")
        ).order_by("name")

        self.stdout.write("Auditoria de saneamiento operativo")
        self.stdout.write(f"Empresa real: {empresa_real.nombre}")
        self.stdout.write(
            f"Vehiculos empresa real: totales={vehiculos_totales_real} | "
            f"activos={vehiculos_activos_real}"
        )
        self.stdout.write(f"Empresas extra detectadas: {empresas_extra.count()}")
        for empresa in empresas_extra:
            activos = Vehiculo.objects.filter(empresa=empresa, activo=True).count()
            total = Vehiculo.objects.filter(empresa=empresa).count()
            self.stdout.write(
                f" - Empresa extra: {empresa.nombre} | vehiculos={total} | activos={activos}"
            )

        self.stdout.write(
            f"Usuarios potencialmente huerfanos fuera de la empresa real: {usuarios_huerfanos.count()}"
        )
        for usuario in usuarios_huerfanos:
            empresa_nombre = getattr(getattr(usuario, "perfil", None), "empresa", None)
            self.stdout.write(
                f" - Usuario: {usuario.username} | empresa={empresa_nombre or 'SIN EMPRESA'}"
            )

        self.stdout.write(f"Grupos demo detectados: {grupos_demo.count()}")
        for grupo in grupos_demo:
            self.stdout.write(f" - Grupo demo: {grupo.name}")

        if not options["aplicar"]:
            self.stdout.write(
                self.style.WARNING(
                    "Modo auditoria: no se elimino nada. "
                    "Use --aplicar y --confirmar-empresa para ejecutar."
                )
            )
            return

        if options["confirmar_empresa"].strip() != empresa_real.nombre:
            raise CommandError(
                "Confirmacion rechazada. Debe repetir exactamente el nombre de la empresa real "
                "en --confirmar-empresa antes de borrar."
            )

        with transaction.atomic():
            empresas_eliminadas = empresas_extra.count()
            if empresas_eliminadas:
                empresas_extra.delete()

            usuarios_eliminados = 0
            if options["eliminar_usuarios_huerfanos"]:
                usuarios_eliminados = usuarios_huerfanos.count()
                if usuarios_eliminados:
                    usuarios_huerfanos.delete()

            grupos_eliminados = 0
            if options["eliminar_grupos_demo"]:
                grupos_eliminados = grupos_demo.count()
                if grupos_eliminados:
                    grupos_demo.delete()

        self.stdout.write(
            self.style.SUCCESS(
                "Saneamiento completado. "
                f"Empresas eliminadas: {empresas_eliminadas}, "
                f"Usuarios eliminados: {usuarios_eliminados}, "
                f"Grupos demo eliminados: {grupos_eliminados}."
            )
        )
