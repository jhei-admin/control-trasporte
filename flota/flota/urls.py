from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from django.template import loader
from django.contrib.auth import views as auth_views
from flota_app.views import LoginSistemaView


# =========================
# REDIRECCIÓN RAÍZ
# =========================
from django.urls import reverse

def root_redirect(request):
    if not request.user.is_authenticated:
        return redirect(reverse("login"))

    user = request.user

    if user.is_superuser:
        return redirect(reverse("admin:index"))

    if user.groups.filter(name="despachador").exists():
        return redirect(reverse("panel_despachador"))

    return redirect(reverse("login"))


# =========================
# URLS
# =========================
urlpatterns = [

    path("", root_redirect),

    # LOGIN DEL SISTEMA
    path(
        "login/",
        LoginSistemaView.as_view(),
        name="login"
    ),

    path(
        "logout/",
        auth_views.LogoutView.as_view(next_page="/login/"),
        name="logout",
    ),

    # ADMIN DJANGO (NORMAL)
    path("admin/", admin.site.urls),

    # SISTEMA DESPACHADOR
    path(
        "sistema/",
        include("flota_app.urls")
    ),
]