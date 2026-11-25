"""Stage 3: departments and staff invitations."""
import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantScopedMixin, TimestampMixin, db, utcnow

EMPLOYMENT_TYPES = [
    ("", "Not specified"),
    ("full_time", "Full-time"),
    ("part_time", "Part-time"),
    ("contract", "Contract"),
    ("consultant", "Consultant"),
    ("volunteer", "Volunteer"),
    ("intern", "Intern / National service"),
]


class Department(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(20), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    head_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL", use_alter=True,
                                                           name="fk_departments_head_id_users"))

    head = relationship("User", foreign_keys=[head_id], lazy="joined", post_update=True)

    __table_args__ = (db.UniqueConstraint("organization_id", "name", name="uq_departments_org_name"),)


class Invitation(TenantScopedMixin, TimestampMixin, db.Model):
    """An emailed invitation to join an organization. Only a hash of the token is stored."""

    __tablename__ = "invitations"
    VALID_DAYS = 7

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    job_title: Mapped[str] = mapped_column(String(120), default="")
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    role_ids: Mapped[list] = mapped_column(JSON, default=list)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    department = relationship("Department", lazy="joined")
    invited_by = relationship("User", foreign_keys=[invited_by_id], lazy="joined")
    organization = relationship("Organization", lazy="joined")

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def issue_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self.token_hash = self.hash_token(token)
        self.expires_at = utcnow() + timedelta(days=self.VALID_DAYS)
        return token

    @property
    def status(self) -> str:
        if self.accepted_at:
            return "accepted"
        if self.revoked_at:
            return "revoked"
        if self.expires_at < utcnow():
            return "expired"
        return "pending"

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"
