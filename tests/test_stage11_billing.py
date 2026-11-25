"""Stage 11: invoices, Paystack checkout / verification / webhook, renewals, admin billing, email outbox and API."""
import hashlib
import hmac
import io
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.extensions import db
from app.models import (ApiKey, AuditLog, EmailOutbox, Indicator, IndicatorValue, Invoice, Organization, Payment,
                        Plan, Project, Subscription, User)
from app.services import billing, email_outbox
from app.tenancy import tenant_scope

from .conftest import add_member, login, make_org, make_superadmin

T = date.today()
SECRET = "sk_test_unit_tests_only"


class FakePaystack:
    """Stands in for Paystack's API. `verify_data` controls what verification reports."""

    def __init__(self):
        self.calls, self.verify_data, self.init_ok, self.amounts = [], {}, True, {}

    def __call__(self, method, path, payload):
        self.calls.append((method, path, payload))
        if path == "/transaction/initialize":
            if not self.init_ok:
                return {"status": False, "message": "Invalid key"}
            self.amounts[payload["reference"]] = payload["amount"]
            return {"status": True, "data": {"authorization_url": "https://checkout.paystack.com/abc123",
                                             "reference": payload["reference"]}}
        if path.startswith("/transaction/verify/"):
            ref = path.rsplit("/", 1)[1]
            data = {"reference": ref, "status": "success", "currency": "GHS", "channel": "mobile_money",
                    "gateway_response": "Approved", "amount": self.amounts.get(ref, 0)}
            data.update(self.verify_data.get(ref, {}))
            return {"status": True, "data": data}
        return {"status": False}


@pytest.fixture
def paystack(app):
    fake = FakePaystack()
    app.extensions["paystack_http"] = fake
    yield fake
    app.extensions.pop("paystack_http", None)


@pytest.fixture
def world(client, app):
    org_id, admin = make_org(plan="starter")
    with tenant_scope(org_id):
        org = db.session.get(Organization, org_id)
        org.email = "accounts@hope.org"
        db.session.commit()
    login(client, "admin@hope.org")
    return {"org": org_id, "admin": admin}


def plan(code) -> Plan:
    with tenant_scope(platform=True):
        p = db.session.scalar(select(Plan).where(Plan.code == code))
        db.session.expunge(p)
        return p


