"""Stage 10: vendors, procurement requests, quotations and purchase orders."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantScopedMixin, TimestampMixin, db, utcnow

VENDOR_CATEGORIES = [("goods", "Goods / supplies"), ("works", "Works / construction"), ("services", "Services"),
                     ("consultancy", "Consultancy"), ("it", "IT & equipment"), ("transport", "Transport & logistics"),
                     ("other", "Other")]
VENDOR_STATUSES = [("pending", "Pending approval"), ("approved", "Approved"), ("suspended", "Suspended"),
                   ("blacklisted", "Blacklisted")]
PROCUREMENT_STATUSES = [("draft", "Draft"), ("submitted", "Waiting for approval"), ("approved", "Approved"),
                        ("rejected", "Rejected"), ("ordered", "Ordered"), ("received", "Received"),
                        ("cancelled", "Cancelled")]
# Procurement methods by estimated value (in the organization's base currency) and the quotes each needs
PROCUREMENT_METHODS = [("direct", "Single quotation"), ("rfq", "Request for quotations"), ("tender", "Open tender")]
METHOD_MIN_QUOTES = {"direct": 1, "rfq": 3, "tender": 3}
METHOD_THRESHOLDS = [(Decimal("5000"), "direct"), (Decimal("50000"), "rfq")]  # at or above the last → tender


class Vendor(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(12), default="goods", nullable=False)
    contact_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    tax_id: Mapped[str] = mapped_column(String(40), default="")  # TIN / VAT number
    registration_no: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False, index=True)
    status_note: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    quotations = relationship("Quotation", back_populates="vendor")

    __table_args__ = (db.UniqueConstraint("organization_id", "seq", name="uq_vendors_org_seq"),
                      db.UniqueConstraint("organization_id", "name", name="uq_vendors_org_name"))

    @property
    def category_label(self) -> str:
        return dict(VENDOR_CATEGORIES).get(self.category, self.category)

    @property
    def status_label(self) -> str:
        return dict(VENDOR_STATUSES).get(self.status, self.status)


class ProcurementRequest(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "procurement_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    budget_line_id: Mapped[int | None] = mapped_column(ForeignKey("budget_lines.id", ondelete="SET NULL"), index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    reference: Mapped[str] = mapped_column(String(30), nullable=False)  # PR-2026-0001
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    justification: Mapped[str] = mapped_column(Text, default="")
    needed_by: Mapped[date | None] = mapped_column(Date)
    delivery_location: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(10), default="draft", nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(10), default="direct", nullable=False)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(Text, default="")
    over_budget: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # award
    awarded_quotation_id: Mapped[int | None] = mapped_column(
        ForeignKey("quotations.id", ondelete="SET NULL", use_alter=True, name="fk_procurement_requests_award"))
    award_justification: Mapped[str] = mapped_column(Text, default="")
    awarded_amount_project: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    po_seq: Mapped[int | None] = mapped_column(Integer)
    po_number: Mapped[str] = mapped_column(String(30), default="")
    ordered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # receipt
    received_on: Mapped[date | None] = mapped_column(Date)
    received_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    receipt_note: Mapped[str] = mapped_column(Text, default="")
    vendor_rating: Mapped[int | None] = mapped_column(Integer)  # 1–5, given on receipt
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expenses.id", ondelete="SET NULL"))
    cancel_note: Mapped[str] = mapped_column(Text, default="")

    project = relationship("Project", lazy="joined")
    budget_line = relationship("BudgetLine", lazy="joined")
    requested_by = relationship("User", foreign_keys=[requested_by_id], lazy="joined")
    decided_by = relationship("User", foreign_keys=[decided_by_id])
    ordered_by = relationship("User", foreign_keys=[ordered_by_id])
    received_by = relationship("User", foreign_keys=[received_by_id])
    items = relationship("ProcurementItem", back_populates="request", cascade="all, delete-orphan",
                         order_by="ProcurementItem.id")
    quotations = relationship("Quotation", back_populates="request", cascade="all, delete-orphan",
                              foreign_keys="Quotation.request_id", order_by="Quotation.amount_project")
    awarded_quotation = relationship("Quotation", foreign_keys=[awarded_quotation_id], post_update=True)
    expense = relationship("Expense")

    __table_args__ = (db.UniqueConstraint("organization_id", "seq", name="uq_procurement_requests_org_seq"),)

    @property
    def status_label(self) -> str:
        return dict(PROCUREMENT_STATUSES).get(self.status, self.status)

    @property
    def method_label(self) -> str:
        return dict(PROCUREMENT_METHODS).get(self.method, self.method)

    @property
    def estimated_total(self) -> Decimal:
        return sum((i.total for i in self.items), Decimal("0.00"))

    @property
    def min_quotes(self) -> int:
        return METHOD_MIN_QUOTES[self.method]


class ProcurementItem(TenantScopedMixin, db.Model):
    __tablename__ = "procurement_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("procurement_requests.id", ondelete="CASCADE"), nullable=False,
                                            index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    specification: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), default="")
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)  # estimate, project currency

    request = relationship("ProcurementRequest", back_populates="items")

    @property
    def total(self) -> Decimal:
        return (self.quantity * self.unit_cost).quantize(Decimal("0.01"))


class Quotation(TenantScopedMixin, db.Model):
    __tablename__ = "quotations"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("procurement_requests.id", ondelete="CASCADE"), nullable=False,
                                            index=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=1, nullable=False)
    amount_project: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    delivery_days: Mapped[int | None] = mapped_column(Integer)
    valid_until: Mapped[date | None] = mapped_column(Date)
    reference: Mapped[str] = mapped_column(String(60), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    recorded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    request = relationship("ProcurementRequest", back_populates="quotations", foreign_keys=[request_id])
    vendor = relationship("Vendor", back_populates="quotations", lazy="joined")
    document = relationship("Document")

    __table_args__ = (db.UniqueConstraint("request_id", "vendor_id", name="uq_quotations_request_vendor"),)
