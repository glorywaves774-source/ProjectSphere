"""Stage 10: risk register, issue log, vendors and the procurement cycle."""
import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from sqlalchemy import select, text

from app.extensions import db
from app.models import (Expense, Issue, Notification, ProcurementRequest, Project, Quotation, Risk, RiskReview,
                        Vendor, risk_level)
from app.services import procurement as psvc
from app.services import risks as rsvc
from app.tenancy import tenant_scope

from .conftest import add_member, login, make_org

T = date.today()


@pytest.fixture
def world(client, app):
    org_id, admin = make_org(plan="professional")
    pm = add_member(org_id, "kwame@hope.org", "project_manager", name="Kwame Asante")
    officer = add_member(org_id, "akosua@hope.org", "procurement_officer", name="Akosua Owusu")
    field = add_member(org_id, "efua@hope.org", "field_officer", name="Efua Sarpong")
    outsider = add_member(org_id, "kojo@hope.org", "field_officer", name="Kojo Ansah")
    staff = add_member(org_id, "kofi@hope.org", "staff", name="Kofi Boateng")
    login(client, "admin@hope.org")
    client.post("/app/projects/new", data={"name": "Clean Water", "category_id": 0, "department_id": 0,
                                           "manager_id": pm, "status": "active", "priority": "high", "currency": "GHS",
                                           "start_date": (T - timedelta(days=30)).isoformat(),
                                           "end_date": (T + timedelta(days=120)).isoformat()})
    with tenant_scope(org_id):
        pid = db.session.scalar(select(Project.id))
    client.post(f"/app/projects/{pid}/members", data={"m-user_id": field, "m-project_role": "Field officer"})
    for desc, qty, cost, cat in (("Hand pumps", "1", "30000", "equipment"), ("Training", "1", "4000", "training")):
        client.post(f"/app/projects/{pid}/budget/lines/new", data={"category": cat, "description": desc,
                                                                  "quantity": qty, "unit": "lot", "unit_cost": cost,
                                                                  "task_id": 0})
    client.post(f"/app/projects/{pid}/budget/submit")
    client.post(f"/app/projects/{pid}/budget/approve")
    c = {}
    for key, email in (("pm", "kwame@hope.org"), ("po", "akosua@hope.org"), ("f", "efua@hope.org"),
                       ("o", "kojo@hope.org"), ("s", "kofi@hope.org")):
        c[key] = app.test_client()
        login(c[key], email)
    return {"org": org_id, "admin": admin, "pm_id": pm, "officer": officer, "field": field, "outsider": outsider,
            "staff": staff, "pid": pid, **c}


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


def page_text(r):
    return r.get_data(as_text=True).replace("&#39;", "'").replace("&#34;", '"')


# ================================================================ risks
def add_risk(c, pid, **kw):
    data = {"project": pid, "title": "Rains delay drilling", "description": "", "category": "external",
            "likelihood": 3, "impact": 3, "response": "reduce", "mitigation": "Drill before the rains start.",
            "contingency": "", "owner_id": 0, "review_date": (T + timedelta(days=14)).isoformat()}
    data.update(kw)
    return c.post("/app/risks/new", data={k: str(v) for k, v in data.items()})


@pytest.mark.parametrize("score,level", [(1, "low"), (4, "low"), (5, "medium"), (9, "medium"), (10, "high"),
                                         (15, "high"), (16, "critical"), (25, "critical")])
def test_risk_bands(score, level):
    assert risk_level(score) == level


def test_risk_register_access_and_rules(world, client):
    assert add_risk(world["pm"], world["pid"]).status_code == 302           # project manager
    assert add_risk(world["f"], world["pid"]).status_code == 403            # field officer can't add risks
    assert world["s"].get("/app/risks").status_code == 403                  # staff: no risk access
    assert world["f"].get(f"/app/projects/{world['pid']}/risks").status_code == 200
    r = add_risk(world["pm"], world["pid"], likelihood=4, impact=4, mitigation="")
    assert "need a mitigation plan" in page_text(r)
    assert add_risk(world["pm"], world["pid"], likelihood=4, impact=4, mitigation="", response="accept",
                    title="Fuel price rises").status_code == 302
    risks = q(world["org"], Risk)
    assert [r.code for r in risks] == ["RSK-01", "RSK-02"] and risks[1].level == "critical"
    assert len(q(world["org"], RiskReview)) == 2   # each risk starts its history


