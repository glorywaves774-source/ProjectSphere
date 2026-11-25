"""Support requests: organizations ↔ Kayron support desk, with tenant isolation."""
from sqlalchemy import func, select, text

from app.extensions import db
from app.models import SupportMessage, SupportTicket
from app.tenancy import tenant_scope

from .conftest import add_member, login, make_org, make_superadmin

NEW = {"subject": "Cannot see my team", "category": "technical", "priority": "high",
       "body": "When I open Team the page is empty after signing in."}


def _ticket_id(subject="Cannot see my team"):
    with tenant_scope(platform=True):
        return db.session.scalar(select(SupportTicket.id).where(SupportTicket.subject == subject))


def test_full_support_conversation(app):
    make_org()
    make_superadmin()
    org, desk = app.test_client(), app.test_client()
    login(org, "admin@hope.org")
    r = org.post("/app/support/new", data=NEW)
    assert r.status_code == 302
    tid = _ticket_id()
    assert "Cannot see my team" in org.get("/app/support").get_data(as_text=True)

    login(desk, "root@kayron.com")
    assert "Cannot see my team" in desk.get("/admin/support").get_data(as_text=True)
    assert "Cannot see my team" in desk.get("/admin/").get_data(as_text=True)
    desk.post(f"/admin/support/{tid}", data={"form": "reply", "r-body": "Please clear your cache and retry.",
                                             "r-status": "waiting"})
    desk.post(f"/admin/support/{tid}", data={"form": "reply", "r-body": "Probably a browser issue, check logs.",
                                             "r-internal": "y", "r-status": "waiting"})

    page = org.get(f"/app/support/{tid}").get_data(as_text=True)
    assert "Please clear your cache" in page and "Kayron support" in page
    assert "check logs" not in page  # internal notes stay private
    assert "Waiting on you" in page

    org.post(f"/app/support/{tid}", data={"action": "reply", "body": "Still empty, sorry."})
    with tenant_scope(platform=True):
        t = db.session.get(SupportTicket, tid)
        assert t.status == "open" and t.assigned_to.email == "root@kayron.com"
        assert db.session.scalar(select(func.count(SupportMessage.id)).where(SupportMessage.ticket_id == tid)) == 4
    org.post(f"/app/support/{tid}", data={"action": "close"})
    db.session.expire_all()
    with tenant_scope(platform=True):
        assert db.session.get(SupportTicket, tid).status == "closed"


def test_staff_only_see_their_own_requests_admins_see_all(app):
    org_id, _ = make_org()
    add_member(org_id, "kofi@hope.org", "staff")
    add_member(org_id, "esi@hope.org", "staff", name="Esi Owusu")
    kofi, esi, admin = app.test_client(), app.test_client(), app.test_client()
    login(kofi, "kofi@hope.org")
    login(esi, "esi@hope.org")
    login(admin, "admin@hope.org")
    kofi.post("/app/support/new", data=NEW)
    tid = _ticket_id()
    assert "Cannot see my team" not in esi.get("/app/support").get_data(as_text=True)
    assert esi.get(f"/app/support/{tid}").status_code == 404
    assert "Cannot see my team" in admin.get("/app/support").get_data(as_text=True)
    assert admin.get(f"/app/support/{tid}").status_code == 200


def test_other_organizations_cannot_see_tickets(app):
    make_org("Alpha", "a@alpha.org")
    b, _ = make_org("Beta", "b@beta.org")
    alpha, beta = app.test_client(), app.test_client()
    login(alpha, "a@alpha.org")
    login(beta, "b@beta.org")
    alpha.post("/app/support/new", data=NEW)
    tid = _ticket_id()
    assert beta.get(f"/app/support/{tid}").status_code == 404
    assert "Cannot see my team" not in beta.get("/app/support?status=all").get_data(as_text=True)
    with tenant_scope(b):  # database layer agrees
        assert db.session.execute(text("SELECT count(*) FROM support_tickets")).scalar() == 0
        assert db.session.execute(text("SELECT count(*) FROM support_messages")).scalar() == 0


def test_support_validation_and_desk_filters(app):
    make_org()
    make_superadmin()
    c, desk = app.test_client(), app.test_client()
    login(c, "admin@hope.org")
    r = c.post("/app/support/new", data={"subject": "Hi", "category": "other", "priority": "normal", "body": "short"})
    assert r.status_code == 200 and b"between 4 and 160" in r.data and b"between 10 and 5000" in r.data
    c.post("/app/support/new", data=NEW)
    c.post("/app/support/new", data=dict(NEW, subject="Invoice question", priority="low", category="billing"))
    login(desk, "root@kayron.com")
    html = desk.get("/admin/support?priority=high").get_data(as_text=True)
    assert "Cannot see my team" in html and "Invoice question" not in html
    html = desk.get(f"/admin/support?q=PS-{_ticket_id('Invoice question'):05d}").get_data(as_text=True)
    assert "Invoice question" in html and "Cannot see my team" not in html
    tid = _ticket_id()
    desk.post(f"/admin/support/{tid}", data={"form": "triage", "t-status": "resolved", "t-priority": "urgent",
                                             "t-assigned_to_id": "0"})
    with tenant_scope(platform=True):
        t = db.session.get(SupportTicket, tid)
        assert (t.status, t.priority, t.assigned_to_id) == ("resolved", "urgent", None)
    assert "Cannot see my team" not in desk.get("/admin/support").get_data(as_text=True)  # not open any more
