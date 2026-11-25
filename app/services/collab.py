"""Stage 8: messaging, project discussions, announcements and meetings."""
from datetime import datetime, timedelta, timezone  # noqa: F401

from sqlalchemy import func, or_, select

from app.extensions import db
from app.models import (Announcement, Conversation, ConversationParticipant, Meeting, MeetingAttendee, Message, Project,
                        User, utcnow)

from . import audit
from . import projects as project_svc
from .notify import notify


class CollabError(ValueError):
    pass


# ---------------------------------------------------------------- conversations
def _participant(conv: Conversation, user_id: int) -> ConversationParticipant | None:
    return next((p for p in conv.participants if p.user_id == user_id), None)


def can_access(user: User, conv: Conversation) -> bool:
    if _participant(conv, user.id):
        return True
    if conv.kind == "project" and conv.project is not None:
        return project_svc.can_edit(user, conv.project) or any(m.user_id == user.id for m in conv.project.members)
    return False


def _active_users(ids) -> list[User]:
    users = db.session.scalars(select(User).where(User.id.in_(list(ids) or [-1]), User.is_active_user.is_(True))).all()
    return users


def direct(actor: User, other_id: int) -> Conversation:
    if other_id == actor.id:
        raise CollabError("Choose someone other than yourself.")
    other = _active_users([other_id])
    if not other:
        raise CollabError("Choose an active team member.")
    mine = select(ConversationParticipant.conversation_id).where(ConversationParticipant.user_id == actor.id)
    theirs = select(ConversationParticipant.conversation_id).where(ConversationParticipant.user_id == other_id)
    existing = db.session.scalar(select(Conversation).where(Conversation.kind == "direct", Conversation.id.in_(mine),
                                                            Conversation.id.in_(theirs)))
    if existing:
        return existing
    conv = Conversation(organization_id=actor.organization_id, kind="direct")
    conv.participants = [ConversationParticipant(organization_id=actor.organization_id, user_id=u)
                         for u in (actor.id, other_id)]
    db.session.add(conv)
    db.session.flush()
    return conv


def group(actor: User, title: str, user_ids: list[int]) -> Conversation:
    title = (title or "").strip()
    if len(title) < 2:
        raise CollabError("Give the group a name.")
    people = {u.id for u in _active_users(user_ids)} | {actor.id}
    if len(people) < 3:
        raise CollabError("A group needs at least two other people. For one person, start a direct message.")
    conv = Conversation(organization_id=actor.organization_id, kind="group", title=title[:120])
    conv.participants = [ConversationParticipant(organization_id=actor.organization_id, user_id=u) for u in people]
    db.session.add(conv)
    db.session.flush()
    return conv


def project_channel(actor: User, project: Project) -> Conversation:
    """Each project has one discussion; its team (and manager) are kept as participants."""
    conv = db.session.scalar(select(Conversation).where(Conversation.project_id == project.id))
    if conv is None:
        conv = Conversation(organization_id=project.organization_id, kind="project", project_id=project.id,
                            title=project.name[:120])
        db.session.add(conv)
        db.session.flush()
    wanted = {m.user_id for m in project.members} | ({project.manager_id} if project.manager_id else set())
    have = {p.user_id for p in conv.participants}
    for uid in wanted - have:
        conv.participants.append(ConversationParticipant(organization_id=project.organization_id, user_id=uid))
    if not can_access(actor, conv):
        raise CollabError("Only the project team can join this discussion.")
    if actor.id not in have | wanted:
        conv.participants.append(ConversationParticipant(organization_id=project.organization_id, user_id=actor.id))
    db.session.flush()
    return conv


