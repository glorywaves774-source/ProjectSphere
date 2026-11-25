"""Route guards for the two administrative levels and per-permission access."""
from functools import wraps

from flask import abort, redirect, render_template, url_for
from flask_login import current_user


def platform_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not current_user.is_platform_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def tenant_required(view):
    """Signed-in organization user, organization not suspended."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if current_user.is_platform_admin:
            return redirect(url_for("admin.dashboard"))
        org = current_user.organization
        if org is None:
            abort(403)
        if org.is_suspended:
            return render_template("errors/suspended.html", org=org), 403
        return view(*args, **kwargs)

    return wrapped


def permission_required(*codes: str):
    def decorator(view):
        @wraps(view)
        @tenant_required
        def wrapped(*args, **kwargs):
            if not current_user.can(*codes):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
