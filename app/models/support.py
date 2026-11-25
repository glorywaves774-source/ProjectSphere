from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantScopedMixin, TimestampMixin, db, utcnow

TICKET_CATEGORIES = [
    ("billing", "Billing & subscription"),
    ("technical", "Technical problem"),
    ("account", "Account & access"),
    ("feature", "Feature request"),
    ("other", "Other"),
]
TICKET_PRIORITIES = [("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")]
TICKET_STATUSES = [
    ("open", "Open"),
    ("in_progress", "In progress"),
    ("waiting", "Waiting on you"),
    ("resolved", "Resolved"),
    ("closed", "Closed"),
]
OPEN_STATUSES = {"open", "in_progress", "waiting"}


class SupportTicket(TenantScopedMixin, TimestampMixin, db.Model):
    """A support request raised by an organization to Kayron Global Tech Services."""

    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(20), default="other", nullable=False)
    priority: Mapped[str] = mapped_column(String(10), default="normal", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False, index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    created_by = relationship("User", foreign_keys=[created_by_id], lazy="joined")
    assigned_to = relationship("User", foreign_keys=[assigned_to_id], lazy="joined")
    organization = relationship("Organization", lazy="joined")
    messages = relationship("SupportMessage", back_populates="ticket", order_by="SupportMessage.created_at",
                            cascade="all, delete-orphan")

    @property
    def reference(self) -> str:
        return f"PS-{self.id:05d}"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def status_label(self) -> str:
        return dict(TICKET_STATUSES).get(self.status, self.status)

    @property
    def category_label(self) -> str:
        return dict(TICKET_CATEGORIES).get(self.category, self.category)

    def public_messages(self):
        return [m for m in self.messages if not m.is_internal]


class SupportMessage(TenantScopedMixin, db.Model):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # staff-only note
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    ticket = relationship("SupportTicket", back_populates="messages")
    author = relationship("User", lazy="joined")
