"""Stage 8: documents, notifications, messaging, announcements and meetings."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantScopedMixin, TimestampMixin, db, utcnow

DOCUMENT_CATEGORIES = [
    ("report", "Report"),
    ("proposal", "Proposal"),
    ("workplan", "Work plan"),
    ("budget", "Budget"),
    ("minutes", "Meeting minutes"),
    ("contract", "Contract / agreement"),
    ("policy", "Policy / guideline"),
    ("media", "Photo / media"),
    ("other", "Other"),
]


class Document(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(20), default="other", nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Stage 9: photos and attachments from a field monitoring visit
    visit_id: Mapped[int | None] = mapped_column(ForeignKey("monitoring_visits.id", ondelete="SET NULL"), index=True)

    project = relationship("Project", lazy="joined")
    created_by = relationship("User", lazy="joined")
    versions = relationship("DocumentVersion", back_populates="document", cascade="all, delete-orphan",
                            order_by="DocumentVersion.version_no.desc()")
    comments = relationship("DocumentComment", back_populates="document", cascade="all, delete-orphan",
                            order_by="DocumentComment.created_at")

    @property
    def current(self):
        return self.versions[0] if self.versions else None

    @property
    def category_label(self) -> str:
        return dict(DOCUMENT_CATEGORIES).get(self.category, self.category)


class DocumentVersion(TenantScopedMixin, db.Model):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    extension: Mapped[str] = mapped_column(String(10), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    note: Mapped[str] = mapped_column(String(255), default="")
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    document = relationship("Document", back_populates="versions")
    uploaded_by = relationship("User", lazy="joined")

    __table_args__ = (db.UniqueConstraint("document_id", "version_no", name="uq_document_versions_doc_no"),)


class DocumentComment(TenantScopedMixin, db.Model):
    __tablename__ = "document_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    document = relationship("Document", back_populates="comments")
    author = relationship("User", lazy="joined")


class Notification(TenantScopedMixin, db.Model):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    link: Mapped[str] = mapped_column(String(255), default="")
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    actor = relationship("User", foreign_keys=[actor_id], lazy="joined")


class Conversation(TenantScopedMixin, TimestampMixin, db.Model):
    """A direct message, a group chat, or a project's discussion channel."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), default="direct", nullable=False)  # direct | group | project
    title: Mapped[str] = mapped_column(String(120), default="")
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    project = relationship("Project", lazy="joined")
    participants = relationship("ConversationParticipant", back_populates="conversation", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan",
                            order_by="Message.created_at")


class ConversationParticipant(TenantScopedMixin, db.Model):
    __tablename__ = "conversation_participants"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False,
                                                 index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    conversation = relationship("Conversation", back_populates="participants")
    user = relationship("User", lazy="joined")

    __table_args__ = (db.UniqueConstraint("conversation_id", "user_id", name="uq_conversation_participants_pair"),)


class Message(TenantScopedMixin, db.Model):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False,
                                                 index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    conversation = relationship("Conversation", back_populates="messages")
    author = relationship("User", lazy="joined")


class Announcement(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    author = relationship("User", lazy="joined")


class Meeting(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    duration_min: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    location: Mapped[str] = mapped_column(String(255), default="")  # room or video link
    agenda: Mapped[str] = mapped_column(Text, default="")
    minutes: Mapped[str] = mapped_column(Text, default="")
    organizer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    project = relationship("Project", lazy="joined")
    organizer = relationship("User", foreign_keys=[organizer_id], lazy="joined")
    attendees = relationship("MeetingAttendee", back_populates="meeting", cascade="all, delete-orphan")


class MeetingAttendee(TenantScopedMixin, db.Model):
    __tablename__ = "meeting_attendees"

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    meeting = relationship("Meeting", back_populates="attendees")
    user = relationship("User", lazy="joined")

    __table_args__ = (db.UniqueConstraint("meeting_id", "user_id", name="uq_meeting_attendees_pair"),)
