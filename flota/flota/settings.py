"""
Django settings for flota project.
"""

from pathlib import Path
import os
import dj_database_url

# =========================
# BASE
# =========================
BASE_DIR = Path(__file__).resolve().parent.parent


# =========================
# SECURITY
# =========================
SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "django-insecure-fallback-key-solo-local"
)

DEBUG = False


# =========================
# HOSTS
# =========================
ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    ".onrender.com",
    ".trycloudflare.com",
]


# =========================
# CSRF
# =========================
CSRF_TRUSTED_ORIGINS = [
    "https://control-trasporte.onrender.com",
    "https://*.onrender.com",
    "https://*.trycloudflare.com",
]

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True


# =========================
# INSTALLED APPS
# =========================
INSTALLED_APPS = [

    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # CORS
    "corsheaders",

    # App
    "flota_app.apps.FlotaAppConfig",
]


# =========================
# MIDDLEWARE
# =========================
MIDDLEWARE = [

    "corsheaders.middleware.CorsMiddleware",

    "django.middleware.security.SecurityMiddleware",

    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",

    "django.middleware.common.CommonMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",

    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# =========================
# CORS
# =========================
CORS_ALLOWED_ORIGINS = [
    "https://control-trasporte.onrender.com"
]

CORS_ALLOW_CREDENTIALS = True


# =========================
# URLS / WSGI / ASGI
# =========================
ROOT_URLCONF = "flota.urls"

WSGI_APPLICATION = "flota.wsgi.application"
ASGI_APPLICATION = "flota.asgi.application"


# =========================
# TEMPLATES
# =========================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",

        "DIRS": [],

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


# =========================
# DATABASE
# =========================
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=600,
            ssl_require=True,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# =========================
# PASSWORD VALIDATION
# =========================
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# =========================
# I18N
# =========================
LANGUAGE_CODE = "es-pe"

TIME_ZONE = os.getenv("TIME_ZONE", "America/Lima")

USE_I18N = True

USE_TZ = True


# =========================
# STATIC FILES
# =========================
STATIC_URL = "/static/"

STATICFILES_DIRS = [
    BASE_DIR / "flota_app" / "static",
]

STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


# =========================
# LOGGING (IMPORTANTE EN RENDER)
# =========================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "ERROR",
    },
}


# =========================
# SECURITY HEADERS
# =========================
SECURE_CONTENT_TYPE_NOSNIFF = False

SECURE_CROSS_ORIGIN_OPENER_POLICY = None

SECURE_REFERRER_POLICY = "same-origin"


# =========================
# MISC
# =========================
APPEND_SLASH = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# =========================
# MAPBOX
# =========================
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN")


# =========================
# LOGIN CONFIG
# =========================
LOGIN_URL = "/login/"

LOGIN_REDIRECT_URL = "/sistema/despachador/"

LOGOUT_REDIRECT_URL = "/login/"


# =========================
# SESSION
# =========================
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

SESSION_COOKIE_AGE = 0

SESSION_SAVE_EVERY_REQUEST = True