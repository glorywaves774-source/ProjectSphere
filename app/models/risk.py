"""Stage 10: risk registers and issue logs."""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantScopedMixin, TimestampMixin, db, utcnow

RISK_CATEGORIES = [("strategic", "Strategic"), ("operational", "Operational"), ("financial", "Financial"),
                   ("compliance", "Legal / compliance"), ("safety", "Health & safety"),
                   ("security", "Security"), ("environmental", "Environmental"), ("reputational", "Reputational"),
                   ("technical", "Technical"), ("external", "External / political")]
RISK_RESPONSES = [("reduce", "Reduce"), ("avoid", "Avoid"), ("transfer", "Transfer / share"), ("accept", "Accept")]
RISK_STATUSES = [("open", "Open"), ("occurred", "Occurred (now an issue)"), ("closed", "Closed")]
LIKELIHOOD = [(1, "Rare"), (2, "Unlikely"), (3, "Possible"), (4, "Likely"), (5, "Almost certain")]
IMPACT = [(1, "Negligible"), (2, "Minor"), (3, "Moderate"), (4, "Major"), (5, "Severe")]
ISSUE_PRIORITIES = [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")]
ISSUE_STATUSES = [("open", "Open"), ("in_progress", "In progress"), ("resolved", "Resolved"), ("closed", "Closed")]


def risk_level(score: int) -> str:
    """5×5 matrix bands: 1–4 low, 5–9 medium, 10–15 high, 16–25 critical."""
    if score >= 16:
        return "critical"
    if score >= 10:
        return "high"
    if score >= 5:
        return "medium"
    return "low"


LEVEL_LABELS = {"low": "Low", "medium": "Medium", "high": "High", "critical": "Critical"}


class Risk(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "risks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")  # cause → event → consequence
    category: Mapped[str] = mapped_column(String(15), default="operational", nullable=False)
    likelihood: Mapped[int] = mapped_column(Integer, nullable=False)
    impact: Mapped[int] = mapped_column(Integer, nullable=False)
    response: Mapped[str] = mapped_column(String(10), default="reduce", nullable=False)
    mitigation: Mapped[str] = mapped_column(Text, default="")
    contingency: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(10), default="open", nullable=False, index=True)
    review_date: Mapped[date | None] = mapped_column(Date)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_note: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    project = relationship("Project", lazy="joined")
    owner = relationship("User", foreign_keys=[owner_id], lazy="joined")
    reviews = relationship("RiskReview", back_populates="risk", cascade="all, delete-orphan",
                           order_by="RiskReview.created_at.desc()")
    issues = relationship("Issue", back_populates="risk")

    __table_args__ = (db.UniqueConstraint("project_id", "seq", name="uq_risks_project_seq"),)

    @property
    def score(self) -> int:
        return self.likelihood * self.impact

    @property
    def level(self) -> str:
        return risk_level(self.score)

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS[self.level]

    @property
    def category_label(self) -> str:
        return dict(RISK_CATEGORIES).get(self.category, self.category)

    @property
    def response_label(self) -> str:
        return dict(RISK_RESPONSES).get(self.response, self.response)

    @property
    def status_label(self) -> str:
        return dict(RISK_STATUSES).get(self.status, self.status)

    @property
    def review_overdue(self) -> bool:
        return self.status == "open" and self.review_date is not None and self.review_date < date.today()


class RiskReview(TenantScopedMixin, db.Model):
    """A dated reassessment, so the register shows how each risk has moved."""

    __tablename__ = "risk_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    risk_id: Mapped[int] = mapped_column(ForeignKey("risks.id", ondelete="CASCADE"), nullable=False, index=True)
    likelihood: Mapped[int] = mapped_column(Integer, nullable=False)
    impact: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    risk = relationship("Risk", back_populates="reviews")
    reviewed_by = relationship("User", lazy="joined")

    @property
    def score(self) -> int:
        return self.likelihood * self.impact

    @property
    def level(self) -> str:
        return risk_level(self.score)


class Issue(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    risk_id: Mapped[int | None] = mapped_column(ForeignKey("risks.id", ondelete="SET NULL"), index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(10), default="medium", nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="open", nullable=False, index=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    raised_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    due_date: Mapped[date | None] = mapped_column(Date)
    resolution: Mapped[str] = mapped_column(Text, default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project = relationship("Project", lazy="joined")
    risk = relationship("Risk", back_populates="issues")
    assignee = relationship("User", foreign_keys=[assignee_id], lazy="joined")
    raised_by = relationship("User", foreign_keys=[raised_by_id], lazy="joined")
    comments = relationship("IssueComment", back_populates="issue", cascade="all, delete-orphan",
                            order_by="IssueComment.created_at")

    __table_args__ = (db.UniqueConstraint("project_id", "seq", name="uq_issues_project_seq"),)

    @property
    def is_open(self) -> bool:
        return self.status in ("open", "in_progress")

    @property
    def is_overdue(self) -> bool:
        return self.is_open and self.due_date is not None and self.due_date < date.today()

    @property
    def status_label(self) -> str:
        return dict(ISSUE_STATUSES).get(self.status, self.status)

    @property
    def priority_label(self) -> str:
        return dict(ISSUE_PRIORITIES).get(self.priority, self.priority)


class IssueComment(TenantScopedMixin, db.Model):
    __tablename__ = "issue_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    issue = relationship("Issue", back_populates="comments")
    author = relationship("User", lazy="joined")
