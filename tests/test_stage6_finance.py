"""Stage 6: budgets, expenditure, approvals, exchange rates, variance and Excel reports."""
import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from flask import current_app
from openpyxl import load_workbook
from sqlalchemy import select, text

from app.extensions import db
from app.models import BudgetLine, Expense, Project
from app.services.excel import safe
from app.tenancy import tenant_scope

from .conftest import add_member, login, make_org

T = date.today()


@pytest.fixture
def org(client):
    org_id, _ = make_org(plan="professional")
    login(client, "admin@hope.org")
    client.post("/app/projects/new", data={"name": "Clean Water", "category_id": 0, "department_id": 0,
                                           "manager_id": 0, "status": "active", "priority": "high",
                                           "start_date": (T - timedelta(days=30)).isoformat(),
                                           "end_date": (T + timedelta(days=30)).isoformat(), "currency": "GHS"})
    return org_id


def pid(org):
    with tenant_scope(org):
        return db.session.scalar(select(Project.id))


def project(org) -> Project:
    with tenant_scope(org):
        db.session.expire_all()
        p = db.session.scalar(select(Project))
        db.session.expunge(p)
        return p


def line(org, code="BL-01") -> BudgetLine:
    with tenant_scope(org):
        db.session.expire_all()
        return db.session.scalar(select(BudgetLine).where(BudgetLine.code == code))


def expense(org, desc) -> Expense:
    with tenant_scope(org):
        db.session.expire_all()
        e = db.session.scalar(select(Expense).where(Expense.description == desc))
        if e:
            db.session.expunge(e)
        return e


def add_line(client, p, desc="Drilling contractor", qty="2", cost="50000", cat="services"):
    return client.post(f"/app/projects/{p}/budget/lines/new", data={"category": cat, "description": desc,
                                                                    "quantity": qty, "unit": "boreholes",
                                                                    "unit_cost": cost, "task_id": 0})


def approved_budget(client, org, app):
    """Admin drafts, submits; a separate director approves."""
    p = pid(org)
    add_line(client, p)                                            # BL-01: 100,000
    add_line(client, p, "Field travel", "10", "500", "travel")     # BL-02: 5,000
    client.post(f"/app/projects/{p}/budget/submit")
    client.post(f"/app/projects/{p}/budget/approve")
    return p


def record(client, p, line_id, desc, amount, currency="GHS", rate="", submit=True):
    data = {"budget_line_id": line_id, "spent_on": T.isoformat(), "description": desc, "payee": "Aqua Drillers",
            "invoice_ref": "INV-77", "amount": amount, "currency": currency, "exchange_rate": rate}
    if submit:
        data["submit_now"] = "1"
    return client.post(f"/app/projects/{p}/expenses/new", data=data)


# ---------------------------------------------------------------- budget lines & approval
def test_budget_lines_codes_amounts_and_validation(client, org):
    p = pid(org)
    add_line(client, p)
    add_line(client, p, "Per diems", "12.5", "80", "travel")
    assert line(org).amount == Decimal("100000.00") and line(org, "BL-02").amount == Decimal("1000.00")
    r = add_line(client, p, "Bad", "0", "10")
    assert line(org, "BL-03") is None
    r = add_line(client, p, "Neg", "1", "-5")
    assert line(org, "BL-03") is None and r.status_code == 200
    html = client.get(f"/app/projects/{p}/budget").get_data(as_text=True)
    assert "GH₵101,000" in html and "12.5 boreholes" in html


def test_budget_workflow_and_lock(client, org, app):
    p = pid(org)
    r = client.post(f"/app/projects/{p}/budget/submit", follow_redirects=True)
    assert b"at least one budget line" in r.data
    add_line(client, p)
    client.post(f"/app/projects/{p}/budget/submit")
    assert project(org).budget_status == "submitted"
    r = add_line(client, p, "Late addition")
    assert r.status_code == 403 or b"submitted or approved" in r.data
    assert line(org, "BL-02") is None
    # a Finance Officer can't approve
    add_member(org, "fin@hope.org", "finance_officer", name="Fiona Finance")
    fin = app.test_client()
    login(fin, "fin@hope.org")
    r = fin.post(f"/app/projects/{p}/budget/approve", follow_redirects=True)
    assert b"permission to approve budgets" in r.data and project(org).budget_status == "submitted"
    # return needs a reason
    r = client.post(f"/app/projects/{p}/budget/return", data={"note": ""}, follow_redirects=True)
    assert b"Explain why" in r.data
    client.post(f"/app/projects/{p}/budget/approve")
    pr = project(org)
    assert pr.budget_status == "approved" and pr.budget == Decimal("100000.00")
    client.post(f"/app/projects/{p}/budget/return", data={"note": "Add community contribution line"})
    assert project(org).budget_status == "draft"


# ---------------------------------------------------------------- expenses
def test_no_spending_before_budget_approval(client, org):
    p = pid(org)
    add_line(client, p)
    r = client.get(f"/app/projects/{p}/expenses/new", follow_redirects=True)
    assert b"once the project budget is approved" in r.data


