from os import environ
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = environ.get("METEO_DEBUG") == "1"
SECRET_KEY = environ.get("METEO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("METEO_SECRET_KEY is required.")
    SECRET_KEY = "local-development-key-for-meteo-catalog"

ALLOWED_HOSTS = [host.strip() for host in environ.get("METEO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
    "django.contrib.messages", "django.contrib.staticfiles", "catalog",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "portal.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "portal.wsgi.application"

DATA_DIR = Path(environ.get("METEO_DATA_DIR", BASE_DIR / "storage" / "catalog"))
DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "catalog.sqlite3",
    "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
}}

AUTH_PASSWORD_VALIDATORS = [{"NAME": f"django.contrib.auth.password_validation.{validator}"} for validator in (
    "UserAttributeSimilarityValidator", "MinimumLengthValidator", "CommonPasswordValidator", "NumericPasswordValidator",
)]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "catalog"
LOGOUT_REDIRECT_URL = "login"
LANGUAGE_CODE = "ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
STATIC_ROOT = Path(environ.get("METEO_STATIC_ROOT", BASE_DIR / "storage" / "static"))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = 0 if DEBUG else 31536000
X_FRAME_OPTIONS = "DENY"

CATALOG_CSV_MAX_BYTES = 10 * 1024 * 1024
METEO_MAP_STYLE_URL = environ.get("METEO_MAP_STYLE_URL", "")
METEO_SUPERSET_URL = environ.get("METEO_SUPERSET_URL", "")
