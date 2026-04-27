"""
Django settings for flota project (PRODUCCIÓN ESTABLE)
Optimizado para Render + Seguridad + CSRF estable
"""

from pathlib import Path
import os
import dj_database_url
from django.core.exceptions import ImproperlyConfigured


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=None):
    value = os.getenv(name)
    if not value:
        return list(default or [])
    return [item.strip() for item in value.split(",") if item.strip()]


# =================================================
# BASE
# =================================================
BASE_DIR = Path(__file__).resolve().parent.parent


# =================================================
# SECURITY
# =================================================
SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "django-insecure-dev-key"
)

DEBUG = env_bool("DEBUG", False)
IS_RENDER = env_bool("RENDER", bool(os.getenv("RENDER")))
REQUIRE_POSTGRES = env_bool("REQUIRE_POSTGRES", False)
FAIL_ON_SQLITE_IN_PRODUCTION = env_bool(
    "FAIL_ON_SQLITE_IN_PRODUCTION",
    IS_RENDER or REQUIRE_POSTGRES,
)


# =================================================
# HOSTS
# =================================================
ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    ".onrender.com",
    "control-trasporte.onrender.com",
]
ALLOWED_HOSTS.extend(
    host for host in env_list("ALLOWED_HOSTS") if host not in ALLOWED_HOSTS
)


# =================================================
# CSRF / SESSION (FIX DEFINITIVO RENDER)
# =================================================

CSRF_TRUSTED_ORIGINS = [
    "https://control-trasporte.onrender.com",
    "https://*.onrender.com",
]
CSRF_TRUSTED_ORIGINS.extend(
    origin
    for origin in env_list("CSRF_TRUSTED_ORIGINS")
    if origin not in CSRF_TRUSTED_ORIGINS
)

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Cookies seguras
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG)

CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_HTTPONLY = True

# 🔥 evita errores CSRF en Render
CSRF_COOKIE_HTTPONLY = False
CSRF_USE_SESSIONS = False

# duración sesiones
SESSION_COOKIE_AGE = 86400
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = True


# =================================================
# LOGIN SECURITY
# =================================================
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend"
]


# =================================================
# INSTALLED APPS
# =================================================
INSTALLED_APPS = [

    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "corsheaders",

    "flota_app.apps.FlotaAppConfig",
]


# =================================================
# MIDDLEWARE
# =================================================
MIDDLEWARE = [

    "django.middleware.security.SecurityMiddleware",

    "flota_app.middleware.startup_audit.StartupAuditMiddleware",

    "whitenoise.middleware.WhiteNoiseMiddleware",

    "corsheaders.middleware.CorsMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",

    "django.middleware.common.CommonMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",

    'flota_app.middleware.empresa_middleware.EmpresaMiddleware',

    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# =================================================
# CORS
# =================================================
CORS_ALLOWED_ORIGINS = [
    "https://control-trasporte.onrender.com",
]
CORS_ALLOWED_ORIGINS.extend(
    origin
    for origin in env_list("CORS_ALLOWED_ORIGINS")
    if origin not in CORS_ALLOWED_ORIGINS
)

CORS_ALLOW_CREDENTIALS = True


# =================================================
# URLS
# =================================================
ROOT_URLCONF = "flota.urls"

WSGI_APPLICATION = "flota.wsgi.application"
ASGI_APPLICATION = "flota.asgi.application"


# =================================================
# TEMPLATES
# =================================================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",

        "DIRS": [BASE_DIR / "templates"],

        "APP_DIRS": True,

        "OPTIONS": {
            "context_processors": [

                "django.template.context_processors.debug",

                "django.template.context_processors.request",

                "django.contrib.auth.context_processors.auth",

                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# =================================================
# DATABASE
# =================================================
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=600,
            ssl_require=True,
        )
    }
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"].setdefault(
        "connect_timeout",
        int(os.getenv("DATABASE_CONNECT_TIMEOUT", "10")),
    )
    DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