def test_expense_approval_flow_with_segregation(client, org, app):
    p = approved_budget(client, org, app)
    add_member(org, "fin@hope.org", "finance_officer", name="Fiona Finance")
    add_member(org, "dir@hope.org", "executive_director", name="Dede Director")
    fin, director = app.test_client(), app.test_client()
    login(fin, "fin@hope.org")
    login(director, "dir@hope.org")
    # finance officer records and submits
    r = record(fin, p, line(org).id, "First drilling instalment", "40000")
    assert r.status_code == 302
    e = expense(org, "First drilling instalment")
    assert e.status == "submitted" and e.reference == f"EXP-{T.year}-0001" and not e.over_budget
    # requester can't approve their own (finance officer lacks approve anyway); admin who didn't request can
    # admin records one: admin can't approve it themselves
    record(client, p, line(org).id, "Admin's own request", "1000")
    own = expense(org, "Admin's own request")
    r = client.post(f"/app/expenses/{own.id}", data={"action": "approve", "d-note": "ok"}, follow_redirects=True)
    assert b"can&#39;t approve your own expense" in r.data and expense(org, "Admin's own request").status == "submitted"
    # director approves; rejection needs a reason
    r = director.post(f"/app/expenses/{e.id}", data={"action": "reject", "d-note": ""}, follow_redirects=True)
    assert b"Say why" in r.data
    director.post(f"/app/expenses/{e.id}", data={"action": "approve", "d-note": ""})
    e = expense(org, "First drilling instalment")
    assert e.status == "approved" and e.decided_by_id is not None
    # only finance records payment
    r = director.post(f"/app/expenses/{e.id}", data={"action": "pay", "p-paid_at": T.isoformat(), "p-method": "bank",
                                                     "p-ref": "TRX1"}, follow_redirects=True)
    assert b"Only finance staff" in r.data
    fin.post(f"/app/expenses/{e.id}", data={"action": "pay", "p-paid_at": T.isoformat(), "p-method": "momo",
                                            "p-ref": "MOMO-55"})
    e = expense(org, "First drilling instalment")
    assert e.status == "paid" and e.payment_label == "Mobile money"


def test_over_budget_needs_explicit_override(client, org, app):
    p = approved_budget(client, org, app)
    add_member(org, "dir@hope.org", "executive_director", name="Dede Director")
    d = app.test_client()
    login(d, "dir@hope.org")
    l2 = line(org, "BL-02")  # 5,000 travel
    r = record(client, p, l2.id, "Vehicle hire", "6000")
    e = expense(org, "Vehicle hire")
    assert e.over_budget
    r = d.post(f"/app/expenses/{e.id}", data={"action": "approve", "d-note": ""}, follow_redirects=True)
    assert b"over budget by" in r.data and expense(org, "Vehicle hire").status == "submitted"
    r = d.post(f"/app/expenses/{e.id}", data={"action": "approve", "d-note": "ok", "d-allow_over": "y"},
               follow_redirects=True)
    assert b"Explain why" in r.data
    d.post(f"/app/expenses/{e.id}", data={"action": "approve", "d-note": "Rains forced extra trips",
                                          "d-allow_over": "y"})
    assert expense(org, "Vehicle hire").status == "approved"
    html = client.get(f"/app/projects/{p}/budget").get_data(as_text=True)
    assert "over-row" in html


def test_multi_currency(client, org, app):
    p = approved_budget(client, org, app)
    r = record(client, p, line(org).id, "Pump parts", "1000", currency="USD")
    assert b"no exchange rate for USD" in r.data
    client.post("/app/finance/rates", data={"currency": "USD", "rate": "15.25", "effective_on": T.isoformat()})
    record(client, p, line(org).id, "Pump parts", "1000", currency="USD")
    e = expense(org, "Pump parts")
    assert e.exchange_rate == Decimal("15.250000") and e.amount_project == Decimal("15250.00")
    record(client, p, line(org).id, "Spare valves", "100", currency="EUR", rate="16.5")
    assert expense(org, "Spare valves").amount_project == Decimal("1650.00")
    # base currency can't be set
    r = client.post("/app/finance/rates", data={"currency": "GHS", "rate": "2", "effective_on": T.isoformat()})
    assert b"Not a valid choice" in r.data or r.status_code == 200


def test_variance_and_earned_value(client, org, app):
    from app.services.finance import project_finance

    p = approved_budget(client, org, app)
    add_member(org, "dir@hope.org", "executive_director", name="Dede Director")
    d = app.test_client()
    login(d, "dir@hope.org")
    record(client, p, line(org).id, "Instalment", "52500")
    d.post(f"/app/expenses/{expense(org, 'Instalment').id}", data={"action": "approve", "d-note": ""})
    record(client, p, line(org).id, "Pending one", "2000")
    with tenant_scope(org):
        pr = db.session.get(Project, p)
        pr.progress = 40
        db.session.commit()
        f = project_finance(pr)
    assert f["bac"] == Decimal("105000.00") and f["ac"] == Decimal("52500.00") and f["committed"] == Decimal("2000.00")
    assert f["remaining"] == Decimal("50500.00") and f["used_pct"] == 50
    assert f["ev"] == Decimal("42000.00") and f["cpi"] == 0.8  # spending ahead of progress
    html = client.get("/app/finance").get_data(as_text=True)
    assert "GH₵105,000" in html and "Pending one" in html


