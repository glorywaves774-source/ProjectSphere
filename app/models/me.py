"""Stage 9: monitoring and evaluation — results frameworks, indicators, field visits and evaluations."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantScopedMixin, TimestampMixin, db, utcnow

RESULT_LEVELS = [("goal", "Goal (impact)"), ("outcome", "Outcome"), ("output", "Output")]
INDICATOR_MEASURES = [("number", "Number"), ("percent", "Percentage (0–100)")]
INDICATOR_DIRECTIONS = [("increase", "Higher is better"), ("decrease", "Lower is better")]
FREQUENCIES = [("monthly", "Monthly"), ("quarterly", "Quarterly"), ("biannual", "Every six months"),
               ("annual", "Yearly"), ("once", "Once (at the end)")]
FREQUENCY_MONTHS = {"monthly": 1, "quarterly": 3, "biannual": 6, "annual": 12, "once": None}
VALUE_STATUSES = [("pending", "Waiting for verification"), ("verified", "Verified"), ("rejected", "Rejected")]
VISIT_STATUSES = [("draft", "Draft"), ("submitted", "Submitted"), ("reviewed", "Reviewed")]
VISIT_RATINGS = [("good", "Going well"), ("concerns", "Some concerns"), ("serious", "Serious problems")]
EVALUATION_KINDS = [("baseline", "Baseline study"), ("midterm", "Mid-term evaluation"),
                    ("endline", "Endline / final evaluation"), ("impact", "Impact evaluation"),
                    ("special", "Special review")]
EVALUATION_STATUSES = [("planned", "Planned"), ("in_progress", "In progress"), ("completed", "Completed")]
# OECD-DAC evaluation criteria, each rated 1 (unsatisfactory) to 4 (highly satisfactory)
DAC_CRITERIA = [("relevance", "Relevance"), ("coherence", "Coherence"), ("effectiveness", "Effectiveness"),
                ("efficiency", "Efficiency"), ("impact", "Impact"), ("sustainability", "Sustainability")]
RATING_SCALE = [(4, "Highly satisfactory"), (3, "Satisfactory"), (2, "Partly satisfactory"), (1, "Unsatisfactory")]


class ResultItem(TenantScopedMixin, TimestampMixin, db.Model):
    """One statement in a project's results framework (goal → outcomes → outputs)."""

    __tablename__ = "result_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("result_items.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    code: Mapped[str] = mapped_column(String(12), default="", nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    assumptions: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    project = relationship("Project")
    parent = relationship("ResultItem", remote_side="ResultItem.id", back_populates="children")
    children = relationship("ResultItem", back_populates="parent", order_by="ResultItem.sort_order",
                            cascade="all, delete-orphan")
    indicators = relationship("Indicator", back_populates="result", order_by="Indicator.seq")

    @property
    def level_label(self) -> str:
        return dict(RESULT_LEVELS).get(self.level, self.level)


class Indicator(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "indicators"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    result_id: Mapped[int | None] = mapped_column(ForeignKey("result_items.id", ondelete="SET NULL"), index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    definition: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(40), default="")
    measure: Mapped[str] = mapped_column(String(10), default="number", nullable=False)
    direction: Mapped[str] = mapped_column(String(10), default="increase", nullable=False)
    baseline_value: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0, nullable=False)
    baseline_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_value: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    endline_value: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    endline_date: Mapped[date | None] = mapped_column(Date)
    frequency: Mapped[str] = mapped_column(String(10), default="quarterly", nullable=False)
    data_source: Mapped[str] = mapped_column(Text, default="")  # means of verification
    disaggregation: Mapped[str] = mapped_column(String(255), default="")
    responsible_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    project = relationship("Project")
    result = relationship("ResultItem", back_populates="indicators")
    responsible = relationship("User", lazy="joined")
    values = relationship("IndicatorValue", back_populates="indicator", cascade="all, delete-orphan",
                          order_by="IndicatorValue.period_end.desc(), IndicatorValue.id.desc()")

    __table_args__ = (db.UniqueConstraint("project_id", "seq", name="uq_indicators_project_seq"),)

    @property
    def is_percent(self) -> bool:
        return self.measure == "percent"

    @property
    def frequency_label(self) -> str:
        return dict(FREQUENCIES).get(self.frequency, self.frequency)


class IndicatorValue(TenantScopedMixin, db.Model):
    """An actual value reported for an indicator at the end of a reporting period."""

    __tablename__ = "indicator_values"

    id: Mapped[int] = mapped_column(primary_key=True)
    indicator_id: Mapped[int] = mapped_column(ForeignKey("indicators.id", ondelete="CASCADE"), nullable=False,
                                              index=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False, index=True)
    reported_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    indicator = relationship("Indicator", back_populates="values")
    reported_by = relationship("User", foreign_keys=[reported_by_id], lazy="joined")
    verified_by = relationship("User", foreign_keys=[verified_by_id], lazy="joined")

    @property
    def status_label(self) -> str:
        return dict(VALUE_STATUSES).get(self.status, self.status)


class MonitoringVisit(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "monitoring_visits"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    reference: Mapped[str] = mapped_column(String(30), nullable=False)
    visit_date: Mapped[date] = mapped_column(Date, nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    purpose: Mapped[str] = mapped_column(Text, default="")
    people_met: Mapped[str] = mapped_column(Text, default="")
    findings: Mapped[str] = mapped_column(Text, default="")
    recommendations: Mapped[str] = mapped_column(Text, default="")
    rating: Mapped[str] = mapped_column(String(10), default="good", nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="draft", nullable=False, index=True)
    visited_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str] = mapped_column(Text, default="")

    project = relationship("Project", lazy="joined")
    visited_by = relationship("User", foreign_keys=[visited_by_id], lazy="joined")
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id], lazy="joined")
    photos = relationship("Document", order_by="Document.id", viewonly=True,
                          primaryjoin="Document.visit_id == MonitoringVisit.id")

    __table_args__ = (db.UniqueConstraint("organization_id", "seq", name="uq_monitoring_visits_org_seq"),)

    @property
    def status_label(self) -> str:
        return dict(VISIT_STATUSES).get(self.status, self.status)

    @property
    def rating_label(self) -> str:
        return dict(VISIT_RATINGS).get(self.rating, self.rating)

    @property
    def has_gps(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class Evaluation(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), default="midterm", nullable=False)
    evaluator: Mapped[str] = mapped_column(String(200), default="")
    is_external: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="planned", nullable=False)
    scope: Mapped[str] = mapped_column(Text, default="")
    findings: Mapped[str] = mapped_column(Text, default="")
    recommendations: Mapped[str] = mapped_column(Text, default="")
    management_response: Mapped[str] = mapped_column(Text, default="")
    relevance: Mapped[int | None] = mapped_column(Integer)
    coherence: Mapped[int | None] = mapped_column(Integer)
    effectiveness: Mapped[int | None] = mapped_column(Integer)
    efficiency: Mapped[int | None] = mapped_column(Integer)
    impact: Mapped[int | None] = mapped_column(Integer)
    sustainability: Mapped[int | None] = mapped_column(Integer)
    report_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    project = relationship("Project", lazy="joined")
    report_document = relationship("Document", lazy="joined")

    @property
    def kind_label(self) -> str:
        return dict(EVALUATION_KINDS).get(self.kind, self.kind)

    @property
    def status_label(self) -> str:
        return dict(EVALUATION_STATUSES).get(self.status, self.status)

    @property
    def ratings(self) -> list[tuple[str, str, int | None]]:
        return [(k, label, getattr(self, k)) for k, label in DAC_CRITERIA]

    @property
    def overall(self) -> float | None:
        rated = [r for _, _, r in self.ratings if r]
        return round(sum(rated) / len(rated), 1) if rated else None