def q(org, model, **where):
    with tenant_scope(org):
        db.session.expire_all()
        stmt = select(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        out = db.session.scalars(stmt.order_by(model.id)).all()
        for o in out:
            db.session.expunge(o)
        return out


def sub(org) -> Subscription:
    return q(org, Subscription)[0]


def checkout(client, code="professional", cycle="monthly"):
    return client.post("/app/billing/checkout", data={"plan": plan(code).id, "cycle": cycle})


def pay(client, world, paystack, **verify):
    inv = q(world["org"], Invoice, status="open")[0]
    r = client.post(f"/app/billing/invoices/{inv.id}/pay")
    assert r.status_code == 302 and r.headers["Location"] == "https://checkout.paystack.com/abc123"
    ref = q(world["org"], Payment)[-1].reference
    if verify:
        paystack.verify_data[ref] = verify
    return inv, ref, client.get(f"/app/billing/return?reference={ref}")


# ---------------------------------------------------------------- invoices and checkout
def test_choosing_a_plan_issues_one_invoice(world, client):
    r = checkout(client)
    assert r.status_code == 302
    (inv,) = q(world["org"], Invoice)
    p = plan("professional")
    assert inv.status == "open" and inv.amount == p.price_monthly and inv.kind == "new"
    assert inv.number.startswith(f"INV-{T.year}-") and inv.period_start == T
    assert inv.period_end == billing.cycle_end(T, "monthly") and inv.due_date == T + timedelta(days=7)
    checkout(client)                                         # same choice → same invoice
    assert len(q(world["org"], Invoice)) == 1
    checkout(client, cycle="annual")                         # different choice → old one voided
    invs = q(world["org"], Invoice)
    assert [i.status for i in invs] == ["void", "open"] and invs[1].amount == p.price_annual


def test_custom_priced_plan_and_downgrade_limits(world, client):
    r = checkout(client, "enterprise")
    assert q(world["org"], Invoice) == []
    assert "Contact our sales team" in client.get(r.headers["Location"]).get_data(as_text=True)
    for n in range(plan("starter").max_users):
        add_member(world["org"], f"user{n}@hope.org")
    with tenant_scope(world["org"]):
        org = db.session.get(Organization, world["org"])
        assert billing.usage_blockers(org, plan("starter"))   # 1 + max users > starter allows
        assert not billing.usage_blockers(org, plan("business"))


def test_only_subscription_managers_pay(world, client, app, paystack):
    add_member(world["org"], "kofi@hope.org", "staff")
    s = app.test_client()
    login(s, "kofi@hope.org")
    assert s.post("/app/billing/checkout", data={"plan": plan("professional").id, "cycle": "monthly"}).status_code == 403
    checkout(client)
    inv = q(world["org"], Invoice)[0]
    assert s.post(f"/app/billing/invoices/{inv.id}/pay").status_code == 403


def test_paystack_checkout_sends_org_email_and_exact_amount(world, client, paystack):
    checkout(client)
    inv = q(world["org"], Invoice)[0]
    client.post(f"/app/billing/invoices/{inv.id}/pay")
    method, path, payload = paystack.calls[0]
    assert path == "/transaction/initialize"
    assert payload["email"] == "accounts@hope.org"            # organization billing email, not a person's
    assert payload["amount"] == int(inv.amount * 100) and payload["currency"] == "GHS"
    assert payload["callback_url"] == "http://localhost/app/billing/return"
    (p,) = q(world["org"], Payment)
    assert p.status == "pending" and p.reference == payload["reference"]


def test_checkout_needs_billing_email_and_handles_gateway_errors(world, client, paystack):
    checkout(client)
    inv = q(world["org"], Invoice)[0]
    with tenant_scope(world["org"]):
        db.session.get(Organization, world["org"]).email = ""
        db.session.commit()
    r = client.post(f"/app/billing/invoices/{inv.id}/pay")
    assert r.headers["Location"].endswith(f"/app/billing/invoices/{inv.id}") and not paystack.calls
    with tenant_scope(world["org"]):
        db.session.get(Organization, world["org"]).email = "accounts@hope.org"
        db.session.commit()
    paystack.init_ok = False
    client.post(f"/app/billing/invoices/{inv.id}/pay")
    assert q(world["org"], Payment)[0].status == "failed"


def test_successful_payment_activates_plan_once(world, client, paystack):
    checkout(client)
    inv, ref, r = pay(client, world, paystack)
    assert r.status_code == 302
    inv = q(world["org"], Invoice)[0]
    assert inv.status == "paid" and inv.payment_method == "paystack" and inv.payment_reference == ref
    s = sub(world["org"])
    assert s.status == "active" and s.plan_id == plan("professional").id and s.trial_ends_at is None
    assert s.current_period_end.date() == inv.period_end
    assert q(world["org"], Payment)[0].channel == "mobile_money"
    # receipt queued for the organization's admin
    assert any("Payment received" in e.subject for e in q(world["org"], EmailOutbox))
    # coming back again (or a late webhook) changes nothing
    client.get(f"/app/billing/return?reference={ref}")
    with tenant_scope(world["org"]):
        assert db.session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "billing.invoice_paid")) == 1


@pytest.mark.parametrize("verify,status", [({"amount": 100}, "failed"), ({"currency": "USD"}, "failed"),
                                           ({"status": "abandoned"}, "abandoned"), ({"status": "failed"}, "failed")])
def test_payment_must_match_the_invoice(world, client, paystack, verify, status):
    checkout(client)
    inv = q(world["org"], Invoice)[0]
    if "amount" not in verify:
        verify = {"amount": int(inv.amount * 100), **verify}
    pay(client, world, paystack, **verify)
    assert q(world["org"], Invoice)[0].status == "open"
    assert q(world["org"], Payment)[0].status == status
    assert sub(world["org"]).status == "trialing"


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()


def test_webhook_signature_and_reverification(world, client, paystack):
    checkout(client)
    inv = q(world["org"], Invoice)[0]
    client.post(f"/app/billing/invoices/{inv.id}/pay")
    ref = q(world["org"], Payment)[0].reference
    paystack.verify_data[ref] = {"amount": int(inv.amount * 100)}
    body = json.dumps({"event": "charge.success", "data": {"reference": ref, "amount": 1}}).encode()
    anon = client.application.test_client()
    r = anon.post("/api/webhooks/paystack", data=body, headers={"X-Paystack-Signature": "0" * 128},
                  content_type="application/json")
    assert r.status_code == 401 and q(world["org"], Invoice)[0].status == "open"
    r = anon.post("/api/webhooks/paystack", data=body, headers={"X-Paystack-Signature": _sign(body)},
                  content_type="application/json")
    assert r.status_code == 200 and r.json["result"] == "ok"
    assert q(world["org"], Invoice)[0].status == "paid"       # amount came from verification, not the body
    r = anon.post("/api/webhooks/paystack", data=body, headers={"X-Paystack-Signature": _sign(body)},
                  content_type="application/json")
    assert r.status_code == 200                                # replay is harmless
    other = json.dumps({"event": "transfer.success", "data": {}}).encode()
    assert anon.post("/api/webhooks/paystack", data=other, headers={"X-Paystack-Signature": _sign(other)},
                     content_type="application/json").json["result"] == "ignored"


