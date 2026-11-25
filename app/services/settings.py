"""Platform settings managed by the Super Admin (stored in platform_settings)."""
from dataclasses import dataclass

from flask import current_app, g, has_app_context
from sqlalchemy import select

from app.extensions import db
from app.models import PlatformSetting


@dataclass(frozen=True)
class SettingDef:
    key: str
    label: str
    kind: str  # text | email | bool | int | choice
    default: str
    help: str = ""
    choices: tuple = ()


SETTINGS = [
    SettingDef("platform_name", "Platform name", "text", "Laari ProjectSphere", "Shown in page titles and emails."),
    SettingDef("support_email", "Support email", "email", "support@example.com", "Where organizations can write for help."),
    SettingDef("sales_email", "Sales email", "email", "sales@example.com", "Used by the Enterprise 'Talk to sales' button."),
    SettingDef("allow_signup", "Allow self-service sign-up", "bool", "true",
               "When off, only a Super Admin can create new organizations."),
    SettingDef("trial_days", "Free trial length (days)", "int", "14", "Applies to new sign-ups. 0 starts on an active plan."),
    SettingDef("announcement", "Announcement banner", "text", "",
               "Shown at the top of every organization workspace. Leave empty to hide."),
    SettingDef("announcement_level", "Banner style", "choice", "info", "",
               (("info", "Information"), ("warning", "Warning"), ("success", "Good news"))),
]
BY_KEY = {s.key: s for s in SETTINGS}


def _load() -> dict[str, str]:
    values = {s.key: s.default for s in SETTINGS}
    for row in db.session.scalars(select(PlatformSetting)):
        if row.key in values:
            values[row.key] = row.value
    return values


def all_settings() -> dict[str, str]:
    if has_app_context() and "_ps_settings" in g:
        return g._ps_settings
    values = _load()
    if has_app_context():
        g._ps_settings = values
    return values


def get(key: str) -> str:
    return all_settings()[key]


def get_bool(key: str) -> bool:
    return get(key).strip().lower() in {"1", "true", "yes", "on"}


def get_int(key: str) -> int:
    try:
        return int(get(key))
    except ValueError:
        return int(BY_KEY[key].default)


def save(values: dict[str, str]) -> list[str]:
    """Save settings; returns the keys that changed."""
    current = _load()
    changed = []
    for key, value in values.items():
        if key not in BY_KEY or current.get(key) == value:
            continue
        row = db.session.get(PlatformSetting, key)
        if row is None:
            db.session.add(PlatformSetting(key=key, value=value))
        else:
            row.value = value
        changed.append(key)
    if has_app_context():
        g.pop("_ps_settings", None)
    return changed


def trial_days() -> int:
    try:
        return get_int("trial_days")
    except Exception:  # pragma: no cover - table missing during first migration
        return current_app.config["TRIAL_DAYS"]
