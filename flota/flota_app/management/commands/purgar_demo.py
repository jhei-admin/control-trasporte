from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db.models import Q

from flota_app.models import Empresa


class Command(BaseCommand):
    help = (
        "Identifica y elimina datos demo de forma segura. "
        "Por defecto solo muestra coincidencias; use --aplicar para borrar."
    )

    EMPRESA_DEMO_PATTERNS = ("demo", "prueba", "test", "example", "sample")
    USUARIO_DEMO_PATTERNS = ("admin", "despachador", "demo", "test", "example")
    GRUPO_DEMO_PATTERNS = ("demo", "test", "example")

    def add_arguments(self, parser):
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Ejecuta el borrado. Sin este flag solo informa coincidencias.",
        )
        parser.add_argument(
            "--empresa",
            action="append",
            default=[],
            help="Nombre exacto de empresa a purgar. Puede repetirse.",
        )
        parser.add_argument(
            "--usuario",
            action="append",
            default=[],
            help="Username exacto a purgar. Puede repetirse.",
        )
        parser.add_argument(
            "--incluir-admin-basico",
            action="store_true",
            help="Incluye usuarios genericos como admin y despachador en la deteccion automatica.",
        )

    def _or_query(self, field_name, values, lookup="icontains"):
        query = Q()
        for value in values:
            query |= Q(**{f"{field_name}__{lookup}": value})
        return query

    def handle(self, *args, **options):
        empresa_names = [value.strip() for value in options["empresa"] if value.strip()]
        user_names = [value.strip() for value in options["usuario"] if value.strip()]

        empresa_query = self._or_query("nombre", self.EMPRESA_DEMO_PATTERNS)
        if empresa_names:
            empresa_query |= self._or_query("nombre", empresa_names, lookup="iexact")
        empresas = Empresa.objects.filter(empresa_query)

        user_patterns = [
            pattern
            for pattern in self.USUARIO_DEMO_PATTERNS
            if pattern not in {"admin", "despachador"}
        ]
        if options["incluir_admin_basico"]:
            user_patterns.extend(["admin", "despachador"])
        user_query = self._or_query("username", user_patterns)
        if user_names:
            user_query |= self._or_query("username", user_names, lookup="iexact")
        usuarios = User.objects.filter(user_query)

        grupos = Group.objects.filter(
            self._or_query("name", self.GRUPO_DEMO_PATTERNS)
        )

        self.stdout.write("Auditoria de datos demo")
        self.stdout.write(
            f"Empresas candidatas: {empresas.count()} | "
            f"Usuarios candidatos: {usuarios.count()} | "
            f"Grupos candidatos: {grupos.count()}"
        )

        for empresa in empresas.order_by("nombre"):
            self.stdout.write(f" - Empresa: {empresa.nombre}")
        for usuario in usuarios.order_by("username"):
            self.stdout.write(f" - Usuario: {usuario.username}")
        for grupo in grupos.order_by("name"):
            self.stdout.write(f" - Grupo: {grupo.name}")

        if not options["aplicar"]:
            self.stdout.write(
                self.style.WARNING(
                    "Modo auditoria: no se elimino nada. Use --aplicar para confirmar."
                )
            )
            return

        empresa_ids = list(empresas.values_list("id", flat=True))
        usuario_ids = list(usuarios.values_list("id", flat=True))
        grupo_ids = list(grupos.values_list("id", flat=True))

        deleted_empresas = len(empresa_ids)
        deleted_usuarios = len(usuario_ids)
        deleted_grupos = len(grupo_ids)

        if deleted_usuarios:
            User.objects.filter(id__in=usuario_ids).delete()
        if deleted_grupos:
            Group.objects.filter(id__in=grupo_ids).delete()
        if deleted_empresas:
            Empresa.objects.filter(id__in=empresa_ids).delete()

        self.stdout.write(
            self.style.SUCCESS(
                "Purga completada. "
                f"Empresas eliminadas: {deleted_empresas}, "
                f"Usuarios eliminados: {deleted_usuarios}, "
                f"Grupos eliminados: {deleted_grupos}"
            )
        )
