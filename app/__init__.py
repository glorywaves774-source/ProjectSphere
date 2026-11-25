"""Laari ProjectSphere - application factory."""
import logging

from flask import Flask, flash, g, redirect, render_template, request, url_for
from flask_login import current_user
from flask_talisman import Talisman
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import get_config
from .extensions import csrf, db, limiter, login_manager, migrate
from .tenancy import TenantContext, auth_lookup_scope, clear_context, set_context

CSP = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self'",
    "img-src": "'self' data:",
    "font-src": "'self'",
    "connect-src": "'self'",
    "object-src": "'none'",
    "base-uri": "'self'",
    "form-action": "'self' https://checkout.paystack.com",  # Pay now hands over to Paystack's checkout
    "frame-ancestors": "'none'",
}


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY environment variable is required")
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError("DATABASE_URL environment variable is required")

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "info"
    login_manager.session_protection = "strong"

    Talisman(
        app,
        content_security_policy=CSP,
        force_https=app.config["FORCE_HTTPS"],
        strict_transport_security=app.config["FORCE_HTTPS"],
        session_cookie_secure=app.config["SESSION_COOKIE_SECURE"],
        referrer_policy="strict-origin-when-cross-origin",
        frame_options="DENY",
        # location is only ever used by "Use my location" on field visits; nothing else may ask
        permissions_policy={"geolocation": "(self)", "camera": "(self)", "microphone": "()", "payment": "()",
                            "usb": "()", "browsing-topics": "()"},
    )

    from . import models as _models  # noqa: F401  (register models)
    from .tenancy.guard import install as install_tenant_guard

    with app.app_context():
        install_tenant_guard(db)

    _register_auth(app)
    _register_blueprints(app)
    _register_errors(app)
    _register_template_helpers(app)

    from .cli import register_cli

    register_cli(app)
    return app


_PASSWORD_CHANGE_ALLOWED = {"workspace.account", "admin.account", "auth.logout", "public.healthz",
                            "help.ask", "help.suggestions"}


def _register_auth(app: Flask) -> None:
    from .models import User

    @login_manager.user_loader
    def load_user(token: str):
        try:
            user_id, version = (int(x) for x in token.split(":", 1))
        except (ValueError, AttributeError):
            return None
        with auth_lookup_scope():
            user = db.session.get(User, user_id)
            if user is not None:
                _ = user.roles  # load while lookup is allowed
        if user is None or user.session_version != version or not user.is_active_user:
            return None
        return user

    @app.before_request
    def _bind_tenant():
        clear_context()
        if request.endpoint == "static":
            return
        if current_user.is_authenticated:
            set_context(
                TenantContext(
                    tenant_id=current_user.organization_id,
                    platform=bool(current_user.is_platform_admin),
                )
            )
            # Accounts given a temporary password must choose their own first
            if current_user.must_change_password and request.endpoint not in _PASSWORD_CHANGE_ALLOWED:
                flash("Please choose your own password to continue.", "warning")
                return redirect(url_for("admin.account" if current_user.is_platform_admin else "workspace.account"))

    @app.teardown_request
    def _unbind_tenant(exc=None):
        # One database session per request: nothing loaded under one tenant
        # survives into the next request.
        db.session.remove()
        g.pop("_login_user", None)
        clear_context()


def _register_blueprints(app: Flask) -> None:
    from .blueprints.admin.routes import bp as admin_bp
    from .blueprints.api.routes import bp as api_bp
    from .blueprints.auth.routes import bp as auth_bp
    from .blueprints.help.routes import bp as help_bp
    from .blueprints.public.routes import bp as public_bp
    from .blueprints.workspace.routes import bp as workspace_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(workspace_bp, url_prefix="/app")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(help_bp, url_prefix="/help")
    app.register_blueprint(api_bp, url_prefix="/api")


def _register_errors(app: Flask) -> None:
    def page(code, title, message):
        return render_template("errors/error.html", code=code, title=title, message=message), code

    @app.errorhandler(403)
    def forbidden(e):
        return page(403, "No access", "Your role does not include access to this page. Ask your organization admin if you need it.")

    @app.errorhandler(404)
    def not_found(e):
        return page(404, "Page not found", "The page you were looking for doesn't exist or has moved.")

    @app.errorhandler(429)
    def too_many(e):
        return page(429, "Slow down", "Too many attempts. Please wait a minute and try again.")

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return page(400, "Form expired", "This form expired for your security. Go back, refresh the page and try again.")

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        return page(500, "Something went wrong", "We hit a problem on our side. Please try again in a moment.")


def _register_template_helpers(app: Flask) -> None:
    from .navigation import nav_for

    @app.context_processor
    def inject():
        from .services import settings as platform_settings

        nav = nav_for(current_user) if current_user.is_authenticated else None
        counts = {"notes": 0, "msgs": 0}
        if nav is not None and not nav["is_admin"] and current_user.organization_id:
            try:
                from .services import collab as collab_svc, notify as notify_svc

                counts = {"notes": notify_svc.unread_count(current_user), "msgs": collab_svc.unread_total(current_user)}
            except Exception:  # never break a page because a badge failed
                counts = {"notes": 0, "msgs": 0}
        try:
            ps = platform_settings.all_settings()
        except Exception:  # database not migrated yet
            ps = {d.key: d.default for d in platform_settings.SETTINGS}
        return {
            "PS": ps,
            "SIGNUP_OPEN": ps.get("allow_signup", "true") == "true",
            "APP_NAME": app.config["APP_NAME"],
            "COMPANY_NAME": app.config["COMPANY_NAME"],
            "TAGLINE": app.config["TAGLINE"],
            "SALES_EMAIL": ps.get("sales_email") or app.config["SALES_EMAIL"],
            "nav": nav,
            "UNREAD": counts,
        }

    from .models import utcnow

    from datetime import timedelta as _td

    app.jinja_env.globals["now_utc"] = utcnow
    app.jinja_env.globals["timedelta"] = _td

    @app.template_filter("filesize")
    def filesize(n):
        n = int(n or 0)
        for unit in ("bytes", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
            n /= 1024

    @app.template_filter("money")
    def money(value, currency="GHS"):
        if value is None:
            return "Custom"
        symbol = {"GHS": "GH₵", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency + " ")
        return f"{symbol}{value:,.0f}" if value == int(value) else f"{symbol}{value:,.2f}"

    @app.template_filter("qty")
    def qty(value):
        if value is None:
            return ""
        text = f"{value:,.2f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    @app.template_filter("dt")
    def dt(value, fmt="%d %b %Y, %H:%M"):
        return value.strftime(fmt) if value else "—"