def test_plan_change_credits_unused_time(world, client, paystack):
    checkout(client)
    pay(client, world, paystack)
    paid = q(world["org"], Invoice)[0]
    with tenant_scope(world["org"]):                           # pretend half the month has gone
        inv = db.session.get(Invoice, paid.id)
        inv.period_start, inv.period_end = T - timedelta(days=15), T + timedelta(days=15)
        db.session.commit()
    checkout(client, "business")
    new = q(world["org"], Invoice, status="open")[0]
    expected = (paid.subtotal * Decimal(15) / Decimal(30)).quantize(Decimal("0.01"))
    assert new.kind == "change" and new.credit == expected
    assert new.amount == plan("business").price_monthly - expected


def test_invoice_pdf_and_isolation(world, client, app):
    checkout(client)
    inv = q(world["org"], Invoice)[0]
    r = client.get(f"/app/billing/invoices/{inv.id}.pdf")
    assert r.status_code == 200 and r.data[:5] == b"%PDF-"
    make_org(name="Other NGO", email="boss@other.org")
    o = app.test_client()
    login(o, "boss@other.org")
    assert o.get(f"/app/billing/invoices/{inv.id}").status_code == 404
    assert o.get(f"/app/billing/invoices/{inv.id}.pdf").status_code == 404


# ---------------------------------------------------------------- renewals
def test_renewal_invoice_then_lapse_then_recovery(world, client, paystack, app):
    checkout(client)
    pay(client, world, paystack)
    end = sub(world["org"]).current_period_end.date()
    with app.app_context():
        assert billing.run_renewals(today=end - timedelta(days=10)) == {"issued": 0, "lapsed": 0}
        assert billing.run_renewals(today=end - timedelta(days=5))["issued"] == 1
        assert billing.run_renewals(today=end - timedelta(days=4))["issued"] == 0   # once only
    ren = q(world["org"], Invoice, kind="renewal")[0]
    assert ren.period_start == end and ren.due_date == end
    with app.app_context():
        assert billing.run_renewals(today=end + timedelta(days=8))["lapsed"] == 1
    assert sub(world["org"]).status == "past_due"
    assert any("past due" in e.subject for e in q(world["org"], EmailOutbox))
    # paying the renewal restores the subscription and extends it
    client.post(f"/app/billing/invoices/{ren.id}/pay")
    ref = q(world["org"], Payment)[-1].reference
    client.get(f"/app/billing/return?reference={ref}")
    s = sub(world["org"])
    assert s.status == "active" and s.current_period_end.date() == ren.period_end


# ---------------------------------------------------------------- Super Admin console
def test_admin_records_offline_payment_and_issues_invoices(world, client, app):
    make_superadmin()
    a = app.test_client()
    login(a, "root@kayron.com")
    assert a.get("/admin/billing").status_code == 200
    r = a.post(f"/admin/organizations/{world['org']}/invoice", data={
        "inv-description": "Enterprise plan, 12 months", "inv-amount": "48000", "inv-currency": "GHS",
        "inv-due_date": (T + timedelta(days=14)).isoformat(), "inv-plan_id": plan("enterprise").id,
        "inv-cycle": "annual", "inv-period_start": T.isoformat()})
    assert r.status_code == 302
    inv = q(world["org"], Invoice)[0]
    assert inv.kind == "manual" and inv.amount == Decimal("48000.00")
    a.post(f"/admin/billing/invoices/{inv.id}", data={"action": "paid", "p-method": "bank", "p-reference": "",
                                                      "p-paid_on": T.isoformat()})
    assert q(world["org"], Invoice)[0].status == "open"        # reference needed
    a.post(f"/admin/billing/invoices/{inv.id}", data={"action": "paid", "p-method": "bank",
                                                      "p-reference": "GCB-77812", "p-paid_on": T.isoformat()})
    inv = q(world["org"], Invoice)[0]
    assert inv.status == "paid" and inv.payment_reference == "GCB-77812"
    s = sub(world["org"])
    assert s.plan_id == plan("enterprise").id and s.billing_cycle == "annual" and s.status == "active"
    # organization users can't reach the console
    assert client.get("/admin/billing").status_code == 403