def test_owner_notified_and_review_history(world, client):
    add_risk(world["pm"], world["pid"], owner_id=world["field"])
    (r,) = q(world["org"], Risk)
    assert any("You own risk RSK-01" in n.title for n in q(world["org"], Notification, user_id=world["field"]))
    f = world["f"]
    url = f"/app/risks/{r.id}"
    f.post(url, data={"action": "review", "rv-likelihood": 5, "rv-impact": 4, "rv-note": ""})
    assert q(world["org"], Risk)[0].score == 9     # a note is needed
    f.post(url, data={"action": "review", "rv-likelihood": 5, "rv-impact": 4, "rv-note": "Rains came early",
                      "rv-next_review": (T + timedelta(days=7)).isoformat()})
    r = q(world["org"], Risk)[0]
    assert (r.likelihood, r.impact, r.score, r.level) == (5, 4, 20, "critical")
    assert [x.score for x in q(world["org"], RiskReview)] == [9, 20]
    assert any("now critical" in n.title for n in q(world["org"], Notification, user_id=world["pm_id"]))
    # someone who doesn't own or manage it can't review
    world["o"].post(url, data={"action": "review", "rv-likelihood": 1, "rv-impact": 1, "rv-note": "fine"})
    assert q(world["org"], Risk)[0].score == 20


def test_escalate_risk_to_issue_and_issue_workflow(world, client):
    add_risk(world["pm"], world["pid"])
    (r,) = q(world["org"], Risk)
    resp = world["pm"].post(f"/app/risks/{r.id}", data={"action": "escalate", "es-priority": "high",
                                                       "es-assignee_id": world["field"], "es-description": "It rained"})
    assert resp.status_code == 302
    (i,) = q(world["org"], Issue)
    assert i.risk_id == r.id and i.code == "ISS-01" and i.assignee_id == world["field"]
    assert q(world["org"], Risk)[0].status == "occurred"
    url = f"/app/issues/{i.id}"
    f = world["f"]
    f.post(url, data={"action": "status", "s-status": "resolved", "s-resolution": ""})
    assert q(world["org"], Issue)[0].status == "open"           # needs a resolution
    f.post(url, data={"action": "status", "s-status": "resolved", "s-resolution": "Second rig hired"})
    assert q(world["org"], Issue)[0].status == "resolved"
    f.post(url, data={"action": "status", "s-status": "closed", "s-resolution": ""})
    assert q(world["org"], Issue)[0].status == "resolved"       # only managers close
    world["pm"].post(url, data={"action": "status", "s-status": "closed", "s-resolution": ""})
    assert q(world["org"], Issue)[0].status == "closed" and q(world["org"], Issue)[0].resolved_at is not None


def test_who_can_raise_issues(world, client):
    data = {"project": world["pid"], "title": "Pump handle broken", "priority": "high", "assignee_id": 0,
            "description": "", "due_date": ""}
    assert world["f"].post("/app/issues/new", data=data).status_code == 302      # on the team
    assert world["o"].post("/app/issues/new", data=data).status_code == 403      # not on the team
    (i,) = q(world["org"], Issue)
    assert i.raised_by_id == world["field"]
    assert any("High issue logged" in n.title for n in q(world["org"], Notification, user_id=world["pm_id"]))
    world["o"].post(f"/app/issues/{i.id}", data={"action": "status", "s-status": "resolved",
                                                 "s-resolution": "not mine"})
    assert q(world["org"], Issue)[0].status == "open"


def test_heatmap_and_filters(world, client):
    add_risk(world["pm"], world["pid"], likelihood=5, impact=5, title="Flood destroys site")
    add_risk(world["pm"], world["pid"], likelihood=5, impact=5, title="Contractor goes bankrupt")
    add_risk(world["pm"], world["pid"], likelihood=1, impact=2, title="Minor paperwork delays")
    with tenant_scope(world["org"]):
        s = rsvc.summary()
        assert s["heat"]["5-5"] == 2 and s["heat"]["1-2"] == 1 and s["levels"]["critical"] == 2
    body = page_text(client.get("/app/risks?cell=1-2"))
    assert "Minor paperwork delays" in body and "Flood destroys site" not in body


def test_risk_review_reminders_once(world, client):
    add_risk(world["pm"], world["pid"], owner_id=world["field"])
    with tenant_scope(world["org"]):
        r = db.session.scalar(select(Risk))
        r.review_date = T - timedelta(days=3)
        db.session.commit()
    assert rsvc.send_review_reminders() == 1
    assert rsvc.send_review_reminders() == 0
    assert len(q(world["org"], Notification, user_id=world["field"], kind="risk.review")) == 1


