import secrets

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from flota_app.models import ConfiguracionDespacho, Empresa, PerfilUsuario, Ruta, Vehiculo


class Command(BaseCommand):
    help = "Crea una configuracion inicial de empresa, rutas, unidades y usuarios."

    def add_arguments(self, parser):
        parser.add_argument(
            "--empresa",
            default="Empresa Demo Transporte",
            help="Nombre de la empresa inicial",
        )
        parser.add_argument(
            "--rutas",
            nargs="*",
            default=["Ruta A", "Ruta B"],
            help="Nombres de rutas iniciales",
        )
        parser.add_argument(
            "--unidades",
            type=int,
            default=20,
            help="Cantidad de unidades iniciales",
        )
        parser.add_argument(
            "--admin-user",
            default="admin",
            help="Usuario administrador",
        )
        parser.add_argument(
            "--admin-pass",
            default=None,
            help="Password del administrador",
        )
        parser.add_argument(
            "--despachador-user",
            default="despachador",
            help="Usuario despachador",
        )
        parser.add_argument(
            "--despachador-pass",
            default=None,
            help="Password del despachador",
        )

    def handle(self, *args, **options):
        empresa, _ = Empresa.objects.get_or_create(
            nombre=options["empresa"],
            defaults={"activa": True},
        )

        rutas_creadas = []
        for nombre_ruta in options["rutas"]:
            ruta, _ = Ruta.objects.get_or_create(
                empresa=empresa,
                nombre=nombre_ruta,
            )
            rutas_creadas.append(ruta.nombre)

        unidades_creadas = 0
        for numero in range(1, options["unidades"] + 1):
            codigo = f"{numero:02d}"
            _, created = Vehiculo.objects.get_or_create(
                empresa=empresa,
                codigo=codigo,
                defaults={"activo": True},
            )
            if created:
                unidades_creadas += 1

        ConfiguracionDespacho.objects.filter(empresa=empresa).update(activa=False)
        ConfiguracionDespacho.objects.create(
            empresa=empresa,
            intervalo_fijo=None,
            activa=True,
        )

        grupo_despachador, _ = Group.objects.get_or_create(name="despachador")

        admin_password = options["admin_pass"] or secrets.token_urlsafe(10)
        admin_user, admin_created = User.objects.get_or_create(
            username=options["admin_user"],
            defaults={
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        if admin_created:
            admin_user.set_password(admin_password)
            admin_user.save()
        else:
            admin_password = "(sin cambios)"

        despachador_password = options["despachador_pass"] or secrets.token_urlsafe(10)
        despachador_user, desp_created = User.objects.get_or_create(
            username=options["despachador_user"],
            defaults={
                "is_staff": True,
                "is_superuser": False,
                "is_active": True,
            },
        )
        if desp_created:
            despachador_user.set_password(despachador_password)
            despachador_user.save()
        else:
            despachador_password = "(sin cambios)"

        despachador_user.groups.add(grupo_despachador)

        admin_perfil, _ = PerfilUsuario.objects.get_or_create(user=admin_user)
        admin_perfil.empresa = empresa
        admin_perfil.save(update_fields=["empresa"])

        desp_perfil, _ = PerfilUsuario.objects.get_or_create(user=despachador_user)
        desp_perfil.empresa = empresa
        desp_perfil.save(update_fields=["empresa"])

        self.stdout.write(self.style.SUCCESS("Bootstrap inicial completado"))
        self.stdout.write(f"Empresa: {empresa.nombre}")
        self.stdout.write(f"Rutas: {', '.join(rutas_creadas)}")
        self.stdout.write(f"Unidades nuevas creadas: {unidades_creadas}")
        self.stdout.write(f"Admin: {admin_user.username} | Password: {admin_password}")
        self.stdout.write(
            f"Despachador: {despachador_user.username} | Password: {despachador_password}"
        )