def test_admin_void(world, client, app):
    make_superadmin()
    a = app.test_client()
    login(a, "root@kayron.com")
    checkout(client)
    inv = q(world["org"], Invoice)[0]
    a.post(f"/admin/billing/invoices/{inv.id}", data={"action": "void", "note": ""})
    assert q(world["org"], Invoice)[0].status == "open"
    a.post(f"/admin/billing/invoices/{inv.id}", data={"action": "void", "note": "Duplicate"})
    assert q(world["org"], Invoice)[0].status == "void"


# ---------------------------------------------------------------- email outbox
def test_notifications_are_emailed_to_people_who_want_them(world, client, app):
    from app.services.notify import notify

    kofi = add_member(world["org"], "kofi@hope.org", "staff")
    efua = add_member(world["org"], "efua@hope.org", "staff", name="Efua")
    k = app.test_client()
    login(k, "efua@hope.org")
    k.post("/app/account", data={"form": "notify"})            # unticked → off
    assert not q(world["org"], User, id=efua)[0].email_notify
    with app.test_request_context(), tenant_scope(world["org"]):
        notify([kofi, efua], "task", "Survey households is assigned to you", "/app/tasks/1",
               organization_id=world["org"])
        notify([kofi], "something.internal", "Not emailed", "/app/", organization_id=world["org"])
        db.session.commit()
    mail = q(world["org"], EmailOutbox)
    assert [m.to_address for m in mail] == ["kofi@hope.org"]
    assert "https://app.example.test/app/tasks/1" in mail[0].body_text and "My account" in mail[0].body_text


def test_outbox_flush(world, app, monkeypatch):
    from app.services import mailer

    with app.test_request_context(), tenant_scope(world["org"]):
        email_outbox.queue(db.session.get(User, world["admin"]), "Hello", "Body", "/app/")
        db.session.commit()
    with app.app_context():
        assert email_outbox.flush() == {"sent": 0, "not_sent": 0}   # no mail server: kept for later
    assert q(world["org"], EmailOutbox)[0].status == "queued"
    sent = []
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subj, body, html=None: sent.append(to) or True)
    with app.app_context():
        assert email_outbox.flush()["sent"] == 1
    m = q(world["org"], EmailOutbox)[0]
    assert m.status == "sent" and m.sent_at and sent == ["admin@hope.org"]


# ---------------------------------------------------------------- API
def make_key(client, scopes=("projects",), name="Power BI", expires=365):
    r = client.post("/app/integrations", data={"name": name, "scopes": list(scopes), "expires": expires})
    body = r.get_data(as_text=True)
    start = body.index('value="ps_live_') + len('value="')
    return body[start:body.index('"', start)], r


def professional(world):
    with tenant_scope(world["org"]):
        s = db.session.scalar(select(Subscription))
        s.plan_id, s.status = plan("professional").id, "active"
        s.current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
        db.session.commit()


def api(client, path, token, method="GET", **kw):
    return getattr(client, method.lower())(f"/api/v1{path}", headers={"Authorization": f"Bearer {token}"}, **kw)


def test_api_key_lifecycle_and_storage(world, client):
    r = client.post("/app/integrations", data={"name": "BI", "scopes": ["projects"], "expires": 90})
    assert "Professional plan" in r.get_data(as_text=True) and q(world["org"], ApiKey) == []   # starter: no API
    professional(world)
    token, r = make_key(client)
    assert r.headers["Cache-Control"] == "no-store" and token.startswith("ps_live_")
    (key,) = q(world["org"], ApiKey)
    assert key.key_hash == hashlib.sha256(token.encode()).hexdigest() and token not in key.prefix + key.name
    with tenant_scope(world["org"]):
        assert db.session.execute(text("SELECT count(*) FROM api_keys WHERE key_hash = :t"), {"t": token}).scalar() == 0
    assert token not in client.get("/app/integrations").get_data(as_text=True)   # shown once only
    assert api(client, "/projects", token).status_code == 200
    client.post("/app/integrations", data={"action": "revoke", "key": key.id})
    assert api(client, "/projects", token).status_code == 401