def test_risk_register_excel_is_safe(world, client):
    add_risk(world["pm"], world["pid"], title="=cmd|' /C calc'!A0 risk")
    wb = load_workbook(io.BytesIO(client.get("/app/risks.xlsx").data))
    values = [c.value for row in wb["Risk register"].iter_rows() for c in row]
    assert "'=cmd|' /C calc'!A0 risk" in values and "Issue log" in wb.sheetnames


# ================================================================ procurement
def vendor(c, name, tin="C001", category="goods"):
    return c.post("/app/vendors/new", data={"name": name, "category": category, "contact_name": "", "email": "",
                                            "phone": "", "address": "", "tax_id": tin, "registration_no": "",
                                            "notes": ""})


def vendors(world, client, names=("Alpha Pumps", "Beta Supplies", "Gamma Hardware")):
    for n in names:
        vendor(world["po"], n)
    out = q(world["org"], Vendor)
    for v in out:
        client.post(f"/app/vendors/{v.id}", data={"status": "approved"})   # a different officer approves
    return {v.name: v.id for v in q(world["org"], Vendor)}


def request(world, c=None, items=(("India Mark II pump", "4", "6000"),), line="Hand pumps", submit=True,
            responses=None, **kw):
    c = c or world["pm"]
    with tenant_scope(world["org"]):
        from app.models import BudgetLine

        line_id = db.session.scalar(select(BudgetLine.id).where(BudgetLine.description == line)) if line else 0
    data = {"project_id": world["pid"], "title": "Hand pumps for boreholes", "budget_line_id": line_id,
            "needed_by": (T + timedelta(days=20)).isoformat(), "delivery_location": "Field office",
            "justification": "Needed for the first four boreholes."}
    data.update(kw)
    for n, (d, qty, cost) in enumerate(items):
        data.update({f"item-{n}-description": d, f"item-{n}-quantity": qty, f"item-{n}-unit_cost": cost,
                     f"item-{n}-unit": "sets", f"item-{n}-specification": ""})
    if submit:
        data["submit_now"] = "1"
    r = c.post("/app/procurement/new", data={k: str(v) for k, v in data.items()})
    if responses is not None:
        responses.append(r)
    return q(world["org"], ProcurementRequest)[-1] if q(world["org"], ProcurementRequest) else None


def act(c, pr, **data):
    return c.post(f"/app/procurement/{pr.id}", data={k: str(v) for k, v in data.items()})


def get(world, pr):
    return q(world["org"], ProcurementRequest, id=pr.id)[0]


def test_method_follows_estimate(world, client):
    with tenant_scope(world["org"]):
        p = db.session.get(Project, world["pid"])
        assert psvc.method_for(Decimal("4999.99"), p) == "direct"
        assert psvc.method_for(Decimal("5000"), p) == "rfq"
        assert psvc.method_for(Decimal("49999"), p) == "rfq"
        assert psvc.method_for(Decimal("50000"), p) == "tender"
    pr = request(world)
    assert pr.method == "rfq" and pr.status == "submitted"


@pytest.mark.parametrize("items,msg", [
    ((), "Add at least one item"), ((("Pump", "0", "100"),), "Enter a quantity"),
    ((("Pump", "2", "0"),), "estimated unit cost"), ((("Pump", "two", "100"),), "must be a number"),
])
def test_request_item_validation(world, client, items, msg):
    out = []
    request(world, items=items, responses=out)
    assert q(world["org"], ProcurementRequest) == []
    assert msg in page_text(out[0])


def test_submit_needs_justification_and_drafts_are_private(world, client):
    pr = request(world, submit=False, justification="")
    assert pr.status == "draft"
    act(world["pm"], pr, action="submit")
    assert get(world, pr).status == "draft"
    assert world["f"].get(f"/app/procurement/{pr.id}").status_code == 403          # no procurement access
    with tenant_scope(world["org"]):
        p = db.session.get(ProcurementRequest, pr.id)
        p.justification = "Needed now"
        db.session.commit()
    act(world["pm"], pr, action="submit")
    assert get(world, pr).status == "submitted"
    assert any("waiting for approval" in n.title for n in q(world["org"], Notification, user_id=world["admin"]))


def test_approval_rules(world, client):
    pr = request(world, c=client)                                    # raised by the admin
    act(client, pr, action="approve")
    assert get(world, pr).status == "submitted"                      # can't approve your own
    pr = request(world)
    act(world["po"], pr, action="approve")
    assert get(world, pr).status == "submitted"                      # officer has no approval right
    act(client, pr, action="reject", **{"d-note": ""})
    assert get(world, pr).status == "submitted"                      # rejecting needs a reason
    act(client, pr, action="approve")
    assert get(world, pr).status == "approved"
    assert any("approved by" in n.title for n in q(world["org"], Notification, user_id=world["officer"]))


