"""Stage 7: weekly timesheets and time entries."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantScopedMixin, TimestampMixin, db

TIMESHEET_STATUSES = [("draft", "Draft"), ("submitted", "Awaiting approval"), ("approved", "Approved"),
                      ("rejected", "Returned")]


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


class Timesheet(TenantScopedMixin, TimestampMixin, db.Model):
    """One person's hours for one week (Monday to Sunday)."""

    __tablename__ = "timesheets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(12), default="draft", nullable=False, index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(Text, default="")

    user = relationship("User", foreign_keys=[user_id], lazy="joined")
    decided_by = relationship("User", foreign_keys=[decided_by_id], lazy="joined")
    entries = relationship("TimeEntry", back_populates="timesheet", cascade="all, delete-orphan",
                           order_by="TimeEntry.work_date")

    __table_args__ = (db.UniqueConstraint("user_id", "week_start", name="uq_timesheets_user_week"),)

    @property
    def status_label(self) -> str:
        return dict(TIMESHEET_STATUSES).get(self.status, self.status)

    @property
    def total_hours(self) -> Decimal:
        return sum((e.hours for e in self.entries), Decimal(0))

    @property
    def is_locked(self) -> bool:
        return self.status in ("submitted", "approved")

    @property
    def week_end(self) -> date:
        return self.week_start + timedelta(days=6)


class TimeEntry(TenantScopedMixin, db.Model):
    __tablename__ = "time_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    timesheet_id: Mapped[int] = mapped_column(ForeignKey("timesheets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    hours: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    note: Mapped[str] = mapped_column(String(255), default="")

    timesheet = relationship("Timesheet", back_populates="entries")
    project = relationship("Project", lazy="joined")
    task = relationship("Task", lazy="joined")
