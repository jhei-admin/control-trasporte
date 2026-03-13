from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from django.template import loader
from django.contrib.auth import views as auth_views

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
# ROOT REDIRECT
# =========================
def root_redirect(request):

    if not request.user.is_authenticated:
        return redirect("/login/")

    user = request.user

    if user.is_superuser:
        return redirect("/admin/")

    if user.groups.filter(name="despachador").exists():
        return redirect("/sistema/despachador/")

    return redirect("/admin/")


urlpatterns = [

    path("service-worker.js", service_worker),

    path("", root_redirect),

    # ADMIN DJANGO (usa su propio login/logout)
    path("admin/", admin.site.urls),

    # SISTEMA
    path("sistema/", include("flota_app.urls")),

    # LOGIN SISTEMA
    path(
        "login/",
        LoginSistemaView.as_view(),
        name="login"
    ),

    # LOGOUT SISTEMA
    path(
        "logout/",
        auth_views.LogoutView.as_view(
            next_page="/login/"
        ),
        name="logout"
    ),
]