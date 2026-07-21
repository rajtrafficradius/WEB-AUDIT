"""Secure-by-default settings for local, staging, and production environments."""

from __future__ import annotations

import importlib.util
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DOTENV_PATH = BASE_DIR / ".env"
LOAD_DOTENV = os.getenv("DJANGO_LOAD_DOTENV", "true").strip().casefold() in {
    "1", "true", "yes", "on"
}
if LOAD_DOTENV and DOTENV_PATH.is_file():
    environ.Env.read_env(DOTENV_PATH, overwrite=False)

DEPLOYMENT_ENV = os.getenv("DJANGO_ENV", "development").strip().casefold()
VALID_DEPLOYMENT_ENVIRONMENTS = {"development", "test", "staging", "production"}
if DEPLOYMENT_ENV not in VALID_DEPLOYMENT_ENVIRONMENTS:
    raise ImproperlyConfigured(
        "DJANGO_ENV must be one of development, test, staging, or production."
    )
IS_DEPLOYED = DEPLOYMENT_ENV in {"staging", "production"}


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
_RUNNING_TESTS = "test" in sys.argv or "pytest" in str(Path(sys.argv[0])).casefold()
if not SECRET_KEY:
    if not IS_DEPLOYED and (
        DEBUG or _RUNNING_TESTS or os.getenv("DJANGO_ALLOW_INSECURE_DEV_KEY") == "1"
    ):
        SECRET_KEY = secrets.token_urlsafe(64)
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be configured outside development.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
THIRD_PARTY_APPS: list[str] = []
if importlib.util.find_spec("rest_framework") is not None:
    THIRD_PARTY_APPS.append("rest_framework")

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + ["app.domain.apps.DomainConfig"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "app.middleware.RequestIDMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "app.middleware.ForcePasswordChangeMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "app.urls"
WSGI_APPLICATION = "app.wsgi.application"
ASGI_APPLICATION = "app.asgi.application"

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
            ]
        },
    }
]


def database_config() -> dict[str, object]:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}
    parsed = urlparse(value)
    engines = {
        "postgres": "django.db.backends.postgresql",
        "postgresql": "django.db.backends.postgresql",
        "sqlite": "django.db.backends.sqlite3",
    }
    scheme = parsed.scheme.split("+")[0]
    if scheme not in engines:
        raise ValueError(f"Unsupported DATABASE_URL scheme: {scheme}")
    if scheme == "sqlite":
        return {"ENGINE": engines[scheme], "NAME": unquote(parsed.path)}
    query = parse_qs(parsed.query)
    options: dict[str, str] = {}
    if query.get("sslmode"):
        options["sslmode"] = query["sslmode"][0]
    config: dict[str, object] = {
        "ENGINE": engines[scheme],
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": parsed.port or 5432,
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }
    if options:
        config["OPTIONS"] = options
    return config


DATABASES = {"default": database_config()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "domain.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LANGUAGE_CODE = "en-au"
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "Australia/Melbourne")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
MEDIA_ROOT = BASE_DIR / ".local-media"
MEDIA_URL = "/media/"

OBJECT_STORAGE_ENABLED = env_bool(
    "OBJECT_STORAGE_ENABLED", bool(os.getenv("AWS_STORAGE_BUCKET_NAME", "").strip())
)
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN", "").strip()
AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME", "").strip()
AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL", "").strip()
AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "auto").strip() or "auto"
AWS_S3_ADDRESSING_STYLE = os.getenv("AWS_S3_ADDRESSING_STYLE", "path").strip().casefold()
AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_S3_VERIFY = env_bool("AWS_S3_VERIFY", True)
AWS_S3_FILE_OVERWRITE = False
AWS_DEFAULT_ACL = "private"
AWS_QUERYSTRING_AUTH = True
AWS_QUERYSTRING_EXPIRE = int(os.getenv("AWS_QUERYSTRING_EXPIRE", "300"))
OBJECT_STORAGE_PREFIX = os.getenv("OBJECT_STORAGE_PREFIX", "private/objects").strip("/")
OBJECT_STORAGE_SSE = os.getenv("OBJECT_STORAGE_SSE", "AES256").strip()
OBJECT_STORAGE_KMS_KEY_ID = os.getenv("OBJECT_STORAGE_KMS_KEY_ID", "").strip()


