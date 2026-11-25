"""Stage 4: projects and the project portfolio."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantScopedMixin, TimestampMixin, db, utcnow

PROJECT_STATUSES = [
    ("planning", "Planning"),
    ("active", "Active"),
    ("on_hold", "On hold"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled"),
]
OPEN_PROJECT_STATUSES = {"planning", "active", "on_hold"}  # count towards the plan's project limit
PROJECT_PRIORITIES = [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")]
PROJECT_HEALTH = [("on_track", "On track"), ("at_risk", "At risk"), ("off_track", "Off track")]

DEFAULT_CATEGORIES = [
    ("Water & sanitation", "#0ea5e9"), ("Health", "#ef4444"), ("Education", "#f59e0b"),
    ("Agriculture & livelihoods", "#22c55e"), ("Infrastructure & construction", "#64748b"),
    ("Governance & policy", "#8b5cf6"), ("ICT & digital", "#2f7cf6"), ("Research & consulting", "#14b8a6"),
    ("Internal operations", "#6d5bf6"), ("Other", "#94a3b8"),
]


class ProjectCategory(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "project_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    color: Mapped[str] = mapped_column(String(7), default="#6d5bf6", nullable=False)

    __table_args__ = (db.UniqueConstraint("organization_id", "name", name="uq_project_categories_org_name"),)


class Project(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    objectives: Mapped[str] = mapped_column(Text, default="")
    category_id: Mapped[int | None] = mapped_column(ForeignKey("project_categories.id", ondelete="SET NULL"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"), index=True)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="planning", nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(10), default="medium", nullable=False)
    health: Mapped[str] = mapped_column(String(10), default="on_track", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0-100, from tasks in Stage 5
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)  # planned completion
    actual_end_date: Mapped[date | None] = mapped_column(Date)
    budget: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))  # planned total; detailed budgets in Stage 6
    currency: Mapped[str] = mapped_column(String(3), default="GHS", nullable=False)
    location: Mapped[str] = mapped_column(String(200), default="")
    funder: Mapped[str] = mapped_column(String(200), default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_summary: Mapped[str] = mapped_column(Text, default="")
    # Stage 6: budget approval
    budget_status: Mapped[str] = mapped_column(String(12), default="draft", server_default="draft", nullable=False)
    budget_approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    budget_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lessons_learned: Mapped[str] = mapped_column(Text, default="")

    category = relationship("ProjectCategory", lazy="joined")
    department = relationship("Department", lazy="joined")
    manager = relationship("User", foreign_keys=[manager_id], lazy="joined")
    budget_approved_by = relationship("User", foreign_keys=[budget_approved_by_id])
    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan",
                           order_by="ProjectMember.id")
    updates = relationship("ProjectUpdate", back_populates="project", cascade="all, delete-orphan",
                           order_by="ProjectUpdate.created_at.desc()")

    __table_args__ = (db.UniqueConstraint("organization_id", "code", name="uq_projects_org_code"),)

    # ---- derived -------------------------------------------------------
    @property
    def status_label(self) -> str:
        return dict(PROJECT_STATUSES).get(self.status, self.status)

    @property
    def priority_label(self) -> str:
        return dict(PROJECT_PRIORITIES).get(self.priority, self.priority)

    @property
    def health_label(self) -> str:
        return dict(PROJECT_HEALTH).get(self.health, self.health)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_PROJECT_STATUSES

    @property
    def is_overdue(self) -> bool:
        return self.is_open and self.end_date is not None and self.end_date < date.today()

    @property
    def is_delayed(self) -> bool:
        """Past its planned end date while still open, or reported off track."""
        return self.is_open and (self.is_overdue or self.health == "off_track")

    @property
    def days_left(self) -> int | None:
        if not self.end_date or not self.is_open:
            return None
        return (self.end_date - date.today()).days

    @property
    def time_elapsed_pct(self) -> int | None:
        if not (self.start_date and self.end_date) or self.end_date <= self.start_date:
            return None
        total = (self.end_date - self.start_date).days
        done = (min(date.today(), self.end_date) - self.start_date).days
        return max(0, min(100, round(done * 100 / total)))


class ProjectMember(TenantScopedMixin, db.Model):
    __tablename__ = "project_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_role: Mapped[str] = mapped_column(String(80), default="Team member")
    allocation_pct: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # share of the week
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    project = relationship("Project", back_populates="members")
    user = relationship("User", lazy="joined")

    __table_args__ = (db.UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),)


class ProjectUpdate(TenantScopedMixin, db.Model):
    """A dated status report on a project (and the record of every status/health change)."""

    __tablename__ = "project_updates"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str | None] = mapped_column(String(20))
    health: Mapped[str | None] = mapped_column(String(10))
    progress: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    project = relationship("Project", back_populates="updates")
    author = relationship("User", lazy="joined")
