"""Application configuration.

All secrets come from environment variables. Nothing secret is stored in code.
"""
import os
from datetime import timedelta


def _db_url(var: str, default: str | None = None) -> str | None:
    url = os.environ.get(var, default)
    # Render (and Heroku) hand out postgres:// URLs; SQLAlchemy needs postgresql://
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


class BaseConfig:
    APP_NAME = "Laari ProjectSphere"
    COMPANY_NAME = "Kayron Global Tech Services"
    TAGLINE = "Plan. Execute. Monitor. Deliver."
    SALES_EMAIL = os.environ.get("SALES_EMAIL", "sales@example.com")  # set your real sales address

    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _db_url("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    # Sessions and cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    WTF_CSRF_TIME_LIMIT = 60 * 60 * 8

    # Rate limiting (Redis in production so limits are shared across workers)
    REDIS_URL = os.environ.get("REDIS_URL")
    RATELIMIT_STORAGE_URI = os.environ.get("REDIS_URL", "memory://")
    RATELIMIT_DEFAULT = "300 per minute"
    RATELIMIT_HEADERS_ENABLED = True

    # Email (optional until Stage 11). Without MAIL_SERVER, invite links are shown on screen.
    MAIL_SERVER = os.environ.get("MAIL_SERVER")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_FROM = os.environ.get("MAIL_FROM", "Laari ProjectSphere <no-reply@example.com>")
    # Public address of the app, used for links in emails and payment callbacks (no trailing slash)
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")

    # Billing (Stage 11). Paystack secret key from the Paystack dashboard; never stored in code or the database.
    PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
    PAYSTACK_API = os.environ.get("PAYSTACK_API", "https://api.paystack.co")
    INVOICE_DUE_DAYS = int(os.environ.get("INVOICE_DUE_DAYS", "7"))
    RENEWAL_NOTICE_DAYS = int(os.environ.get("RENEWAL_NOTICE_DAYS", "7"))
    BILLING_GRACE_DAYS = int(os.environ.get("BILLING_GRACE_DAYS", "7"))
    PLATFORM_BANK_DETAILS = os.environ.get("PLATFORM_BANK_DETAILS", "")

    # File storage (Stage 8). Local disk by default; set STORAGE_BACKEND=s3 for cloud object storage.
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
    UPLOAD_ROOT = os.environ.get("UPLOAD_ROOT", os.path.join(os.path.dirname(os.path.dirname(__file__)), "instance", "uploads"))
    S3_BUCKET = os.environ.get("S3_BUCKET")
    S3_REGION = os.environ.get("S3_REGION")
    S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
    S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID")
    S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY")
    MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))
    MAX_CONTENT_LENGTH = (int(os.environ.get("MAX_UPLOAD_MB", "20")) + 1) * 1024 * 1024

    # Account security
    LOGIN_MAX_FAILED = 5
    LOGIN_LOCKOUT_MINUTES = 15
    PASSWORD_MIN_LENGTH = 10

    # SaaS
    TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "14"))
    DEFAULT_CURRENCY = "GHS"

    FORCE_HTTPS = True
    TESTING = False


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-for-production")
    SQLALCHEMY_DATABASE_URI = _db_url(
        "DATABASE_URL", "postgresql://ps_app:ps_app@localhost/projectsphere"
    )
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    FORCE_HTTPS = False


class TestingConfig(BaseConfig):
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = _db_url(
        "TEST_DATABASE_URL", "postgresql://ps_app:ps_app@localhost/projectsphere_test"
    )
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    RATELIMIT_STORAGE_URI = "memory://"
    FORCE_HTTPS = False
    MAIL_SERVER = None
    UPLOAD_ROOT = os.path.join("/tmp", "projectsphere-test-uploads")
    STORAGE_BACKEND = "local"
    APP_BASE_URL = "https://app.example.test"
    PAYSTACK_SECRET_KEY = "sk_test_unit_tests_only"


class ProductionConfig(BaseConfig):
    DEBUG = False


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None):
    name = name or os.environ.get("FLASK_ENV_NAME") or os.environ.get("APP_ENV", "development")
    return CONFIGS.get(name, DevelopmentConfig)
