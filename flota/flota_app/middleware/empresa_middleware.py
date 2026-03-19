from django.utils.deprecation import MiddlewareMixin

class EmpresaMiddleware(MiddlewareMixin):

    def process_request(self, request):
        user = request.user

        if not user.is_authenticated:
            request.empresa = None
            return

        perfil = getattr(user, "perfil", None)
        empresa = getattr(perfil, "empresa", None)

        request.empresa = empresa