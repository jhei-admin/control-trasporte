from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from django.template import loader

from flota_app.views import LoginSistemaView


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

    if user.is_superuser:
        return redirect("/admin/")

    if user.groups.filter(name="despachador").exists():
        return redirect("/sistema/despachador/")

    return redirect("/login/")


# =========================
# URLS
# =========================
urlpatterns = [

    path("service-worker.js", service_worker),

    path("", root_redirect),

    # PANEL ADMIN DJANGO
    path("admin/", admin.site.urls),

    # LOGIN DEL SISTEMA
    path(
        "login/",
        LoginSistemaView.as_view(),
        name="login"
    ),

    # SISTEMA DESPACHADOR
    path(
        "sistema/",
        include("flota_app.urls")
    ),
]