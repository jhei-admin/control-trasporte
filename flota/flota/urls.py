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


urlpatterns = [

    # 🔧 SERVICE WORKER
    path("service-worker.js", service_worker),

    # 🔁 ROOT → SISTEMA
    path("", lambda request: redirect("/sistema/conductor/")),

    # 🔐 ADMIN
    path("admin/", admin.site.urls),

    # 🚍 TODO EL SISTEMA (AQUÍ ESTABA EL ERROR)
    path("sistema/", include("flota_app.urls")),

    path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
