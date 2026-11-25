import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantScopedMixin, TimestampMixin, db, utcnow

ORG_TYPES = [
    ("ngo", "NGO / Non-profit"),
    ("private", "Private company"),
    ("government", "Government agency"),
    ("consulting", "Consulting firm"),
    ("development", "Development organization"),
    ("individual", "Individual / Freelancer"),
    ("other", "Other"),
]


class Plan(TimestampMixin, db.Model):
    """Subscription package. Global (shared by all tenants), managed by Super Admin."""

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="")
    audience: Mapped[str] = mapped_column(String(80), default="")
    price_monthly: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)  # None = custom
    price_annual: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="GHS", nullable=False)
    max_users: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = unlimited
    max_projects: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_storage_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    features: Mapped[list] = mapped_column(JSON, default=list)
    api_access: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    @property
    def is_custom_priced(self) -> bool:
        return self.price_monthly is None

    def limit_for(self, resource: str) -> int | None:
        return {"users": self.max_users, "projects": self.max_projects, "storage_mb": self.max_storage_mb}[resource]


class Organization(TimestampMixin, db.Model):
    """A tenant: one subscribing organization with its own isolated workspace."""

    __tablename__ = "organizations"

    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    org_type: Mapped[str] = mapped_column(String(30), default="other", nullable=False)
    country: Mapped[str] = mapped_column(String(80), default="Ghana", nullable=False)
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    website: Mapped[str] = mapped_column(String(255), default="")
    currency: Mapped[str] = mapped_column(String(3), default="GHS", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Accra", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_ACTIVE, nullable=False, index=True)
    suspended_reason: Mapped[str] = mapped_column(String(255), default="")

    subscription = relationship("Subscription", back_populates="organization", uselist=False)

    @property
    def is_suspended(self) -> bool:
        return self.status == self.STATUS_SUSPENDED

    @property
    def type_label(self) -> str:
        return dict(ORG_TYPES).get(self.org_type, "Other")

    @property
    def profile_complete(self) -> bool:
        return bool(self.email and self.phone and self.address)


class Subscription(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "subscriptions"

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    USABLE = {TRIALING, ACTIVE}

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=TRIALING, nullable=False, index=True)
    billing_cycle: Mapped[str] = mapped_column(String(10), default="monthly", nullable=False)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plan = relationship("Plan", lazy="joined")
    organization = relationship("Organization", back_populates="subscription")

    __table_args__ = (db.UniqueConstraint("organization_id", name="uq_subscriptions_organization_id"),)

    @property
    def is_usable(self) -> bool:
        return self.status in self.USABLE

    @property
    def trial_days_left(self) -> int | None:
        if self.status != self.TRIALING or not self.trial_ends_at:
            return None
        delta = self.trial_ends_at - utcnow()
        return max(0, delta.days + (1 if delta.seconds else 0))


class PlatformSetting(TimestampMixin, db.Model):
    """Key/value settings for the whole platform (Super Admin only)."""

    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