def test_approval_checks_budget_line(world, client):
    first = request(world, items=(("Pumps", "4", "6000"),))           # 24,000 of the 30,000 line
    act(client, first, action="approve")
    second = request(world, items=(("More pumps", "2", "6000"),))     # 12,000 more → 6,000 short
    assert get(world, second).over_budget
    r = act(client, second, action="approve")
    assert get(world, second).status == "submitted"
    assert "6,000.00 short" in page_text(client.get(r.headers["Location"]))
    act(client, second, action="approve", **{"d-allow_over": "y", "d-note": "ok"})
    assert get(world, second).status == "submitted"                  # override needs a real reason
    act(client, second, action="approve", **{"d-allow_over": "y", "d-note": "Donor approved the reallocation"})
    assert get(world, second).status == "approved" and get(world, second).over_budget
    third = request(world, line="")
    act(client, third, action="approve")
    assert get(world, third).status == "submitted"                   # needs a budget line


def test_vendor_controls(world, client):
    assert vendor(world["pm"], "Alpha").status_code == 403           # only officers register vendors
    vendor(world["po"], "Alpha Pumps", tin="")
    (v,) = q(world["org"], Vendor)
    assert v.status == "pending" and v.code == "VEN-0001"
    world["po"].post(f"/app/vendors/{v.id}", data={"status": "approved"})
    assert q(world["org"], Vendor)[0].status == "pending"            # can't approve your own registration
    client.post(f"/app/vendors/{v.id}", data={"status": "approved"})
    assert q(world["org"], Vendor)[0].status == "pending"            # needs a TIN
    assert "already has that name" in page_text(vendor(world["po"], "alpha pumps"))
    client.post(f"/app/vendors/{v.id}/edit", data={"name": "Alpha Pumps", "category": "goods", "tax_id": "C123"})
    client.post(f"/app/vendors/{v.id}", data={"status": "approved"})
    assert q(world["org"], Vendor)[0].status == "approved"
    client.post(f"/app/vendors/{v.id}", data={"status": "blacklisted", "note": ""})
    assert q(world["org"], Vendor)[0].status == "approved"           # reason required
    client.post(f"/app/vendors/{v.id}", data={"status": "blacklisted", "note": "Counterfeit parts"})
    assert q(world["org"], Vendor)[0].status == "blacklisted"


def quote(world, pr, vendor_id, amount, **kw):
    data = {"action": "quote", "q-vendor_id": vendor_id, "q-amount": amount, "q-currency": "GHS",
            "q-valid_until": (T + timedelta(days=30)).isoformat(), "q-document_id": 0}
    data.update({f"q-{k}": v for k, v in kw.items()})
    return act(world["po"], pr, **data)


def approved_request(world, client, **kw):
    pr = request(world, **kw)
    act(client, pr, action="approve")
    return get(world, pr)


def test_quotations_award_and_purchase_order(world, client):
    v = vendors(world, client)
    pr = approved_request(world, client)
    quote(world, pr, v["Alpha Pumps"], 23000, delivery_days=21)
    quote(world, pr, v["Alpha Pumps"], 22000)                         # one quote per vendor
    assert len(q(world["org"], Quotation)) == 1
    # RFQ needs three quotes: award with one needs a justification
    act(world["po"], pr, action="award", **{"a-quotation_id": q(world["org"], Quotation)[0].id})
    assert get(world, pr).status == "approved"
    quote(world, pr, v["Beta Supplies"], 24000, delivery_days=5)
    quote(world, pr, v["Gamma Hardware"], 25000)
    qs = {x.vendor_id: x.id for x in q(world["org"], Quotation)}
    # choosing a dearer quote needs a reason
    act(world["po"], pr, action="award", **{"a-quotation_id": qs[v["Beta Supplies"]], "a-justification": ""})
    assert get(world, pr).status == "approved"
    # the requester can't award
    act(world["pm"], pr, action="award", **{"a-quotation_id": qs[v["Alpha Pumps"]]})
    assert get(world, pr).status == "approved"
    act(world["po"], pr, action="award", **{"a-quotation_id": qs[v["Beta Supplies"]],
                                            "a-justification": "Alpha needs 21 days; crews would stand idle."})
    pr = get(world, pr)
    assert pr.status == "ordered" and pr.po_number == f"PO-{T.year}-0001"
    assert pr.awarded_amount_project == Decimal("24000.00")
    r = world["pm"].get(f"/app/procurement/{pr.id}/po.pdf")
    assert r.status_code == 200 and r.data[:5] == b"%PDF-"