def storage_value_is_placeholder(value: str) -> bool:
    """True when an AWS setting still carries an unedited template value."""

    return "REPLACE_WITH" in value.upper()


OBJECT_STORAGE_MISCONFIGURED = OBJECT_STORAGE_ENABLED and (
    not AWS_STORAGE_BUCKET_NAME
    or storage_value_is_placeholder(AWS_STORAGE_BUCKET_NAME)
    or storage_value_is_placeholder(AWS_ACCESS_KEY_ID)
    or storage_value_is_placeholder(AWS_SECRET_ACCESS_KEY)
    or storage_value_is_placeholder(AWS_S3_REGION_NAME)
    or AWS_S3_ENDPOINT_URL in {"https://", "http://"}
)
if OBJECT_STORAGE_MISCONFIGURED:
    # Booting with a broken S3 backend silently breaks every artifact write
    # (package builds fail at the final save). Fall back to app-local storage
    # so the product keeps working; artifacts are rebuildable on demand.
    import logging as _logging

    _logging.getLogger("app.settings").warning(
        "Object storage is enabled but AWS settings contain placeholder or "
        "invalid values; falling back to local artifact storage. Set real S3 "
        "credentials or OBJECT_STORAGE_ENABLED=false to silence this warning."
    )
    OBJECT_STORAGE_ENABLED = False

if (
    not OBJECT_STORAGE_PREFIX
    or any(part in {"", ".", ".."} for part in OBJECT_STORAGE_PREFIX.split("/"))
    or "\\" in OBJECT_STORAGE_PREFIX
):
    raise ImproperlyConfigured("OBJECT_STORAGE_PREFIX must be a safe relative object prefix.")
if AWS_S3_ADDRESSING_STYLE not in {"path", "virtual"}:
    raise ImproperlyConfigured("AWS_S3_ADDRESSING_STYLE must be path or virtual.")
if OBJECT_STORAGE_SSE not in {"AES256", "aws:kms"}:
    raise ImproperlyConfigured("OBJECT_STORAGE_SSE must be AES256 or aws:kms.")
if OBJECT_STORAGE_SSE == "aws:kms" and not OBJECT_STORAGE_KMS_KEY_ID:
    raise ImproperlyConfigured("OBJECT_STORAGE_KMS_KEY_ID is required for aws:kms encryption.")

AWS_S3_OBJECT_PARAMETERS = {
    "CacheControl": "private, no-store, max-age=0",
    "ServerSideEncryption": OBJECT_STORAGE_SSE,
}
if OBJECT_STORAGE_KMS_KEY_ID:
    AWS_S3_OBJECT_PARAMETERS["SSEKMSKeyId"] = OBJECT_STORAGE_KMS_KEY_ID

_private_storage_options: dict[str, object] = {
    "bucket_name": AWS_STORAGE_BUCKET_NAME,
    "region_name": AWS_S3_REGION_NAME,
    "endpoint_url": AWS_S3_ENDPOINT_URL or None,
    "addressing_style": AWS_S3_ADDRESSING_STYLE,
    "signature_version": AWS_S3_SIGNATURE_VERSION,
    "default_acl": AWS_DEFAULT_ACL,
    "file_overwrite": AWS_S3_FILE_OVERWRITE,
    "object_parameters": AWS_S3_OBJECT_PARAMETERS,
    "querystring_auth": AWS_QUERYSTRING_AUTH,
    "querystring_expire": AWS_QUERYSTRING_EXPIRE,
    "location": OBJECT_STORAGE_PREFIX,
    "custom_domain": None,
    "verify": AWS_S3_VERIFY,
}
if AWS_ACCESS_KEY_ID:
    _private_storage_options["access_key"] = AWS_ACCESS_KEY_ID
if AWS_SECRET_ACCESS_KEY:
    _private_storage_options["secret_key"] = AWS_SECRET_ACCESS_KEY
if AWS_SESSION_TOKEN:
    _private_storage_options["security_token"] = AWS_SESSION_TOKEN

