"""Stage 6: project budgets, expenditure and exchange rates."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantScopedMixin, TimestampMixin, db

BUDGET_CATEGORIES = [
    ("personnel", "Personnel"),
    ("travel", "Travel & transport"),
    ("equipment", "Equipment"),
    ("supplies", "Supplies & materials"),
    ("services", "Contracts & services"),
    ("training", "Training & workshops"),
    ("operations", "Operations & overheads"),
    ("other", "Other"),
]
BUDGET_STATUSES = [("draft", "Draft"), ("submitted", "Submitted for approval"), ("approved", "Approved")]
EXPENSE_STATUSES = [
    ("draft", "Draft"),
    ("submitted", "Awaiting approval"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("paid", "Paid"),
]
SPENT_STATUSES = {"approved", "paid"}  # count as actual expenditure
COMMITTED_STATUSES = {"submitted"}  # requested, not yet approved
PAYMENT_METHODS = [("bank", "Bank transfer"), ("momo", "Mobile money"), ("cheque", "Cheque"),
                   ("cash", "Cash"), ("card", "Card")]


class BudgetLine(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "budget_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(20), default="other", nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=1, nullable=False)
    unit: Mapped[str] = mapped_column(String(30), default="")
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0, nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    notes: Mapped[str] = mapped_column(Text, default="")

    project = relationship("Project", lazy="joined")
    task = relationship("Task", lazy="joined")

    __table_args__ = (db.UniqueConstraint("project_id", "code", name="uq_budget_lines_project_code"),)

    @property
    def amount(self) -> Decimal:
        return (Decimal(self.quantity or 0) * Decimal(self.unit_cost or 0)).quantize(Decimal("0.01"))

    @property
    def category_label(self) -> str:
        return dict(BUDGET_CATEGORIES).get(self.category, self.category)


class Expense(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    budget_line_id: Mapped[int] = mapped_column(ForeignKey("budget_lines.id", ondelete="RESTRICT"), nullable=False,
                                                index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    reference: Mapped[str] = mapped_column(String(30), nullable=False)  # EXP-2026-0001
    spent_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    payee: Mapped[str] = mapped_column(String(160), default="")
    invoice_ref: Mapped[str] = mapped_column(String(60), default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)  # in `currency`
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=1, nullable=False)  # → project currency
    amount_project: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)  # in project currency
    status: Mapped[str] = mapped_column(String(12), default="draft", nullable=False, index=True)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(Text, default="")
    over_budget: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paid_at: Mapped[date | None] = mapped_column(Date)
    payment_method: Mapped[str] = mapped_column(String(10), default="")
    payment_ref: Mapped[str] = mapped_column(String(60), default="")

    project = relationship("Project", lazy="joined")
    budget_line = relationship("BudgetLine", lazy="joined")
    requested_by = relationship("User", foreign_keys=[requested_by_id], lazy="joined")
    decided_by = relationship("User", foreign_keys=[decided_by_id], lazy="joined")

    @property
    def status_label(self) -> str:
        return dict(EXPENSE_STATUSES).get(self.status, self.status)

    @property
    def payment_label(self) -> str:
        return dict(PAYMENT_METHODS).get(self.payment_method, "")


class ExchangeRate(TenantScopedMixin, TimestampMixin, db.Model):
    """How many units of the organization's base currency one unit of `currency` is worth."""

    __tablename__ = "exchange_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate_to_base: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    effective_on: Mapped[date] = mapped_column(Date, nullable=False)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (db.UniqueConstraint("organization_id", "currency", name="uq_exchange_rates_org_currency"),)
