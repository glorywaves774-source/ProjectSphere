"""Stage 9: results frameworks, indicators, verification, field visits, evaluations, reports and reminders."""
import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from sqlalchemy import select, text

from app.extensions import db
from app.models import (Evaluation, Indicator, IndicatorValue, MonitoringVisit, Notification, Project, ResultItem,
                        User)
from app.services import me as svc
from app.tenancy import tenant_scope

from .conftest import add_member, login, make_org

T = date.today()
START, END = T - timedelta(days=100), T + timedelta(days=100)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def world(client, app):
    org_id, admin = make_org(plan="professional")
    login(client, "admin@hope.org")
    client.post("/app/projects/new", data={"name": "Clean Water", "category_id": 0, "department_id": 0,
                                           "manager_id": 0, "status": "active", "priority": "high", "currency": "GHS",
                                           "start_date": START.isoformat(), "end_date": END.isoformat()})
    with tenant_scope(org_id):
        pid = db.session.scalar(select(Project.id))
    meo = add_member(org_id, "yaw@hope.org", "me_officer", name="Yaw Mensah")
    field = add_member(org_id, "efua@hope.org", "field_officer", name="Efua Sarpong")
    other_field = add_member(org_id, "kojo@hope.org", "field_officer", name="Kojo Ansah")
    staff = add_member(org_id, "kofi@hope.org", "staff", name="Kofi Boateng")
    client.post(f"/app/projects/{pid}/members", data={"m-user_id": field, "m-project_role": "Field officer"})
    clients = {}
    for key, email in (("m", "yaw@hope.org"), ("f", "efua@hope.org"), ("o", "kojo@hope.org"), ("s", "kofi@hope.org")):
        clients[key] = app.test_client()
        login(clients[key], email)
    return {"org": org_id, "admin": admin, "pid": pid, "meo": meo, "field": field, "other_field": other_field,
            "staff": staff, **clients}


