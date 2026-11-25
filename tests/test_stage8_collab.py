"""Stage 8: documents (versions, validation, storage), notifications, messages, announcements and meetings."""
import hashlib
import io
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.extensions import db
from app.models import (Conversation, Document, DocumentVersion, Meeting, Message, Notification, Plan, Project,
                        Subscription, User)
from app.services import storage as storage_svc
from app.tenancy import tenant_scope

from .conftest import add_member, login, make_org

T = date.today()
PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def docx(extra=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return b.getvalue()


def upload(client, data, name, **form):
    payload = {"title": "", "category": "report", "project_id": "0", "description": ""}
    payload.update({k: str(v) for k, v in form.items()})
    payload["file"] = (io.BytesIO(data), name)
    return client.post("/app/documents/new", data=payload, content_type="multipart/form-data")


@pytest.fixture
def world(client, app):
    org_id, admin_id = make_org(plan="professional")
    login(client, "admin@hope.org")
    client.post("/app/projects/new", data={"name": "Clean Water", "category_id": 0, "department_id": 0,
                                           "manager_id": 0, "status": "active", "priority": "high", "currency": "GHS",
                                           "start_date": (T - timedelta(days=30)).isoformat(),
                                           "end_date": (T + timedelta(days=60)).isoformat()})
    with tenant_scope(org_id):
        pid = db.session.scalar(select(Project.id))
    kofi = add_member(org_id, "kofi@hope.org", "staff", name="Kofi Boateng")
    efua = add_member(org_id, "efua@hope.org", "staff", name="Efua Owusu")
    k = app.test_client()
    login(k, "kofi@hope.org")
    return {"org": org_id, "admin": admin_id, "pid": pid, "kofi": kofi, "efua": efua, "k": k}


def docs(org):
    with tenant_scope(org):
        db.session.expire_all()
        out = db.session.scalars(select(Document).order_by(Document.id)).all()
        for d in out:
            _ = [v.storage_key for v in d.versions]
        return out


def notes(org, user_id):
    with tenant_scope(org):
        db.session.expire_all()
        return [n.title for n in db.session.scalars(select(Notification).where(Notification.user_id == user_id)
                                                     .order_by(Notification.id))]


# ---------------------------------------------------------------- uploads & validation
def test_upload_pdf_stores_file_and_checksum(world, client, app):
    r = upload(client, PDF, "Annual report.pdf", title="Annual report 2026")
    assert r.status_code == 302
    (d,) = docs(world["org"])
    v = d.versions[0]
    assert d.title == "Annual report 2026" and v.version_no == 1 and v.extension == "pdf"
    assert v.size_bytes == len(PDF) and v.sha256 == hashlib.sha256(PDF).hexdigest()
    assert v.storage_key.startswith(f"{world['org']}/")
    with app.app_context():
        assert storage_svc.get_storage().get(v.storage_key) == PDF


def test_title_defaults_to_file_name(world, client):
    upload(client, docx(), "Work plan v2.docx")
    assert docs(world["org"])[0].title == "Work plan v2"


@pytest.mark.parametrize("data,name,msg", [
    (b"MZ\x90\x00 this is an exe", "invoice.pdf", "doesn't look like a real .pdf"),
    (b"MZ\x90\x00", "tool.exe", "file type isn't allowed"),
    (b"<script>alert(1)</script>", "page.html", "file type isn't allowed"),
    (docx({"word/vbaProject.bin": b"macro"}), "budget.docx", "doesn't look like a real .docx"),
    (b"PK\x03\x04 not really a zip", "sheet.xlsx", "doesn't look like a real .xlsx"),
    (b"abc\x00\x00\x01binary", "notes.txt", "doesn't look like a real .txt"),
    (b"", "empty.pdf", "file is empty"),
])
def test_dangerous_or_disguised_files_are_refused(world, client, data, name, msg):
    r = upload(client, data, name)
    assert r.status_code == 200
    assert msg in r.get_data(as_text=True).replace("&#39;", "'")
    assert docs(world["org"]) == []


def test_oversize_file_refused(world, client, app):
    app.config["MAX_UPLOAD_MB"] = 1
    try:
        r = upload(client, PDF + b"0" * (1024 * 1024 + 10), "big.pdf")
    finally:
        app.config["MAX_UPLOAD_MB"] = 20
    assert "at most 1 MB" in r.get_data(as_text=True)
    assert docs(world["org"]) == []


def test_path_traversal_filename_is_neutralised(world, client):
    upload(client, PDF, "../../etc/passwd.pdf")
    v = docs(world["org"])[0].versions[0]
    assert "/" not in v.filename and ".." not in v.filename


def test_storage_limit_blocks_uploads(world, client):
    with tenant_scope(platform=True):
        sub = db.session.scalar(select(Subscription).where(Subscription.organization_id == world["org"]))
        db.session.get(Plan, sub.plan_id).max_storage_mb = 1
        db.session.commit()
    assert upload(client, PDF, "a.pdf").status_code == 302
    r = upload(client, PDF, "b.pdf")
    assert "upgrade your plan" in r.get_data(as_text=True)
    assert len(docs(world["org"])) == 1


# ---------------------------------------------------------------- versions & downloads
def test_versions_download_and_integrity(world, client, app):
    upload(client, PDF, "plan.pdf", project_id=world["pid"])
    d = docs(world["org"])[0]
    v2 = PDF.replace(b"1.4", b"1.7")
    r = client.post(f"/app/documents/{d.id}", data={"action": "version", "v-note": "Donor edits",
                                                     "v-file": (io.BytesIO(v2), "plan-final.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    d = docs(world["org"])[0]
    assert [v.version_no for v in d.versions] == [2, 1] and d.versions[0].note == "Donor edits"
    old, new = d.versions[1], d.versions[0]
    r = client.get(f"/app/documents/{d.id}/versions/{old.id}/download")
    assert r.status_code == 200 and r.data == PDF
    assert r.headers["Content-Disposition"].startswith("attachment;")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in r.headers["Content-Security-Policy"]
    assert client.get(f"/app/documents/{d.id}/versions/{new.id}/download").data == v2
    # a tampered file is detected instead of being served
    with app.app_context():
        storage_svc.get_storage().put(new.storage_key, b"%PDF-tampered", "application/pdf")
    r = client.get(f"/app/documents/{d.id}/versions/{new.id}/download")
    assert r.status_code == 302


def test_version_id_must_belong_to_document(world, client):
    upload(client, PDF, "a.pdf")
    upload(client, PDF, "b.pdf")
    a, b = docs(world["org"])
    assert client.get(f"/app/documents/{a.id}/versions/{b.versions[0].id}/download").status_code == 404


def test_delete_removes_stored_files(world, client, app):
    upload(client, PDF, "a.pdf")
    d = docs(world["org"])[0]
    key = d.versions[0].storage_key
    assert client.post(f"/app/documents/{d.id}", data={"action": "delete"}).status_code == 302
    assert docs(world["org"]) == []
    with app.app_context(), pytest.raises(storage_svc.StorageError):
        storage_svc.get_storage().get(key)


def test_archive_hides_from_library(world, client):
    upload(client, PDF, "old.pdf", title="Old policy")
    d = docs(world["org"])[0]
    client.post(f"/app/documents/{d.id}", data={"action": "archive"})
    assert "Old policy" not in client.get("/app/documents").get_data(as_text=True)
    assert "Old policy" in client.get("/app/documents?archived=1").get_data(as_text=True)


# ---------------------------------------------------------------- permissions
def test_staff_upload_only_to_their_projects(world, client):
    k = world["k"]
    assert upload(k, PDF, "x.pdf", project_id=world["pid"]).status_code == 403  # on no team: nowhere to upload
    assert docs(world["org"]) == []
    client.post(f"/app/projects/{world['pid']}/members", data={"m-user_id": world["kofi"], "m-project_role": "Officer"})
    assert upload(k, PDF, "x.pdf", project_id=world["pid"]).status_code == 302
    d = docs(world["org"])[0]
    assert d.project_id == world["pid"] and d.created_by_id == world["kofi"]


def test_staff_cannot_delete_others_documents(world, client):
    upload(client, PDF, "a.pdf")
    d = docs(world["org"])[0]
    assert world["k"].post(f"/app/documents/{d.id}", data={"action": "delete"}).status_code == 302
    assert len(docs(world["org"])) == 1


def test_documents_isolated_between_organizations(world, client, app):
    upload(client, PDF, "secret.pdf", title="Secret plan")
    d = docs(world["org"])[0]
    make_org(name="Other NGO", email="boss@other.org")
    other = app.test_client()
    login(other, "boss@other.org")
    assert other.get(f"/app/documents/{d.id}").status_code == 404
    assert other.get(f"/app/documents/{d.id}/versions/{d.versions[0].id}/download").status_code == 404
    assert "Secret plan" not in other.get("/app/documents").get_data(as_text=True)


def test_s3_backend_with_encryption(app):
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="ps-docs")
        s = storage_svc.S3Storage("ps-docs", client=client)
        s.put("org/abc", b"hello", "text/plain")
        assert s.get("org/abc") == b"hello"
        assert client.head_object(Bucket="ps-docs", Key="org/abc")["ServerSideEncryption"] == "AES256"
        s.delete("org/abc")
        with pytest.raises(storage_svc.StorageError):
            s.get("org/abc")


def test_local_storage_rejects_traversal(tmp_path):
    s = storage_svc.LocalStorage(str(tmp_path))
    with pytest.raises(storage_svc.StorageError):
        s.put("../escape", b"x", "text/plain")


# ---------------------------------------------------------------- notifications
def test_notifications_bell_open_and_mark_read(world, client):
    k = world["k"]
    client.post(f"/app/projects/{world['pid']}/tasks/new", data={
        "title": "Survey households", "parent_id": 0, "assignee_id": world["kofi"], "priority": "medium",
        "start_date": T.isoformat(), "due_date": (T + timedelta(days=5)).isoformat()})
    titles = notes(world["org"], world["kofi"])
    assert any("Survey households" in t for t in titles)
    page = k.get("/app/").get_data(as_text=True)
    assert "Notifications, 1 unread" in page
    with tenant_scope(world["org"]):
        nid = db.session.scalar(select(Notification.id).where(Notification.user_id == world["kofi"]))
    r = k.get(f"/app/notifications/{nid}/open")
    assert r.status_code == 302 and r.headers["Location"].startswith("/app/tasks/")
    assert "Notifications, 1 unread" not in k.get("/app/").get_data(as_text=True)
    # someone else's notification can't be opened
    assert client.get(f"/app/notifications/{nid}/open").status_code == 404


def test_mark_all_read(world, client):
    client.post("/app/announcements", data={"title": "Hello team", "body": "Welcome to the new system", "action": "post"})
    k = world["k"]
    assert "1 unread" in k.get("/app/notifications").get_data(as_text=True)
    k.post("/app/notifications")
    assert "0 unread" in k.get("/app/notifications").get_data(as_text=True)


# ---------------------------------------------------------------- messages
def test_direct_message_flow_and_privacy(world, client, app):
    k = world["k"]
    r = k.post("/app/messages/new", data={"people": [world["admin"]], "title": ""})
    assert r.status_code == 302
    conv_url = r.headers["Location"]
    k.post(conv_url, data={"body": "Can we talk about the site visit?"})
    k.post(conv_url, data={"body": "Tomorrow works for me."})
    # admin gets one notification (not one per message) and sees the unread count
    assert sum("Kofi" in t for t in notes(world["org"], world["admin"])) == 1
    assert "Messages, 2 unread" in client.get("/app/").get_data(as_text=True)
    page = client.get(conv_url).get_data(as_text=True)
    assert "Tomorrow works for me." in page
    assert "Messages, 2 unread" not in client.get("/app/").get_data(as_text=True)
    # starting again reuses the same conversation
    assert k.post("/app/messages/new", data={"people": [world["admin"]]}).headers["Location"] == conv_url
    # a third person in the organization can't read it
    e = app.test_client()
    login(e, "efua@hope.org")
    assert e.get(conv_url).status_code == 403
    assert e.post(conv_url, data={"body": "sneaky"}).status_code == 403


def test_group_needs_name_and_project_channel(world, client):
    r = client.post("/app/messages/new", data={"people": [world["kofi"], world["efua"]], "title": ""})
    assert r.status_code == 200
    r = client.post("/app/messages/new", data={"people": [world["kofi"], world["efua"]], "title": "Field team"})
    assert r.status_code == 302
    with tenant_scope(world["org"]):
        conv = db.session.scalar(select(Conversation).where(Conversation.kind == "group"))
        assert conv.title == "Field team" and len(conv.participants) == 3
    r = client.get(f"/app/projects/{world['pid']}/discussion")
    assert r.status_code == 302
    r2 = client.get(f"/app/projects/{world['pid']}/discussion")
    assert r.headers["Location"] == r2.headers["Location"]
    with tenant_scope(world["org"]):
        assert db.session.scalar(select(Conversation).where(Conversation.kind == "project")).project_id == world["pid"]


def test_empty_message_rejected(world, client):
    r = client.post("/app/messages/new", data={"people": [world["kofi"]]})
    client.post(r.headers["Location"], data={"body": "   "})
    with tenant_scope(world["org"]):
        assert db.session.scalar(select(Message.id)) is None


# ---------------------------------------------------------------- announcements
def test_announcements_admin_only_and_everyone_notified(world, client):
    assert world["k"].post("/app/announcements", data={"title": "Hi all", "body": "From Kofi", "action": "post"}
                           ).status_code == 403
    client.post("/app/announcements", data={"title": "Office closed Friday", "body": "Public holiday.",
                                            "pinned": "y", "action": "post"})
    assert any("Office closed Friday" in t for t in notes(world["org"], world["kofi"]))
    assert any("Office closed Friday" in t for t in notes(world["org"], world["efua"]))
    assert not notes(world["org"], world["admin"])
    assert "Office closed Friday" in world["k"].get("/app/").get_data(as_text=True)  # on the dashboard


# ---------------------------------------------------------------- meetings
def _meeting(client, world, **kw):
    data = {"title": "Site planning", "day": (T + timedelta(days=2)).isoformat(), "time": "10:00",
            "duration_min": 60, "location": "Board room", "project_id": 0, "attendees": [world["kofi"]], "agenda": "Plan"}
    data.update(kw)
    return client.post("/app/meetings/new", data=data)


def test_meeting_schedule_notify_calendar_and_ics(world, client):
    r = _meeting(client, world)
    assert r.status_code == 302
    with tenant_scope(world["org"]):
        m = db.session.scalar(select(Meeting))
        mid, starts = m.id, m.starts_at
    assert starts.astimezone(timezone.utc) == datetime.combine(T + timedelta(days=2), datetime.min.time().replace(hour=10),
                                                              timezone.utc)  # Africa/Accra is UTC+0
    assert any("Site planning" in t for t in notes(world["org"], world["kofi"]))
    k = world["k"]
    assert "Site planning" in k.get("/app/meetings").get_data(as_text=True)
    month = (T + timedelta(days=2)).strftime("%Y-%m")
    assert "Site planning" in client.get(f"/app/calendar?month={month}").get_data(as_text=True)
    ics = k.get(f"/app/meetings/{mid}.ics")
    body = ics.get_data(as_text=True)
    assert ics.mimetype == "text/calendar" and "BEGIN:VEVENT" in body and "SUMMARY:Site planning" in body
    # Efua wasn't invited
    e = client.application.test_client()
    login(e, "efua@hope.org")
    assert e.get(f"/app/meetings/{mid}").status_code == 403
    assert e.get(f"/app/meetings/{mid}.ics").status_code == 403


def test_meeting_validation_and_minutes_and_cancel(world, client):
    assert _meeting(client, world, duration_min=2).status_code == 200
    _meeting(client, world)
    with tenant_scope(world["org"]):
        mid = db.session.scalar(select(Meeting.id))
    k = world["k"]
    assert k.post(f"/app/meetings/{mid}", data={"action": "minutes", "minutes": "Agreed on 3 sites."}).status_code == 302
    assert k.post(f"/app/meetings/{mid}", data={"action": "cancel"}).status_code == 302  # attendee can't cancel
    with tenant_scope(world["org"]):
        m = db.session.get(Meeting, mid)
        db.session.refresh(m)
        assert m.minutes == "Agreed on 3 sites." and not m.cancelled
    client.post(f"/app/meetings/{mid}", data={"action": "cancel"})
    with tenant_scope(world["org"]):
        m = db.session.get(Meeting, mid)
        db.session.refresh(m)
        assert m.cancelled
    assert any("ancel" in t for t in notes(world["org"], world["kofi"]))


# ---------------------------------------------------------------- database-level isolation
def test_stage8_tables_have_rls():
    names = ("documents", "document_versions", "document_comments", "notifications", "conversations",
             "conversation_participants", "messages", "announcements", "meetings", "meeting_attendees")
    n = db.session.execute(text("SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
                                "AND relname = ANY(:n)"), {"n": list(names)}).scalar()
    assert n == len(names)


def test_rls_hides_other_org_documents_at_database_level(world, client):
    upload(client, PDF, "a.pdf")
    other_org, _ = make_org(name="Other NGO", email="boss@other.org")
    with tenant_scope(other_org):
        assert db.session.execute(text("SELECT count(*) FROM documents")).scalar() == 0
        assert db.session.execute(text("SELECT count(*) FROM document_versions")).scalar() == 0
    with tenant_scope(world["org"]):
        assert db.session.execute(text("SELECT count(*) FROM documents")).scalar() == 1