STORAGES = {
    "default": (
        {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": _private_storage_options,
        }
        if OBJECT_STORAGE_ENABLED
        else {"BACKEND": "django.core.files.storage.FileSystemStorage"}
    ),
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
WHITENOISE_AUTOREFRESH = DEBUG
WHITENOISE_USE_FINDERS = DEBUG
WHITENOISE_MANIFEST_STRICT = IS_DEPLOYED
WHITENOISE_MAX_AGE = int(os.getenv("WHITENOISE_MAX_AGE", "31536000"))

# Keep authenticated and CSRF session state server-side in the shared cache.
# Production resolves the default cache to Redis; local development uses locmem.
SESSION_ENGINE = os.getenv("SESSION_ENGINE", "django.contrib.sessions.backends.cache")
SESSION_CACHE_ALIAS = os.getenv("SESSION_CACHE_ALIAS", "default")

SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "tr_seo_session")
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", "1800"))
SESSION_SAVE_EVERY_REQUEST = env_bool("SESSION_SAVE_EVERY_REQUEST", True)
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("SESSION_EXPIRE_AT_BROWSER_CLOSE", True)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax").strip().title()
SESSION_COOKIE_DOMAIN = os.getenv("SESSION_COOKIE_DOMAIN", "").strip() or None
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = os.getenv("CSRF_COOKIE_SAMESITE", "Lax").strip().title()
CSRF_COOKIE_DOMAIN = os.getenv("CSRF_COOKIE_DOMAIN", "").strip() or None
CSRF_USE_SESSIONS = True
if SESSION_COOKIE_SAMESITE not in {"Lax", "Strict", "None"}:
    raise ImproperlyConfigured("SESSION_COOKIE_SAMESITE must be Lax, Strict, or None.")
if CSRF_COOKIE_SAMESITE not in {"Lax", "Strict", "None"}:
    raise ImproperlyConfigured("CSRF_COOKIE_SAMESITE must be Lax, Strict, or None.")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", not DEBUG)
# Railway performs deployment health checks over the container's internal HTTP
# endpoint. Exempt only the machine health routes so the probe receives the
# view's direct 200/503 response while every user-facing route still forces HTTPS.
SECURE_REDIRECT_EXEMPT = [r"^healthz/$", r"^readyz/$"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0" if DEBUG else "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("FILE_UPLOAD_MAX_MEMORY_SIZE", str(2 * 1024 * 1024)))

LOGIN_URL = "/auth/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/auth/login/"

REDIS_URL = os.getenv("REDIS_URL", "").strip()


def redis_db_url(value: str, database: int) -> str:
    """Return a Redis URL on an isolated logical database without exposing credentials."""

    parsed = urlparse(value)
    if parsed.scheme not in {"redis", "rediss"}:
        raise ImproperlyConfigured("Redis URLs must use redis:// or rediss://.")
    return parsed._replace(path=f"/{database}").geturl()


REDIS_CACHE_URL = os.getenv("REDIS_CACHE_URL", "").strip()
if not REDIS_CACHE_URL and REDIS_URL:
    REDIS_CACHE_URL = redis_db_url(REDIS_URL, 2)
if REDIS_CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_CACHE_URL,
            "KEY_PREFIX": f"tr-seo:{DEPLOYMENT_ENV}",
            "TIMEOUT": int(os.getenv("CACHE_DEFAULT_TIMEOUT", "300")),
            "OPTIONS": {
                "socket_connect_timeout": int(os.getenv("REDIS_CONNECT_TIMEOUT", "5")),
                "socket_timeout": int(os.getenv("REDIS_SOCKET_TIMEOUT", "5")),
            },
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "traffic-radius",
        }
    }

CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL", redis_db_url(REDIS_URL, 0) if REDIS_URL else "redis://localhost:6379/0"
)
CELERY_RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    redis_db_url(REDIS_URL, 1) if REDIS_URL else "redis://localhost:6379/1",
)
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_DEFAULT_QUEUE = "analysis"
CELERY_IMPORTS = (
    "app.domain.tasks",
    "audit_engine.tasks",
    "exporters.tasks",
    "integrations.tasks",
)
CELERY_TASK_ROUTES = {
    "studio.analysis.*": {"queue": "analysis"},
    "studio.scheduler.*": {"queue": "analysis"},
    "studio.render.*": {"queue": "render"},
    "audit_engine.tasks.*": {"queue": "analysis"},
    "integrations.tasks.*": {"queue": "analysis"},
    "generation.tasks.*": {"queue": "analysis"},
    "exporters.tasks.*": {"queue": "render"},
}
CELERY_TASK_ANNOTATIONS = {
    "studio.analysis.*": {"soft_time_limit": 10_500, "time_limit": 10_800},
    "studio.scheduler.*": {"soft_time_limit": 240, "time_limit": 300},
    "studio.render.*": {"soft_time_limit": 1_500, "time_limit": 1_800},
    "audit_engine.tasks.*": {"soft_time_limit": 10_500, "time_limit": 10_800},
    "integrations.tasks.*": {"soft_time_limit": 3_300, "time_limit": 3_600},
    "generation.tasks.*": {"soft_time_limit": 3_300, "time_limit": 3_600},
    "exporters.tasks.*": {"soft_time_limit": 1_500, "time_limit": 1_800},
}
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_RESULT_EXPIRES = int(os.getenv("CELERY_RESULT_EXPIRES", "604800"))
STAGE_HEARTBEAT_TIMEOUT_SECONDS = max(
    60, int(os.getenv("STAGE_HEARTBEAT_TIMEOUT_SECONDS", "300"))
)
STAGE_STALE_SCAN_INTERVAL_SECONDS = max(
    60, int(os.getenv("STAGE_STALE_SCAN_INTERVAL_SECONDS", "60"))
)
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": int(os.getenv("CELERY_VISIBILITY_TIMEOUT", "14400")),
    "socket_connect_timeout": int(os.getenv("REDIS_CONNECT_TIMEOUT", "5")),
    "socket_timeout": int(os.getenv("REDIS_SOCKET_TIMEOUT", "5")),
}
CELERY_BEAT_SCHEDULE: dict[str, dict[str, object]] = {
    "mark-stale-run-stages": {
        "task": "studio.scheduler.mark_stale_stages",
        "schedule": float(STAGE_STALE_SCAN_INTERVAL_SECONDS),
        "options": {
            "queue": "analysis",
            "expires": max(30, STAGE_STALE_SCAN_INTERVAL_SECONDS - 5),
        },
    }
}
CELERY_BEAT_MAX_LOOP_INTERVAL = int(os.getenv("CELERY_BEAT_MAX_LOOP_INTERVAL", "30"))

CREDENTIAL_ENCRYPTION_KEYS = os.getenv("CREDENTIAL_ENCRYPTION_KEYS", "")
CREDENTIAL_ENCRYPTION_ACTIVE_KEY = os.getenv("CREDENTIAL_ENCRYPTION_ACTIVE_KEY", "")
OPENAI_INTAKE_GENERATION_ENABLED = env_bool("OPENAI_INTAKE_GENERATION_ENABLED", True)
AUTO_START_AUDIT_RUNS = env_bool("AUTO_START_AUDIT_RUNS", not _RUNNING_TESTS)
AUTO_AUDIT_PAGE_LIMIT = max(1, min(2500, int(os.getenv("AUTO_AUDIT_PAGE_LIMIT", "250"))))
AUTO_AUDIT_DURATION_SECONDS = max(30, int(os.getenv("AUTO_AUDIT_DURATION_SECONDS", "600")))
AUTO_BUILD_PACKAGE = env_bool("AUTO_BUILD_PACKAGE", not _RUNNING_TESTS)
OPENAI_STRATEGY_MODEL = os.getenv("OPENAI_STRATEGY_MODEL", "gpt-5.6-sol").strip()
OPENAI_EXTRACTION_MODEL = os.getenv("OPENAI_EXTRACTION_MODEL", "gpt-5.6-luna").strip()

# --- Package enrichment -------------------------------------------------
# Read via getattr across the exporter pipeline; defined here so the flag is
# actually configurable instead of silently defaulting.
PACKAGE_AI_ENRICHMENT_ENABLED = env_bool("PACKAGE_AI_ENRICHMENT_ENABLED", True)
PACKAGE_AI_MAX_CALLS = max(0, int(os.getenv("PACKAGE_AI_MAX_CALLS", "3")))