def q(org, model, **where):
    with tenant_scope(org):
        db.session.expire_all()
        stmt = select(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        out = db.session.scalars(stmt.order_by(model.id)).all()
        for o in out:  # plain snapshots: later requests close the session these were loaded in
            db.session.expunge(o)
        return out


def make_framework(c, pid):
    c.post(f"/app/projects/{pid}/results/new", data={"level": "goal", "parent_id": 0,
                                                    "statement": "Less water-borne disease in the district"})
    c.post(f"/app/projects/{pid}/results/new", data={"level": "outcome", "parent_id": 0,
                                                    "statement": "Year-round access to safe water"})
    c.post(f"/app/projects/{pid}/results/new", data={"level": "outcome", "parent_id": 0,
                                                    "statement": "Safe hygiene practices at home"})


def indicator(c, pid, **kw):
    data = {"name": "Boreholes completed", "result_id": 0, "measure": "number", "unit": "boreholes",
            "direction": "increase", "baseline_value": "0", "baseline_date": START.isoformat(),
            "target_value": "12", "target_date": END.isoformat(), "frequency": "monthly", "responsible_id": 0,
            "definition": "", "data_source": "", "disaggregation": ""}
    data.update({k: str(v) for k, v in kw.items()})
    return c.post(f"/app/projects/{pid}/indicators/new", data=data)


def report(c, iid, period_end, value, **kw):
    return c.post(f"/app/indicators/{iid}", data={"action": "record", "v-period_end": period_end.isoformat(),
                                                   "v-value": str(value), "v-source": kw.get("source", ""),
                                                   "v-notes": kw.get("notes", "")})


# ---------------------------------------------------------------- calculations
class Fake:
    def __init__(self, base, target, direction="increase", freq="monthly"):
        self.baseline_value, self.target_value = Decimal(base), Decimal(target)
        self.baseline_date, self.target_date = START, END
        self.direction, self.frequency = direction, freq


@pytest.mark.parametrize("base,target,actual,expected", [
    (0, 12, 6, 50.0), (38, 85, 52, 29.8), (84, 40, 71, 29.5), (84, 40, 40, 100.0), (10, 20, 25, 150.0),
    (10, 20, 5, -50.0),
])
def test_achievement_both_directions(base, target, actual, expected):
    assert svc.achievement(Fake(base, target), Decimal(actual)) == expected


@pytest.mark.parametrize("ach,exp,status", [
    (None, 50, "no_data"), (100, 50, "achieved"), (120, 10, "achieved"), (45, 50, "on_track"), (44.9, 50, "at_risk"),
    (30, 50, "at_risk"), (29.9, 50, "off_track"), (5, 0, "on_track"),
])
def test_status_thresholds(ach, exp, status):
    assert svc.classify(ach, exp) == status


def test_expected_share_and_next_due():
    f = Fake(0, 10)
    assert svc.expected_share(f, START) == 0 and svc.expected_share(f, END) == 100
    assert svc.expected_share(f, T) == 50.0
    assert svc.expected_share(f, END + timedelta(days=50)) == 100
    assert svc.next_due(f, None) == svc.add_months(START, 1)
    f.frequency = "quarterly"
    assert svc.next_due(f, T) == svc.add_months(T, 3)
    f.frequency = "once"
    assert svc.next_due(f, None) == END
    assert svc.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


# ---------------------------------------------------------------- results framework
def test_framework_codes_and_rules(world, client):
    make_framework(client, world["pid"])
    items = q(world["org"], ResultItem)
    goal, o1, o2 = items
    assert (goal.code, o1.code, o2.code) == ("G", "1", "2") and o1.parent_id == goal.id
    # an output must hang off an outcome
    r = client.post(f"/app/projects/{world['pid']}/results/new", data={"level": "output", "parent_id": 0,
                                                                      "statement": "Boreholes drilled"})
    assert "Choose the outcome" in r.get_data(as_text=True)
    client.post(f"/app/projects/{world['pid']}/results/new", data={"level": "output", "parent_id": o2.id,
                                                                  "statement": "Hygiene sessions held"})
    client.post(f"/app/projects/{world['pid']}/results/new", data={"level": "output", "parent_id": o2.id,
                                                                  "statement": "Soap groups formed"})
    assert [i.code for i in q(world["org"], ResultItem, level="output")] == ["2.1", "2.2"]
    # only one goal
    r = client.post(f"/app/projects/{world['pid']}/results/new", data={"level": "goal", "parent_id": 0,
                                                                      "statement": "A second goal statement"})
    assert "already has a goal" in r.get_data(as_text=True)
    # can't remove an outcome with outputs; removing outcome 1 renumbers outcome 2
    assert client.post(f"/app/results/{o2.id}/edit", data={"action": "delete"}).status_code == 302
    assert len(q(world["org"], ResultItem, level="outcome")) == 2
    client.post(f"/app/results/{o1.id}/edit", data={"action": "delete"})
    out = q(world["org"], ResultItem, level="outcome")
    assert len(out) == 1 and out[0].code == "1"
    assert [i.code for i in q(world["org"], ResultItem, level="output")] == ["1.1", "1.2"]


def test_deleting_result_keeps_indicators(world, client):
    make_framework(client, world["pid"])
    o1 = q(world["org"], ResultItem, level="outcome")[0]
    indicator(client, world["pid"], result_id=o1.id)
    client.post(f"/app/results/{o1.id}/edit", data={"action": "delete"})
    (ind,) = q(world["org"], Indicator)
    assert ind.result_id is None


# ---------------------------------------------------------------- indicators
@pytest.mark.parametrize("kw,msg", [
    ({"target_value": 0}, "must be different from the baseline"),
    ({"direction": "decrease", "baseline_value": 5, "target_value": 10}, "target must be below"),
    ({"direction": "increase", "baseline_value": 10, "target_value": 5}, "target must be above"),
    ({"measure": "percent", "target_value": 120}, "between 0 and 100"),
    ({"target_date": (START - timedelta(days=1)).isoformat()}, "must be after the baseline date"),
])
def test_indicator_validation(world, client, kw, msg):
    r = indicator(client, world["pid"], **kw)
    assert msg in r.get_data(as_text=True)
    assert q(world["org"], Indicator) == []


def test_indicator_codes_and_responsible_notified(world, client):
    indicator(client, world["pid"], responsible_id=world["field"])
    indicator(client, world["pid"], name="People trained", target_value=96)
    assert [i.code for i in q(world["org"], Indicator)] == ["IND-01", "IND-02"]
    notes = q(world["org"], Notification, user_id=world["field"])
    assert any("responsible for reporting IND-01" in n.title for n in notes)


def test_who_can_manage_indicators(world, client):
    assert indicator(world["m"], world["pid"]).status_code == 302          # M&E Officer
    assert indicator(world["f"], world["pid"]).status_code == 403          # Field Officer
    assert world["s"].get(f"/app/projects/{world['pid']}/me").status_code == 403  # staff: no M&E access
    assert world["s"].get("/app/me").status_code == 403


# ---------------------------------------------------------------- reporting & verification
def test_field_report_needs_verification(world, client):
    indicator(client, world["pid"])
    iid = q(world["org"], Indicator)[0].id
    r = report(world["f"], iid, T - timedelta(days=5), 4, source="Site visit")
    assert r.status_code == 302
    (v,) = q(world["org"], IndicatorValue)
    assert v.status == "pending" and v.reported_by_id == world["field"]
    assert any("please verify" in n.title for n in q(world["org"], Notification, user_id=world["meo"]))
    # pending results don't count yet
    with tenant_scope(world["org"]):
        assert svc.progress(db.session.get(Indicator, iid)).actual is None
    # a field officer can't verify
    world["f"].post(f"/app/indicators/{iid}", data={"action": "verify", "value": v.id})
    assert q(world["org"], IndicatorValue)[0].status == "pending"
    # the M&E officer verifies
    world["m"].post(f"/app/indicators/{iid}", data={"action": "verify", "value": v.id})
    v = q(world["org"], IndicatorValue)[0]
    assert v.status == "verified" and v.verified_by_id == world["meo"]
    with tenant_scope(world["org"]):
        p = svc.progress(db.session.get(Indicator, iid))
        assert p.actual == Decimal("4") and p.achievement == 33.3
    assert any("was verified" in n.title for n in q(world["org"], Notification, user_id=world["field"]))


def test_reject_needs_note_and_period_can_be_reported_again(world, client):
    indicator(client, world["pid"])
    iid = q(world["org"], Indicator)[0].id
    report(world["f"], iid, T - timedelta(days=5), 40)
    vid = q(world["org"], IndicatorValue)[0].id
    world["m"].post(f"/app/indicators/{iid}", data={"action": "reject", "value": vid, "r-note": ""})
    assert q(world["org"], IndicatorValue)[0].status == "pending"
    world["m"].post(f"/app/indicators/{iid}", data={"action": "reject", "value": vid, "r-note": "Target is 12, not 40"})
    assert q(world["org"], IndicatorValue)[0].status == "rejected"
    assert report(world["f"], iid, T - timedelta(days=5), 4).status_code == 302
    assert [v.status for v in q(world["org"], IndicatorValue)] == ["rejected", "pending"]


def test_me_officer_results_verified_at_once_but_not_self_verified(world, client):
    indicator(client, world["pid"])
    iid = q(world["org"], Indicator)[0].id
    report(world["m"], iid, T - timedelta(days=5), 3)
    assert q(world["org"], IndicatorValue)[0].status == "verified"
    with tenant_scope(world["org"]):
        row = IndicatorValue(organization_id=world["org"], indicator_id=iid, period_end=T - timedelta(days=40),
                             value=Decimal(1), status="pending", reported_by_id=world["meo"])
        db.session.add(row)
        db.session.commit()
        meo = db.session.get(User, world["meo"])
        with pytest.raises(svc.MEError, match="Someone else"):
            svc.review_value(meo, row, True)


@pytest.mark.parametrize("delta,value,msg", [
    (5, 4, "hasn't ended yet"), (-200, 4, "before the baseline"), (-5, -3, "can't be negative"),
])
def test_report_validation(world, client, delta, value, msg):
    indicator(client, world["pid"])
    iid = q(world["org"], Indicator)[0].id
    r = report(world["m"], iid, T + timedelta(days=delta), value)
    assert r.status_code == 302
    assert msg in world["m"].get(f"/app/indicators/{iid}").get_data(as_text=True).replace("&#39;", "'")
    assert q(world["org"], IndicatorValue) == []


def test_duplicate_period_and_outsider_refused(world, client):
    indicator(client, world["pid"])
    iid = q(world["org"], Indicator)[0].id
    report(world["m"], iid, T - timedelta(days=5), 3)
    report(world["m"], iid, T - timedelta(days=5), 4)
    assert len(q(world["org"], IndicatorValue)) == 1
    report(world["o"], iid, T - timedelta(days=9), 4)  # field officer who is not on this project
    assert len(q(world["org"], IndicatorValue)) == 1


def test_status_uses_verified_results_over_time(world, client):
    indicator(client, world["pid"], target_value=10)
    iid = q(world["org"], Indicator)[0].id
    report(world["m"], iid, T - timedelta(days=1), 5)   # half way, half the time gone → on track
    with tenant_scope(world["org"]):
        assert svc.progress(db.session.get(Indicator, iid)).status == "on_track"
    with tenant_scope(world["org"]):
        ind = db.session.get(Indicator, iid)
        db.session.delete(ind.values[0])
        db.session.commit()
    report(world["m"], iid, T - timedelta(days=1), 2)   # 20% done with 50% of the time gone → off track
    with tenant_scope(world["org"]):
        p = svc.progress(db.session.get(Indicator, iid))
        assert (p.achievement, p.status) == (20.0, "off_track")


def test_endline_overrides_and_verified_history_blocks_delete(world, client):
    indicator(client, world["pid"])
    iid = q(world["org"], Indicator)[0].id
    report(world["m"], iid, T - timedelta(days=5), 6)
    r = client.post(f"/app/indicators/{iid}", data={"action": "delete"})
    assert r.status_code == 302 and len(q(world["org"], Indicator)) == 1
    with tenant_scope(world["org"]):
        ind = db.session.get(Indicator, iid)
        ind.endline_value, ind.endline_date = Decimal(12), T
        db.session.commit()
        p = svc.progress(ind)
        assert p.actual == 12 and p.status == "achieved" and p.next_due is None


# ---------------------------------------------------------------- monitoring visits
def visit(c, pid, **kw):
    data = {"project_id": pid, "visit_date": (T - timedelta(days=2)).isoformat(), "location": "Kpachi borehole",
            "latitude": "9.624140", "longitude": "-0.825430", "purpose": "Check borehole", "people_met": "Committee",
            "findings": "Borehole 3 is complete and working well.", "recommendations": "Fence the site.",
            "rating": "good"}
    data.update({k: str(v) for k, v in kw.items()})
    return c.post("/app/visits/new", data=data)


@pytest.mark.parametrize("kw,msg", [
    ({"latitude": "95", "longitude": "1"}, "aren't valid"),
    ({"latitude": "9.6", "longitude": ""}, "both latitude and longitude"),
    ({"latitude": "north", "longitude": "east"}, "must be numbers"),
    ({"visit_date": (T + timedelta(days=1)).isoformat()}, "can't be in the future"),
])
def test_visit_validation(world, kw, msg):
    r = visit(world["f"], world["pid"], **kw)
    assert msg in r.get_data(as_text=True).replace("&#39;", "'")
    assert q(world["org"], MonitoringVisit) == []


def test_visit_flow_review_and_privacy(world, client, app):
    f, m = world["f"], world["m"]
    assert visit(f, world["pid"]).status_code == 302
    (v,) = q(world["org"], MonitoringVisit)
    assert v.reference == f"VIS-{(T - timedelta(days=2)).year}-0001" and v.latitude == Decimal("9.624140")
    # drafts are private
    assert world["o"].get(f"/app/visits/{v.id}").status_code == 403
    assert "Kpachi" not in world["o"].get("/app/visits").get_data(as_text=True)
    # attach a photo; a disguised file is refused
    f.post(f"/app/visits/{v.id}", data={"action": "attach", "caption": "Site",
                                        "photo": (io.BytesIO(PNG), "site.png")}, content_type="multipart/form-data")
    f.post(f"/app/visits/{v.id}", data={"action": "attach", "photo": (io.BytesIO(b"MZ\x90"), "x.png")},
           content_type="multipart/form-data")
    with tenant_scope(world["org"]):
        vv = db.session.get(MonitoringVisit, v.id)
        assert [d.title for d in vv.photos] == ["Site"] and vv.photos[0].category == "media"
    f.post(f"/app/visits/{v.id}", data={"action": "submit"})
    assert q(world["org"], MonitoringVisit)[0].status == "submitted"
    assert any("needs review" in n.title for n in q(world["org"], Notification, user_id=world["meo"]))
    # the visitor can't review their own visit or edit it once submitted
    f.post(f"/app/visits/{v.id}", data={"action": "accept"})
    assert q(world["org"], MonitoringVisit)[0].status == "submitted"
    assert f.get(f"/app/visits/{v.id}/edit").status_code == 403
    # returning needs a note and sends it back to draft
    m.post(f"/app/visits/{v.id}", data={"action": "return", "note": ""})
    assert q(world["org"], MonitoringVisit)[0].status == "submitted"
    m.post(f"/app/visits/{v.id}", data={"action": "return", "note": "Add the flow rate"})
    v2 = q(world["org"], MonitoringVisit)[0]
    assert v2.status == "draft" and v2.review_note == "Add the flow rate"
    f.post(f"/app/visits/{v.id}", data={"action": "submit"})
    m.post(f"/app/visits/{v.id}", data={"action": "accept", "note": "Thanks"})
    v3 = q(world["org"], MonitoringVisit)[0]
    assert v3.status == "reviewed" and v3.reviewed_by_id == world["meo"]
    r = world["o"].get(f"/app/visits/{v.id}.pdf")
    assert r.status_code == 200 and r.data[:5] == b"%PDF-"


def test_visit_submit_needs_findings(world):
    visit(world["f"], world["pid"], findings="")
    vid = q(world["org"], MonitoringVisit)[0].id
    world["f"].post(f"/app/visits/{vid}", data={"action": "submit"})
    assert q(world["org"], MonitoringVisit)[0].status == "draft"


# ---------------------------------------------------------------- evaluations
def evaluation(c, pid, **kw):
    data = {"title": "Mid-term review", "kind": "midterm", "status": "planned", "evaluator": "NRA",
            "start_date": T.isoformat(), "end_date": (T + timedelta(days=10)).isoformat(), "scope": "", "findings": "",
            "recommendations": "", "management_response": "", "report_document_id": 0,
            "relevance": 0, "coherence": 0, "effectiveness": 0, "efficiency": 0, "impact": 0, "sustainability": 0}
    data.update({k: str(v) for k, v in kw.items()})
    return c.post(f"/app/projects/{pid}/evaluations/new", data=data)


def test_evaluation_rules_and_ratings(world, client):
    r = evaluation(client, world["pid"], status="completed")
    assert "key findings before marking" in r.get_data(as_text=True)
    r = evaluation(client, world["pid"], end_date=(T - timedelta(days=1)).isoformat())
    assert "can't be before the start" in r.get_data(as_text=True).replace("&#39;", "'")
    assert evaluation(world["f"], world["pid"]).status_code == 403
    evaluation(client, world["pid"], status="completed", findings="Attendance rose from 71% to 79%.",
               relevance=4, effectiveness=3, efficiency=2)
    (ev,) = q(world["org"], Evaluation)
    assert ev.overall == 3.0 and ev.coherence is None
    assert any("Evaluation completed" in n.title for n in q(world["org"], Notification, user_id=world["field"]))


# ---------------------------------------------------------------- reports
def test_pdf_report_contains_data_and_escapes_text(world, client):
    from pypdf import PdfReader

    make_framework(client, world["pid"])
    o1 = q(world["org"], ResultItem, level="outcome")[0]
    indicator(client, world["pid"], result_id=o1.id, name="Wells <script>alert(1)</script> & pumps")
    iid = q(world["org"], Indicator)[0].id
    report(world["m"], iid, T - timedelta(days=3), 6)
    visit(world["f"], world["pid"])
    r = world["m"].get(f"/app/projects/{world['pid']}/me/report.pdf")
    assert r.status_code == 200 and r.data[:5] == b"%PDF-" and r.mimetype == "application/pdf"
    content = " ".join(" ".join(page.extract_text().split()) for page in PdfReader(io.BytesIO(r.data)).pages)
    assert "Wells <script>alert(1)</script> & pumps" in content
    assert "Year-round access to safe water" in content and "50.0%" in content
    assert "Budget snapshot" not in content  # the M&E officer can't see budgets
    assert "Budget snapshot" in " ".join(p.extract_text() for p in PdfReader(io.BytesIO(
        client.get(f"/app/projects/{world['pid']}/me/report.pdf").data)).pages)


def test_excel_tracking_table(world, client):
    indicator(client, world["pid"], name="=HYPERLINK(\"http://evil\")")
    iid = q(world["org"], Indicator)[0].id
    report(world["m"], iid, T - timedelta(days=3), 6)
    r = client.get("/app/me/indicators.xlsx")
    wb = load_workbook(io.BytesIO(r.data))
    ws = wb["Indicators"]
    rows = [[c.value for c in row] for row in ws.iter_rows()]
    data_row = next(row for row in rows if row[2] == "IND-01")
    assert data_row[3] == "'=HYPERLINK(\"http://evil\")" and data_row[12] == 6 and data_row[15] == 0.5
    assert "Reported values" in wb.sheetnames


# ---------------------------------------------------------------- reminders
def test_due_reminders_sent_once(world, client):
    indicator(client, world["pid"], responsible_id=world["field"])  # monthly from START → first data long overdue
    before = len(q(world["org"], Notification, user_id=world["field"], kind="me.due"))
    assert svc.send_due_reminders() >= 1
    svc.send_due_reminders()
    assert len(q(world["org"], Notification, user_id=world["field"], kind="me.due")) == before + 1


# ---------------------------------------------------------------- isolation
def test_other_organization_cannot_see_me_data(world, client, app):
    indicator(client, world["pid"])
    visit(world["f"], world["pid"])
    iid = q(world["org"], Indicator)[0].id
    vid = q(world["org"], MonitoringVisit)[0].id
    other_org, _ = make_org(name="Other NGO", email="boss@other.org")
    o = app.test_client()
    login(o, "boss@other.org")
    assert o.get(f"/app/indicators/{iid}").status_code == 404
    assert o.get(f"/app/visits/{vid}").status_code == 404
    assert o.get(f"/app/projects/{world['pid']}/me/report.pdf").status_code == 404
    with tenant_scope(other_org):
        for table in ("indicators", "result_items", "monitoring_visits", "indicator_values", "evaluations"):
            assert db.session.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


def test_stage9_tables_have_rls():
    names = ["result_items", "indicators", "indicator_values", "monitoring_visits", "evaluations"]
    n = db.session.execute(text("SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
                                "AND relname = ANY(:n)"), {"n": names}).scalar()
    assert n == len(names)