def send(actor: User, conv: Conversation, body: str) -> Message:
    if not can_access(actor, conv):
        raise CollabError("You're not part of this conversation.")
    body = (body or "").strip()
    if not body:
        raise CollabError("Write a message first.")
    if len(body) > 5000:
        raise CollabError("Messages can be at most 5,000 characters.")
    if not _participant(conv, actor.id):
        conv.participants.append(ConversationParticipant(organization_id=conv.organization_id, user_id=actor.id))
    earlier = list(conv.messages)  # before this one is added
    msg = Message(organization_id=conv.organization_id, conversation_id=conv.id, author_id=actor.id, body=body)
    db.session.add(msg)
    now = utcnow()
    conv.last_message_at = now
    me = _participant(conv, actor.id)
    if me:
        me.last_read_at = now

    def already_unread(p: ConversationParticipant) -> bool:
        return any(m.author_id != p.user_id and (p.last_read_at is None or m.created_at > p.last_read_at)
                   for m in earlier)

    # one notification when a conversation first has something unread, not one per message
    recipients = [p.user_id for p in conv.participants if p.user_id != actor.id and not already_unread(p)]
    where = "" if conv.kind == "direct" else f" in {conv.title}"
    notify(recipients, "message", f"New message from {actor.full_name}{where}", f"/app/messages/{conv.id}", actor=actor)
    return msg


def mark_read(user: User, conv: Conversation) -> None:
    p = _participant(conv, user.id)
    if p:
        p.last_read_at = utcnow()


def inbox(user: User) -> list[dict]:
    convs = db.session.scalars(select(Conversation).join(ConversationParticipant).where(
        ConversationParticipant.user_id == user.id).order_by(Conversation.last_message_at.desc())).unique().all()
    out = []
    for c in convs:
        me = _participant(c, user.id)
        last = c.messages[-1] if c.messages else None
        unread = sum(1 for m in c.messages if m.author_id != user.id and
                     (me.last_read_at is None or m.created_at > me.last_read_at))
        out.append({"conv": c, "last": last, "unread": unread, "name": display_name(c, user)})
    return out


def unread_total(user: User) -> int:
    """Unread messages across all of the user's conversations, counted in the database (runs on every page)."""
    stmt = (select(func.count(Message.id))
            .join(ConversationParticipant, ConversationParticipant.conversation_id == Message.conversation_id)
            .where(ConversationParticipant.user_id == user.id,
                   or_(Message.author_id.is_(None), Message.author_id != user.id),
                   or_(ConversationParticipant.last_read_at.is_(None),
                       Message.created_at > ConversationParticipant.last_read_at)))
    return db.session.scalar(stmt) or 0


def display_name(conv: Conversation, viewer: User) -> str:
    if conv.kind == "direct":
        other = next((p.user for p in conv.participants if p.user_id != viewer.id), None)
        return other.full_name if other else "Conversation"
    return conv.title or "Group"


# ---------------------------------------------------------------- announcements
def announce(actor: User, title: str, body: str, pinned: bool) -> Announcement:
    if not actor.can("org.manage"):
        raise CollabError("Only organization admins can post announcements.")
    if len((title or "").strip()) < 3 or len((body or "").strip()) < 3:
        raise CollabError("Write a title and a message.")
    a = Announcement(organization_id=actor.organization_id, title=title.strip()[:160], body=body.strip(),
                     pinned=pinned, author_id=actor.id)
    db.session.add(a)
    db.session.flush()
    everyone = db.session.scalars(select(User.id).where(User.is_active_user.is_(True))).all()
    notify(everyone, "announcement", f"Announcement: {a.title}", "/app/announcements", actor=actor)
    audit.record("collab.announcement", summary=f"Posted announcement \u201c{a.title}\u201d", entity=a)
    return a


# ---------------------------------------------------------------- meetings
def can_edit_meeting(user: User, m: Meeting) -> bool:
    return m.organizer_id == user.id or user.can("org.manage") or (
        m.project is not None and project_svc.can_edit(user, m.project))


