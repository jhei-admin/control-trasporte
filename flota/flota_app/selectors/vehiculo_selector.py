from ..models import Vehiculo


# =================================================
# 🚍 OBTENER VEHÍCULO POR CÓDIGO
# =================================================
def obtener_vehiculo_por_codigo(empresa, codigo):
    return (
        Vehiculo.objects.for_empresa(empresa)
        .filter(
            codigo=codigo,
            activo=True,
        )
        .first()
    )


# =================================================
# 🚍 OBTENER QUERYSET (SI NECESITAS VALIDAR)
# =================================================
def obtener_qs_vehiculo_por_codigo(empresa, codigo):
    return Vehiculo.objects.for_empresa(empresa).filter(
        codigo=codigo,
        activo=True,
    )


# =================================================
# 🚍 LISTAR VEHÍCULOS DE EMPRESA
# =================================================
def listar_vehiculos_empresa(empresa):
    return Vehiculo.objects.for_empresa(empresa).order_by("codigo")