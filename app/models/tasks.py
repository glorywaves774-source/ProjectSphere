"""Stage 5: tasks, work breakdown, dependencies and milestones."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantScopedMixin, TimestampMixin, db, utcnow

TASK_STATUSES = [
    ("todo", "To do"),
    ("in_progress", "In progress"),
    ("blocked", "Blocked"),
    ("review", "Awaiting approval"),
    ("done", "Done"),
]
TASK_PRIORITIES = [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("urgent", "Urgent")]


class Task(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # order among siblings
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="todo", nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(10), default="medium", nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    estimate_hours: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_milestone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    project = relationship("Project", lazy="joined")
    assignee = relationship("User", foreign_keys=[assignee_id], lazy="joined")
    approved_by = relationship("User", foreign_keys=[approved_by_id], lazy="joined")
    parent = relationship("Task", remote_side=[id], back_populates="children")
    children = relationship("Task", back_populates="parent", cascade="all, delete-orphan",
                            order_by="Task.position")
    comments = relationship("TaskComment", back_populates="task", cascade="all, delete-orphan",
                            order_by="TaskComment.created_at")

    @property
    def status_label(self) -> str:
        return dict(TASK_STATUSES).get(self.status, self.status)

    @property
    def priority_label(self) -> str:
        return dict(TASK_PRIORITIES).get(self.priority, self.priority)

    @property
    def is_done(self) -> bool:
        return self.status == "done"

    @property
    def is_overdue(self) -> bool:
        return not self.is_done and self.due_date is not None and self.due_date < date.today()

    @property
    def duration_days(self) -> int:
        if self.start_date and self.due_date:
            return max(1, (self.due_date - self.start_date).days + 1)
        return 1


class TaskDependency(TenantScopedMixin, db.Model):
    """Finish-to-start: the successor can't be completed until the predecessor is done."""

    __tablename__ = "task_dependencies"

    id: Mapped[int] = mapped_column(primary_key=True)
    predecessor_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    successor_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)

    predecessor = relationship("Task", foreign_keys=[predecessor_id], lazy="joined")
    successor = relationship("Task", foreign_keys=[successor_id], lazy="joined")

    __table_args__ = (db.UniqueConstraint("predecessor_id", "successor_id", name="uq_task_dependencies_pair"),)


class TaskComment(TenantScopedMixin, db.Model):
    __tablename__ = "task_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    task = relationship("Task", back_populates="comments")
    author = relationship("User", lazy="joined")
