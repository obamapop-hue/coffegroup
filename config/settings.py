"""Environment-based settings shared by local development and future deployment."""

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DJANGO_DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY or SECRET_KEY == "replace-with-a-random-local-secret":
    raise ImproperlyConfigured("실제 DJANGO_SECRET_KEY를 설정해 주세요.")

DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
railway_domain = env("RAILWAY_PUBLIC_DOMAIN", default="")
if railway_domain:
    ALLOWED_HOSTS.extend([railway_domain, "healthcheck.railway.app"])
if not DEBUG and (not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS):
    raise ImproperlyConfigured("운영 모드에는 구체적인 DJANGO_ALLOWED_HOSTS가 필요합니다.")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.kakao",
    "accounts",
    "cafes",
    "orders",
    "core",
    "support",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.HealthEndpointMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site",
            ],
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

database = env.db("DATABASE_URL")
if database["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured("DATABASE_URL은 PostgreSQL 연결이어야 합니다.")
database["CONN_MAX_AGE"] = 60
database["CONN_HEALTH_CHECKS"] = True
database.setdefault("OPTIONS", {}).setdefault("connect_timeout", 5)
DATABASES = {"default": database}

# The custom model must be selected before the first migration. Kakao identity
# and multi-role permissions are deliberately left for development stage 2.
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
LOGIN_URL = "accounts:entry-organizer"
LOGIN_REDIRECT_URL = "accounts:organizer"
SOCIALACCOUNT_ONLY = True
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_SIGNUP_FIELDS = ["username*"]
ACCOUNT_LOGIN_METHODS = {"username"}
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_ADAPTER = "accounts.adapters.AccountAdapter"
SOCIALACCOUNT_ADAPTER = "accounts.adapters.KakaoAdapter"
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False
SOCIALACCOUNT_REQUESTS_TIMEOUT = 8

KAKAO_LOGIN_ENABLED = env.bool("KAKAO_LOGIN_ENABLED", default=False)
# RESET is the owner's chosen variable name. Keep REST as a rollout fallback.
KAKAO_RESET_API_KEY = env("KAKAO_RESET_API_KEY", default="") or env(
    "KAKAO_REST_API_KEY", default=""
)
KAKAO_CLIENT_SECRET = env("KAKAO_CLIENT_SECRET", default="")
# Out-of-band first-owner selection. No public request can set this value.
INITIAL_OPERATOR_ACCOUNT_CODE = env("INITIAL_OPERATOR_ACCOUNT_CODE", default="")
if KAKAO_LOGIN_ENABLED and not (KAKAO_RESET_API_KEY and KAKAO_CLIENT_SECRET):
    raise ImproperlyConfigured(
        "카카오 로그인 활성화에는 KAKAO_RESET_API_KEY와 KAKAO_CLIENT_SECRET이 필요합니다."
    )
SOCIALACCOUNT_PROVIDERS = {
    "kakao": {
        "APP": {"client_id": KAKAO_RESET_API_KEY, "secret": KAKAO_CLIENT_SECRET},
        "SCOPE": ["profile_nickname"],
    }
}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
REQUIRE_DEADLINE_WORKER = env.bool("REQUIRE_DEADLINE_WORKER", default=False)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
if not DEBUG:
    STORAGES["staticfiles"] = {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    }

# Enable only behind the trusted Railway edge, which supplies X-Forwarded-Proto.
if env.bool("DJANGO_TRUST_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 3600 if not DEBUG else 0
# Do not preload or extend HSTS to other hosts on the shared test domain.
SECURE_REDIRECT_EXEMPT = [r"^health/live/$", r"^health/ready/$"]

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
# Persistent, sliding site sessions are independent of Kakao access-token expiry.
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = 60 * 60 * 24 * 365
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_SECURE = not DEBUG
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