# --- SEMrush market data ------------------------------------------------
# SEMrush bills per returned line. SEMRUSH_UNIT_BUDGET is a hard per-run
# ceiling: a report whose estimated cost exceeds the remaining budget is
# skipped and recorded, never issued.
SEMRUSH_API_KEY = os.getenv("SEMRUSH_API_KEY", "").strip()
SEMRUSH_PLAN_TIER = os.getenv("SEMRUSH_PLAN_TIER", "lite").strip().casefold() or "lite"
if SEMRUSH_PLAN_TIER not in {"lite", "standard", "deep"}:
    raise ImproperlyConfigured("SEMRUSH_PLAN_TIER must be lite, standard, or deep.")
SEMRUSH_UNIT_BUDGET = max(0, int(os.getenv("SEMRUSH_UNIT_BUDGET", "700")))
SEMRUSH_DATABASE = os.getenv("SEMRUSH_DATABASE", "au").strip().casefold() or "au"
# The SEMrush contract caps local caching of API responses at 30 days.
SEMRUSH_CACHE_DAYS = max(0, min(30, int(os.getenv("SEMRUSH_CACHE_DAYS", "30"))))
# Kill switch, default on. Enrichment still only runs when a key resolves
# (organisation credential, per-project connection, or the env var above), so
# leaving this on with no key configured is a no-op, not wasted work.
MARKET_DATA_ENABLED = env_bool("MARKET_DATA_ENABLED", True)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "app.api.exceptions.exception_handler",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "app.logging.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}


def validate_deployed_configuration() -> None:
    """Fail closed when staging or production security invariants are incomplete."""

    if not IS_DEPLOYED:
        return

    failures: list[str] = []
    if DEBUG:
        failures.append("DJANGO_DEBUG must be false")
    if len(SECRET_KEY) < 50 or "replace" in SECRET_KEY.casefold():
        failures.append("DJANGO_SECRET_KEY must be a non-placeholder value of at least 50 characters")
    unsafe_hosts = {"*", "localhost", "127.0.0.1", "testserver"}
    if not ALLOWED_HOSTS or any(
        host.split(":", 1)[0].casefold() in unsafe_hosts for host in ALLOWED_HOSTS
    ):
        failures.append("DJANGO_ALLOWED_HOSTS must contain only explicit deployed hosts")
    if not CSRF_TRUSTED_ORIGINS or any(
        not origin.casefold().startswith("https://") for origin in CSRF_TRUSTED_ORIGINS
    ):
        failures.append("DJANGO_CSRF_TRUSTED_ORIGINS must contain only HTTPS origins")
    if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        failures.append("DATABASE_URL must use PostgreSQL")
    if not REDIS_URL:
        failures.append("REDIS_URL is required")
    # Durable S3-compatible storage is recommended (artifacts survive
    # redeploys) but not mandatory: single-service deployments may run on
    # local storage because every artifact is rebuildable from the database.
    if OBJECT_STORAGE_ENABLED:
        if not AWS_STORAGE_BUCKET_NAME:
            failures.append("AWS_STORAGE_BUCKET_NAME is required when object storage is enabled")
        if AWS_S3_ENDPOINT_URL and not AWS_S3_ENDPOINT_URL.casefold().startswith("https://"):
            failures.append("AWS_S3_ENDPOINT_URL must use HTTPS")
        if not AWS_S3_VERIFY:
            failures.append("AWS_S3_VERIFY must remain enabled")
    if not (SESSION_COOKIE_SECURE and CSRF_COOKIE_SECURE):
        failures.append("session and CSRF cookies must be Secure")
    if not SECURE_SSL_REDIRECT:
        failures.append("SECURE_SSL_REDIRECT must remain enabled")
    if SECURE_HSTS_SECONDS < 86_400:
        failures.append("SECURE_HSTS_SECONDS must be at least 86400")
    key_ids = {
        item.split(":", 1)[0].strip()
        for item in CREDENTIAL_ENCRYPTION_KEYS.split(",")
        if ":" in item
    }
    if not CREDENTIAL_ENCRYPTION_ACTIVE_KEY or CREDENTIAL_ENCRYPTION_ACTIVE_KEY not in key_ids:
        failures.append("credential encryption must have a configured active key")

    if failures:
        raise ImproperlyConfigured(
            f"Unsafe {DEPLOYMENT_ENV} configuration: " + "; ".join(failures)
        )


validate_deployed_configuration()
