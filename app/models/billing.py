"""Stage 11: invoices, payments, API keys and the email outbox."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantScopedMixin, TimestampMixin, db, utcnow

INVOICE_STATUSES = [("open", "Due"), ("paid", "Paid"), ("void", "Void")]
INVOICE_KINDS = [("new", "New subscription"), ("renewal", "Renewal"), ("change", "Plan change"),
                 ("manual", "Manual invoice")]
PAYMENT_STATUSES = [("pending", "Pending"), ("success", "Successful"), ("failed", "Failed"),
                    ("abandoned", "Abandoned")]
API_SCOPES = [("projects", "Projects and tasks (read)"), ("finance", "Budgets and expenses (read)"),
              ("me", "Indicators and results (read)"), ("me:write", "Submit indicator results"),
              ("risks", "Risks and issues (read)")]


class Invoice(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)  # INV-2026-000123 (platform-wide)
    kind: Mapped[str] = mapped_column(String(10), default="new", nullable=False)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"))
    billing_cycle: Mapped[str] = mapped_column(String(10), default="monthly", nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    credit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)  # unused time on old plan
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="GHS", nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="open", nullable=False, index=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_method: Mapped[str] = mapped_column(String(20), default="")  # paystack | bank | cash | other
    payment_reference: Mapped[str] = mapped_column(String(100), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    plan = relationship("Plan", lazy="joined")
    organization = relationship("Organization", lazy="joined")
    payments = relationship("Payment", back_populates="invoice", order_by="Payment.created_at.desc()")

    @property
    def status_label(self) -> str:
        return dict(INVOICE_STATUSES).get(self.status, self.status)

    @property
    def kind_label(self) -> str:
        return dict(INVOICE_KINDS).get(self.kind, self.kind)

    @property
    def is_overdue(self) -> bool:
        return self.status == "open" and self.due_date < date.today()


class Payment(TenantScopedMixin, db.Model):
    """One attempt to pay an invoice online. The reference is unique, which makes processing idempotent."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(20), default="paystack", nullable=False)
    reference: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(30), default="")  # card, mobile_money, bank ...
    gateway_message: Mapped[str] = mapped_column(String(255), default="")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    invoice = relationship("Invoice", back_populates="payments")

    @property
    def status_label(self) -> str:
        return dict(PAYMENT_STATUSES).get(self.status, self.status)


class ApiKey(TenantScopedMixin, db.Model):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)  # first characters, shown to identify a key
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # SHA-256 of the full key
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by = relationship("User", lazy="joined")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > utcnow())

    @property
    def scope_labels(self) -> list[str]:
        d = dict(API_SCOPES)
        return [d.get(s, s) for s in self.scopes or []]


class EmailOutbox(TenantScopedMixin, db.Model):
    """Emails are written here in the same transaction as the change that causes them,
    then sent by the background worker, so nothing is emailed for work that was rolled back."""

    __tablename__ = "email_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="queued", nullable=False, index=True)  # queued|sent|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
