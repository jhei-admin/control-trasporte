from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie


@method_decorator(never_cache, name="dispatch")
@method_decorator(csrf_protect, name="dispatch")
@method_decorator(ensure_csrf_cookie, name="dispatch")
class LoginSistemaView(LoginView):
    template_name = "login.html"
    authentication_form = AuthenticationForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        user = form.get_user()
        login(self.request, user)
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(
            self.request,
            "Usuario o contrasena incorrectos.",
        )
        return super().form_invalid(form)

    def get_success_url(self):
        user = self.request.user

        if user.is_superuser:
            return "/admin/"

        if user.groups.filter(name="despachador").exists():
            return "/sistema/despachador/"

        return "/admin/"


__all__ = ["LoginSistemaView"]
