from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from django.template import loader
from django.contrib.auth import views as auth_views


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

    Si el usuario ya está autenticado:
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

    # 🔐 ADMIN
    path("admin/", admin.site.urls),

    # 🚍 SISTEMA
    path("sistema/", include("flota_app.urls")),

    # 🔐 LOGIN
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="login.html",
            redirect_authenticated_user=True
        ),
        name="login"
    ),

    # 🔓 LOGOUT
    path(
        "logout/",
        auth_views.LogoutView.as_view(),
        name="logout"
    ),
]