def test_award_refuses_unapproved_or_expired(world, client):
    v = vendors(world, client, names=("Alpha Pumps",))
    vendor(world["po"], "Pending Co")
    pending = next(x.id for x in q(world["org"], Vendor) if x.name == "Pending Co")
    pr = approved_request(world, client, items=(("Kit", "1", "1000"),))   # single quotation
    quote(world, pr, pending, 900)
    act(world["po"], pr, action="award", **{"a-quotation_id": q(world["org"], Quotation)[0].id,
                                            "a-justification": "Only one available supplier in the area"})
    assert get(world, pr).status == "approved"                        # vendor not approved yet
    quote(world, pr, v["Alpha Pumps"], 950, valid_until=(T - timedelta(days=1)).isoformat())
    assert len(q(world["org"], Quotation)) == 1                       # expired quote refused


def test_foreign_currency_quote_converts(world, client):
    v = vendors(world, client, names=("Alpha Pumps",))
    pr = approved_request(world, client, items=(("Kit", "1", "1000"),))
    quote(world, pr, v["Alpha Pumps"], 50, currency="USD", exchange_rate="15.5")
    (qq,) = q(world["org"], Quotation)
    assert qq.currency == "USD" and qq.amount_project == Decimal("775.00")


def test_receipt_creates_approved_expense(world, client):
    from app.services import finance

    v = vendors(world, client, names=("Alpha Pumps",))
    pr = approved_request(world, client, items=(("Kit", "1", "1000"),))
    quote(world, pr, v["Alpha Pumps"], 900)
    act(world["po"], pr, action="award", **{"a-quotation_id": q(world["org"], Quotation)[0].id})
    assert get(world, pr).status == "ordered"
    act(world["pm"], pr, action="receive", **{"r-received_on": (T + timedelta(days=1)).isoformat(), "r-rating": 4})
    assert get(world, pr).status == "ordered"                         # future date refused
    act(world["pm"], pr, action="receive", **{"r-received_on": T.isoformat(), "r-rating": 4,
                                              "r-invoice_ref": "INV-9"})
    pr = get(world, pr)
    assert pr.status == "received" and pr.vendor_rating == 4 and pr.expense_id
    (e,) = q(world["org"], Expense)
    assert (e.status, e.amount_project, e.payee, e.invoice_ref) == ("approved", Decimal("900.00"), "Alpha Pumps",
                                                                     "INV-9")
    assert e.decided_by_id == world["admin"]                          # approved by the purchase's approver
    with tenant_scope(world["org"]):
        f = finance.project_finance(db.session.get(Project, world["pid"]))
        assert f["ac"] == Decimal("900.00")
        assert psvc.vendor_stats(db.session.get(Vendor, v["Alpha Pumps"]))["rating"] == 4.0


def test_cancel_and_register_export(world, client):
    pr = request(world)
    act(world["pm"], pr, action="cancel", note="")
    assert get(world, pr).status == "submitted"
    act(world["pm"], pr, action="cancel", note="Donor funded these directly")
    assert get(world, pr).status == "cancelled"
    wb = load_workbook(io.BytesIO(client.get("/app/procurement.xlsx").data))
    refs = [c.value for c in next(wb["Procurement"].iter_cols(min_col=1, max_col=1))]
    assert pr.reference in refs


# ================================================================ isolation
def test_other_organization_sees_nothing(world, client, app):
    add_risk(world["pm"], world["pid"])
    pr = request(world)
    vendor(world["po"], "Alpha Pumps")
    rid, vid = q(world["org"], Risk)[0].id, q(world["org"], Vendor)[0].id
    other_org, _ = make_org(name="Other NGO", email="boss@other.org")
    o = app.test_client()
    login(o, "boss@other.org")
    for url in (f"/app/risks/{rid}", f"/app/procurement/{pr.id}", f"/app/vendors/{vid}"):
        assert o.get(url).status_code == 404
    with tenant_scope(other_org):
        for t in ("risks", "issues", "vendors", "procurement_requests", "procurement_items", "quotations",
                  "risk_reviews", "issue_comments"):
            assert db.session.execute(text(f"SELECT count(*) FROM {t}")).scalar() == 0


def test_stage10_tables_have_rls():
    names = ["risks", "risk_reviews", "issues", "issue_comments", "vendors", "procurement_requests",
             "procurement_items", "quotations"]
    n = db.session.execute(text("SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
                                "AND relname = ANY(:n)"), {"n": names}).scalar()
    assert n == len(names)


def test_default_roles_updated():
    from app.security.catalog import DEFAULT_ROLES

    assert "procurement.view" in DEFAULT_ROLES["project_manager"][2]
    assert "risks.view" in DEFAULT_ROLES["field_officer"][2]
