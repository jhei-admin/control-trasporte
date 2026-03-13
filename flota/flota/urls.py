from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from django.template import loader
from django.contrib.auth import views as auth_views
from django.views.decorators.csrf import ensure_csrf_cookie
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
# REDIRECT ROOT
# =========================
def root_redirect(request):
    """
    Redirección inicial del sistema.

    Si el usuario está autenticado:
        → panel despachador
    Si no:
        → login
    """
    if request.user.is_authenticated:
        return redirect("/sistema/despachador/")
    return redirect("/login/")


# =========================
# URLS PRINCIPALES
# =========================
urlpatterns = [

    # 🔧 SERVICE WORKER
    path("service-worker.js", service_worker),

    # 🔁 ROOT
    path("", root_redirect),

    # 🔐 ADMIN DJANGO
    path("admin/", admin.site.urls),

    # 🚍 SISTEMA
    path("sistema/", include("flota_app.urls")),

    # 🔐 LOGIN DEL SISTEMA
    path(
        "login/",
         ensure_csrf_cookie(
         LoginSistemaView.as_view()
        ),
        name="login"
    ),

    # 🔓 LOGOUT DEL SISTEMA
    path(
        "logout/",
        auth_views.LogoutView.as_view(
            next_page="/login/"
        ),
        name="logout"
    ),
]