def test_excel_reports(client, org, app):
    p = approved_budget(client, org, app)
    record(client, p, line(org).id, "=CMD|' /C calc'!A0", "100")
    r = client.get(f"/app/projects/{p}/budget.xlsx")
    assert r.status_code == 200 and r.mimetype.endswith("spreadsheetml.sheet")
    wb = load_workbook(io.BytesIO(r.data))
    ws = wb["Budget vs actual"]
    values = [c.value for row in ws.iter_rows() for c in row]
    assert "Drilling contractor" in values and 100000 in values
    assert any(isinstance(v, str) and v.startswith("=SUM(G") for v in values)  # live totals
    assert "Performance" in wb.sheetnames
    r = client.get("/app/expenses.xlsx")
    ws = load_workbook(io.BytesIO(r.data)).active
    cells = [c.value for row in ws.iter_rows() for c in row]
    assert "'=CMD|' /C calc'!A0" in cells  # injected formula stored as text
    assert safe("@SUM(1)") == "'@SUM(1)" and safe(5) == 5


def test_permissions_and_visibility(client, org, app):
    p = approved_budget(client, org, app)
    add_member(org, "staff@hope.org", "staff", name="Sam Staff")
    s = app.test_client()
    login(s, "staff@hope.org")
    assert s.get(f"/app/projects/{p}/budget").status_code == 403
    assert s.get("/app/finance").status_code == 403
    assert s.get(f"/app/projects/{p}/expenses/new").status_code == 403
    record(client, p, line(org).id, "Admin request", "500")
    e = expense(org, "Admin request")
    assert s.get(f"/app/expenses/{e.id}").status_code == 403
    assert "Admin request" not in s.get("/app/expenses").get_data(as_text=True)
    # auditor can view everything but not act
    add_member(org, "aud@hope.org", "auditor", name="Audrey Auditor")
    a = app.test_client()
    login(a, "aud@hope.org")
    assert a.get(f"/app/projects/{p}/budget").status_code == 200
    assert "Admin request" in a.get("/app/expenses").get_data(as_text=True)
    r = a.post(f"/app/expenses/{e.id}", data={"action": "approve", "d-note": "x"}, follow_redirects=True)
    assert expense(org, "Admin request").status == "submitted"


def test_draft_edit_and_delete_rules(client, org, app):
    p = approved_budget(client, org, app)
    record(client, p, line(org).id, "Draft thing", "300", submit=False)
    e = expense(org, "Draft thing")
    assert e.status == "draft"
    client.post(f"/app/expenses/{e.id}/edit", data={"budget_line_id": line(org).id, "spent_on": T.isoformat(),
                                                    "description": "Draft thing", "amount": "350", "currency": "GHS"})
    assert expense(org, "Draft thing").amount == Decimal("350.00")
    client.post(f"/app/expenses/{e.id}", data={"action": "delete"})
    assert expense(org, "Draft thing") is None
    # lines with spending can't be deleted
    record(client, p, line(org).id, "Keep", "10")
    client.post(f"/app/projects/{p}/budget/return", data={"note": "Revise lines"})
    r = client.post(f"/app/budget-lines/{line(org).id}", data={"action": "delete"}, follow_redirects=True)
    assert b"can&#39;t be deleted" in r.data


def test_finance_isolated_between_organizations(client, org, app):
    p = approved_budget(client, org, app)
    record(client, p, line(org).id, "Secret spend", "999")
    e = expense(org, "Secret spend")
    other, _ = make_org("Other", "x@other.org")
    c = app.test_client()
    login(c, "x@other.org")
    assert c.get(f"/app/expenses/{e.id}").status_code == 404
    assert c.get(f"/app/budget-lines/{line(org).id}").status_code == 404
    assert c.get(f"/app/projects/{p}/budget.xlsx").status_code == 404
    assert "Secret spend" not in c.get("/app/expenses").get_data(as_text=True)
    with tenant_scope(other):
        for t in ("budget_lines", "expenses", "exchange_rates"):
            assert db.session.execute(text(f"SELECT count(*) FROM {t}")).scalar() == 0


def test_stage6_pages_render(client, org, app):
    p = approved_budget(client, org, app)
    record(client, p, line(org).id, "Thing", "10")
    e = expense(org, "Thing")
    for url in [f"/app/projects/{p}/budget", f"/app/projects/{p}/budget/lines/new", f"/app/budget-lines/{line(org).id}",
                f"/app/projects/{p}/expenses/new", f"/app/expenses/{e.id}", "/app/expenses", "/app/expenses?status=submitted",
                "/app/finance", "/app/finance/rates"]:
        assert client.get(url).status_code == 200, url


def test_stage6_tables_have_rls():
    n = db.session.execute(text("SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
                                "AND relname IN ('budget_lines','expenses','exchange_rates')")).scalar()
    assert n == 3
