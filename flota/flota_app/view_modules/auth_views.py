from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect
from django.urls import reverse
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

    def _resolve_user_redirect(self, user):
        if user.is_superuser or user.is_staff:
            return "/admin/", None

        if user.groups.filter(name="despachador").exists():
            empresa = getattr(getattr(user, "perfil", None), "empresa", None)
            if empresa:
                return "/sistema/despachador/", None
            return None, "Tu usuario despachador no tiene empresa asignada."

        return None, "Tu usuario no tiene permisos para ingresar al sistema."

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            success_url, error_message = self._resolve_user_redirect(request.user)
            if success_url is None:
                logout(request)
                messages.error(request, error_message)
                return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        login(self.request, user)
        success_url, error_message = self._resolve_user_redirect(user)
        if success_url is None:
            logout(self.request)
            messages.error(self.request, error_message)
            return redirect("login")
        return redirect(success_url)

    def form_invalid(self, form):
        messages.error(
            self.request,
            "Usuario o contrasena incorrectos.",
        )
        return super().form_invalid(form)

    def get_success_url(self):
        success_url, _ = self._resolve_user_redirect(self.request.user)
        return success_url or reverse("login")


__all__ = ["LoginSistemaView"]
