import uuid
from decimal import Decimal
from datetime import date, datetime

from flask_login import UserMixin
from sqlalchemy import Boolean, Column, Date, DateTime, Numeric, ForeignKey, Integer, String, Table, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .base import TenantScopedMixin, TimestampMixin, db

role_permissions = Table(
    "role_permissions",
    db.metadata,
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", Integer, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class UserRole(TenantScopedMixin, db.Model):
    """Link between a user and a role, carrying the tenant id so RLS can protect it."""

    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class Permission(db.Model):
    """Global permission catalog (see app.security.catalog)."""

    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    module: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(160), default="")


class Role(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    permissions = relationship("Permission", secondary=role_permissions, lazy="selectin")

    __table_args__ = (db.UniqueConstraint("organization_id", "key", name="uq_roles_org_key"),)

    @property
    def permission_codes(self) -> set[str]:
        return {p.code for p in self.permissions}


class User(UserMixin, TimestampMixin, db.Model):
    """A person who signs in.

    Organization users always have ``organization_id``. Platform Super
    Administrators (Kayron Global Tech Services staff) have no organization.
    """

    __tablename__ = "users"
    __tenant_scoped__ = True  # filtered like tenant data; platform admins have NULL org

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    job_title: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active_user: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Staff profile (Stage 3)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"), index=True)
    staff_number: Mapped[str] = mapped_column(String(40), default="", server_default="", nullable=False)
    employment_type: Mapped[str] = mapped_column(String(20), default="", server_default="", nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    weekly_hours: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=40, server_default="40", nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    email_notify: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    organization = relationship("Organization", lazy="joined")
    department = relationship("Department", foreign_keys=[department_id], lazy="joined")
    roles = relationship("Role", secondary="user_roles", lazy="selectin", viewonly=True)

    # --- auth -------------------------------------------------------------
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)  # scrypt by default
        self.session_version = (self.session_version or 0) + 1

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    def get_id(self) -> str:
        # Changing the password invalidates other sessions and "remember me" cookies
        return f"{self.id}:{self.session_version}"

    @property
    def is_active(self) -> bool:  # used by Flask-Login
        return self.is_active_user

    # --- RBAC -------------------------------------------------------------
    @property
    def permission_codes(self) -> set[str]:
        codes: set[str] = set()
        for role in self.roles:
            codes |= role.permission_codes
        return codes

    def can(self, *codes: str) -> bool:
        if self.is_platform_admin:
            return False  # platform admins act through /admin, not inside tenant workspaces
        have = self.permission_codes
        return all(c in have for c in codes)

    def can_any(self, *codes: str) -> bool:
        have = self.permission_codes
        return any(c in have for c in codes)

    @property
    def role_keys(self) -> set[str]:
        return {r.key for r in self.roles}

    @property
    def employment_label(self) -> str:
        from .people import EMPLOYMENT_TYPES

        return dict(EMPLOYMENT_TYPES).get(self.employment_type, "")

    @property
    def role_names(self) -> list[str]:
        if self.is_platform_admin:
            return ["Super Admin"]
        return sorted(r.name for r in self.roles)

    @property
    def initials(self) -> str:
        parts = [p for p in self.full_name.split() if p]
        return ("".join(p[0] for p in parts[:2]) or "?").upper()