def test_api_scopes_isolation_and_pagination(world, client, app):
    professional(world)
    for n in range(3):
        client.post("/app/projects/new", data={"name": f"Project {n}", "category_id": 0, "department_id": 0,
                                               "manager_id": 0, "status": "active", "priority": "medium",
                                               "currency": "GHS", "start_date": T.isoformat(),
                                               "end_date": (T + timedelta(days=90)).isoformat()})
    token, _ = make_key(client, scopes=("projects",))
    anon = app.test_client()
    assert anon.get("/api/v1/projects").status_code == 401
    assert api(anon, "/projects", "ps_live_wrong").status_code == 401
    r = api(anon, "/projects?limit=2", token)
    assert r.status_code == 200 and r.json["meta"] == {"total": 3, "limit": 2, "offset": 0}
    assert [p["name"] for p in r.json["data"]] == ["Project 0", "Project 1"]
    pid = r.json["data"][0]["id"]
    assert api(anon, f"/projects/{pid}/tasks", token).status_code == 200
    assert api(anon, f"/projects/{pid}/budget", token).status_code == 403     # finance scope missing
    assert api(anon, "/risks", token).status_code == 403
    # another organization's key can't see these projects
    other_org, _ = make_org(name="Other NGO", email="boss@other.org", plan="professional")
    o = app.test_client()
    login(o, "boss@other.org")
    with tenant_scope(other_org):
        s = db.session.scalar(select(Subscription))
        s.status = "active"
        s.current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
        db.session.commit()
    other_token, _ = make_key(o)
    assert api(anon, "/projects", other_token).json["meta"]["total"] == 0
    assert api(anon, f"/projects/{pid}", other_token).status_code == 404
    # usage is recorded
    assert q(world["org"], ApiKey)[0].use_count == 2   # only allowed calls count


def test_api_blocks_expired_keys_and_lapsed_plans(world, client, app):
    professional(world)
    token, _ = make_key(client)
    with tenant_scope(world["org"]):
        s = db.session.scalar(select(Subscription))
        s.status = "past_due"
        db.session.commit()
    assert api(app.test_client(), "/projects", token).status_code == 402
    professional(world)
    with tenant_scope(world["org"]):
        k = db.session.scalar(select(ApiKey))
        k.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()
    assert api(app.test_client(), "/projects", token).status_code == 401


def test_api_indicator_results_wait_for_verification(world, client, app):
    professional(world)
    client.post("/app/projects/new", data={"name": "Clean Water", "category_id": 0, "department_id": 0,
                                           "manager_id": 0, "status": "active", "priority": "medium",
                                           "currency": "GHS", "start_date": (T - timedelta(days=60)).isoformat(),
                                           "end_date": (T + timedelta(days=90)).isoformat()})
    with tenant_scope(world["org"]):
        pid = db.session.scalar(select(Project.id))
    client.post(f"/app/projects/{pid}/indicators/new", data={
        "name": "Boreholes completed", "result_id": 0, "measure": "number", "unit": "", "direction": "increase",
        "baseline_value": "0", "baseline_date": (T - timedelta(days=60)).isoformat(), "target_value": "12",
        "target_date": (T + timedelta(days=90)).isoformat(), "frequency": "monthly", "responsible_id": 0})
    iid = q(world["org"], Indicator)[0].id
    read_only, _ = make_key(client, scopes=("me",), name="Dashboard")
    writer, _ = make_key(client, scopes=("me", "me:write"), name="Kobo sync")
    anon = app.test_client()
    body = {"period_end": (T - timedelta(days=3)).isoformat(), "value": 4, "source": "Kobo survey"}
    assert api(anon, f"/indicators/{iid}/values", read_only, "POST", json=body).status_code == 403
    r = api(anon, f"/indicators/{iid}/values", writer, "POST", json=body)
    assert r.status_code == 201 and r.json["data"]["status"] == "pending"
    assert api(anon, f"/indicators/{iid}/values", writer, "POST", json=body).status_code == 409
    assert api(anon, f"/indicators/{iid}/values", writer, "POST",
               json={"period_end": "31/10/2026", "value": 1}).status_code == 422
    assert api(anon, f"/indicators/{iid}/values", writer, "POST",
               json={"period_end": (T + timedelta(days=3)).isoformat(), "value": 1}).status_code == 422
    (v,) = q(world["org"], IndicatorValue)
    assert v.source == "Kobo survey" and v.status == "pending"
    data = api(anon, f"/indicators?project={pid}", read_only).json["data"][0]
    assert data["code"] == "IND-01" and data["actual"] is None and data["target"] == "12.00"


def test_stage11_tables_have_rls():
    names = ["invoices", "payments", "api_keys", "email_outbox"]
    n = db.session.execute(text("SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
                                "AND relname = ANY(:n)"), {"n": names}).scalar()
    assert n == len(names)