def save_meeting(actor: User, meeting: Meeting | None, *, title: str, starts_at: datetime, duration_min: int,
                 location: str, agenda: str, project_id: int | None, attendee_ids: list[int]) -> Meeting:
    if meeting is not None and not can_edit_meeting(actor, meeting):
        raise CollabError("Only the organizer can change this meeting.")
    if not (5 <= (duration_min or 0) <= 12 * 60):
        raise CollabError("Meetings last between 5 minutes and 12 hours.")
    project = db.session.get(Project, project_id) if project_id else None
    if project_id and project is None:
        raise CollabError("Choose a valid project.")
    people = {u.id for u in _active_users(attendee_ids)} | {actor.id}
    creating = meeting is None
    if creating:
        meeting = Meeting(organization_id=actor.organization_id, organizer_id=actor.id)
        db.session.add(meeting)
    before = {a.user_id for a in meeting.attendees}
    meeting.title, meeting.starts_at, meeting.duration_min = title.strip()[:160], starts_at, duration_min
    meeting.location, meeting.agenda, meeting.project_id = location.strip()[:255], agenda.strip(), project_id
    meeting.attendees = [a for a in meeting.attendees if a.user_id in people] + [
        MeetingAttendee(organization_id=actor.organization_id, user_id=u) for u in people - before]
    db.session.flush()
    when = starts_at.strftime("%a %d %b, %H:%M")
    new_people = people - before if not creating else people
    notify(new_people, "meeting", f"{actor.full_name} invited you to \u201c{meeting.title}\u201d ({when})",
           f"/app/meetings/{meeting.id}", actor=actor)
    if not creating:
        notify(before & people, "meeting", f"\u201c{meeting.title}\u201d was updated ({when})",
               f"/app/meetings/{meeting.id}", actor=actor)
    audit.record("collab.meeting_saved", summary=f"{'Scheduled' if creating else 'Updated'} meeting \u201c{meeting.title}\u201d",
                 entity=meeting)
    return meeting


def cancel_meeting(actor: User, m: Meeting) -> None:
    if not can_edit_meeting(actor, m):
        raise CollabError("Only the organizer can cancel this meeting.")
    m.cancelled = True
    notify([a.user_id for a in m.attendees], "meeting", f"\u201c{m.title}\u201d on {m.starts_at:%d %b} was cancelled",
           f"/app/meetings/{m.id}", actor=actor)


def save_minutes(actor: User, m: Meeting, minutes: str) -> None:
    if not (can_edit_meeting(actor, m) or any(a.user_id == actor.id for a in m.attendees)):
        raise CollabError("Only attendees can record minutes.")
    m.minutes = minutes.strip()
    audit.record("collab.minutes", summary=f"Recorded minutes for \u201c{m.title}\u201d", entity=m)


def ics(m: Meeting, org_name: str) -> str:
    """A calendar invite for Outlook, Google Calendar or Apple Calendar."""
    def esc(s):
        return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    start = m.starts_at.astimezone(timezone.utc)
    end = start + timedelta(minutes=m.duration_min)
    fmt = "%Y%m%dT%H%M%SZ"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Laari ProjectSphere//EN", "METHOD:PUBLISH", "BEGIN:VEVENT",
             f"UID:meeting-{m.id}@projectsphere", f"DTSTAMP:{utcnow().strftime(fmt)}", f"DTSTART:{start.strftime(fmt)}",
             f"DTEND:{end.strftime(fmt)}", f"SUMMARY:{esc(m.title)}", f"LOCATION:{esc(m.location)}",
             f"DESCRIPTION:{esc(m.agenda)}", f"ORGANIZER;CN={esc(m.organizer.full_name if m.organizer else org_name)}:mailto:no-reply@example.com",
             "STATUS:CANCELLED" if m.cancelled else "STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def upcoming_meetings(user: User, limit: int = 5) -> list[Meeting]:
    return db.session.scalars(select(Meeting).join(MeetingAttendee).where(
        MeetingAttendee.user_id == user.id, Meeting.cancelled.is_(False),
        Meeting.starts_at >= utcnow() - timedelta(hours=1)).order_by(Meeting.starts_at).limit(limit)).unique().all()


def meeting_count_for(user: User) -> int:
    return db.session.scalar(select(func.count(MeetingAttendee.id)).where(MeetingAttendee.user_id == user.id)) or 0