else:
    if REQUIRE_POSTGRES or IS_RENDER:
        print("ADVERTENCIA: Render activo sin DATABASE_URL. Se usara SQLite solo como modo de emergencia.")
    sqlite_name = os.getenv("SQLITE_NAME")
    if not sqlite_name:
        for candidate in ("db.clean.sqlite3", "db.active.sqlite3", "db.ready.sqlite3", "db.local.sqlite3", "db.sqlite3"):
            if (BASE_DIR / candidate).exists():
                sqlite_name = candidate
                break
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / sqlite_name,
            "OPTIONS": {
                "timeout": int(os.getenv("SQLITE_TIMEOUT_SECONDS", "30")),
            },
        }
    }

if (
    FAIL_ON_SQLITE_IN_PRODUCTION
    and DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
):
    raise ImproperlyConfigured(
        "SQLite no esta permitido en produccion. Configure DATABASE_URL con PostgreSQL."
    )


# =================================================
# PASSWORD VALIDATION
# =================================================
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# =================================================
# INTERNACIONALIZACIÓN
# =================================================
LANGUAGE_CODE = "es-pe"

TIME_ZONE = os.getenv("TIME_ZONE", "America/Lima")

USE_I18N = True
USE_TZ = True


# =================================================
# STATIC FILES
# =================================================
STATIC_URL = "/static/"

STATICFILES_DIRS = [
    BASE_DIR / "flota_app" / "static",
]

STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


# =================================================
# LOGGING
# =================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": os.getenv("DJANGO_REQUEST_LOG_LEVEL", "ERROR"),
            "propagate": False,
        },
        "flota_app": {
            "handlers": ["console"],
            "level": os.getenv("APP_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.getenv("ROOT_LOG_LEVEL", "ERROR"),
    },
}


# =================================================
# SECURITY HEADERS
# =================================================
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

SECURE_BROWSER_XSS_FILTER = True

SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_RESOURCE_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_EMBEDDER_POLICY = "require-corp"

X_FRAME_OPTIONS = "SAMEORIGIN"


# =================================================
# HTTPS
# =================================================
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)


# =================================================
# MISC
# =================================================
APPEND_SLASH = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

GPS_RETENTION_DAYS = int(os.getenv("GPS_RETENTION_DAYS", "15"))
PARADAS_RETENTION_DAYS = int(os.getenv("PARADAS_RETENTION_DAYS", "2"))
CLEANUP_BATCH_SIZE = int(os.getenv("CLEANUP_BATCH_SIZE", "5000"))
GPS_SAVE_INTERVAL_SECONDS = int(os.getenv("GPS_SAVE_INTERVAL_SECONDS", "15"))
GPS_MAX_PRECISION = float(os.getenv("GPS_MAX_PRECISION", "100"))
MAINTENANCE_ACTION_KEY = os.getenv("MAINTENANCE_ACTION_KEY", "")
ALLOW_LEGACY_QR = env_bool("ALLOW_LEGACY_QR", False)
INACTIVE_SESSION_RETENTION_DAYS = int(os.getenv("INACTIVE_SESSION_RETENTION_DAYS", "7"))
MENSAJES_RETENTION_DAYS = int(os.getenv("MENSAJES_RETENTION_DAYS", "30"))


# =================================================
# APP UPDATES
# =================================================
APP_LATEST_VERSION_CODE = int(os.getenv("APP_LATEST_VERSION_CODE", "0"))
APP_LATEST_VERSION_NAME = os.getenv("APP_LATEST_VERSION_NAME", "").strip()
APP_UPDATE_FORCE = env_bool("APP_UPDATE_FORCE", False)
APP_UPDATE_CHANGELOG = os.getenv("APP_UPDATE_CHANGELOG", "").strip()
APP_UPDATE_PUBLISHED_AT = os.getenv("APP_UPDATE_PUBLISHED_AT", "").strip()
APP_UPDATE_APK_URL = os.getenv("APP_UPDATE_APK_URL", "").strip()
APP_UPDATE_APK_FILENAME = (
    os.getenv("APP_UPDATE_APK_FILENAME", "gpsflotaaqp-latest.apk").strip()
    or "gpsflotaaqp-latest.apk"
)
APP_UPDATE_APK_PATH = BASE_DIR / "app_updates" / APP_UPDATE_APK_FILENAME


# =================================================
# MAPBOX
# =================================================
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN")


# =================================================
# LOGIN
# =================================================
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/sistema/despachador/"
LOGOUT_REDIRECT_URL = "/login/"
