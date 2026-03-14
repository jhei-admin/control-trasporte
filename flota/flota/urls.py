from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from django.template import loader
from django.contrib.auth.decorators import login_required
from django.contrib.auth import views as auth_views

from flota_app.views import LoginSistemaView


# ==========================================
# 🔐 USAR EL MISMO LOGIN PARA TODO EL SISTEMA
# ==========================================
admin.site.login = LoginSistemaView.as_view()


# =========================
# SERVICE WORKER
# =========================
@require_GET
def service_worker(request):
    template = loader.get_template("service-worker.js")
    response = HttpResponse(
        template.render(),
        content_type="application/javascript"
    )
    response["Service-Worker-Allowed"] = "/"
    return response


# =========================
# REDIRECCIÓN RAÍZ
# =========================
def root_redirect(request):

    if not request.user.is_authenticated:
        return redirect("/login/")

    user = request.user

    # ADMIN
    if user.is_superuser:
        return redirect("/admin/")

    # DESPACHADOR
    if user.groups.filter(name="despachador").exists():
        return redirect("/sistema/despachador/")

    return redirect("/login/")


# =========================
# URLS PRINCIPALES
# =========================
urlpatterns = [

    path("service-worker.js", service_worker),

    # RAÍZ
    path("", root_redirect),

    # LOGIN ÚNICO DEL SISTEMA
    path(
        "login/",
        LoginSistemaView.as_view(),
        name="login"
    ),

    # LOGOUT
    path(
        "logout/",
        auth_views.LogoutView.as_view(next_page="/login/"),
        name="logout",
    ),

    # ADMIN (PROTEGIDO)
    path(
        "admin/",
        login_required(admin.site.urls)
    ),

    # SISTEMA DESPACHADOR
    path(
        "sistema/",
        include("flota_app.urls")
    ),
]