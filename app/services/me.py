"""Stage 9: monitoring and evaluation.

Results frameworks (goal → outcomes → outputs), indicators with baseline / target / endline, reported values
with verification, field monitoring visits with GPS and photos, and evaluations rated on the OECD-DAC criteria.
All figures shown anywhere in the product come from these functions, computed from stored data.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import func, select

from app.extensions import db
from app.models import (DAC_CRITERIA, EVALUATION_KINDS, EVALUATION_STATUSES, FREQUENCIES, FREQUENCY_MONTHS,
                        INDICATOR_DIRECTIONS, INDICATOR_MEASURES, RESULT_LEVELS, VISIT_RATINGS, Document, Evaluation,
                        Indicator, IndicatorValue, MonitoringVisit, Project, ResultItem, User, utcnow)

from . import audit
from . import documents as doc_svc
from . import projects as project_svc
from .notify import notify, users_with

TWO = Decimal("0.01")
STATUS_LABELS = {"achieved": "Achieved", "on_track": "On track", "at_risk": "At risk", "off_track": "Off track",
                 "no_data": "No data yet"}


class MEError(ValueError):
    pass


# ---------------------------------------------------------------- access
def can_view(user: User) -> bool:
    return user.can("me.view") and user.can("projects.view")


def can_manage(user: User, project: Project) -> bool:
    """M&E Officers manage every framework; a project's manager manages their own."""
    return user.can("me.manage") or project_svc.can_edit(user, project)


def _on_team(user: User, project: Project) -> bool:
    return project.manager_id == user.id or any(m.user_id == user.id for m in project.members)


def can_report(user: User, project: Project) -> bool:
    """Record indicator values and monitoring visits."""
    if can_manage(user, project):
        return True
    return user.can("field.report") and _on_team(user, project)


def can_verify(user: User) -> bool:
    return user.can("me.manage")


def _require_open(project: Project) -> None:
    if not project.is_open:
        raise MEError("This project is closed, so its M&E records can't be changed.")


# ---------------------------------------------------------------- numbers
def to_decimal(value, field: str = "Value") -> Decimal:
    try:
        d = Decimal(str(value)).quantize(TWO, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise MEError(f"{field} must be a number.") from None
    if not d.is_finite() or abs(d) >= Decimal("1e14"):
        raise MEError(f"{field} is too large.")
    return d


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    import calendar

    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


# ---------------------------------------------------------------- results framework
def framework(project: Project) -> list[ResultItem]:
    """Top-level items (goal first, then any outcomes without a goal), children loaded in order."""
    items = db.session.scalars(select(ResultItem).where(ResultItem.project_id == project.id)
                               .order_by(ResultItem.sort_order, ResultItem.id)).all()
    order = {"goal": 0, "outcome": 1, "output": 2}
    return sorted([i for i in items if i.parent_id is None], key=lambda i: (order[i.level], i.sort_order, i.id))


def _renumber(project: Project) -> None:
    """Codes follow the logframe convention: G, 1, 2 … for outcomes and 1.1, 1.2 … for outputs."""
    db.session.flush()
    outcomes = db.session.scalars(select(ResultItem).where(ResultItem.project_id == project.id,
                                                           ResultItem.level == "outcome")
                                  .order_by(ResultItem.sort_order, ResultItem.id)).all()
    for g in db.session.scalars(select(ResultItem).where(ResultItem.project_id == project.id,
                                                          ResultItem.level == "goal")):
        g.code = "G"
    for i, o in enumerate(outcomes, start=1):
        o.code, o.sort_order = str(i), i
        outputs = sorted([c for c in o.children if c.level == "output"], key=lambda c: (c.sort_order, c.id))
        for j, out in enumerate(outputs, start=1):
            out.code, out.sort_order = f"{i}.{j}", j


def allowed_parents(project: Project, level: str) -> list[ResultItem]:
    need = {"goal": None, "outcome": "goal", "output": "outcome"}[level]
    if need is None:
        return []
    return db.session.scalars(select(ResultItem).where(ResultItem.project_id == project.id, ResultItem.level == need)
                              .order_by(ResultItem.sort_order, ResultItem.id)).all()


def save_result(actor: User, project: Project, item: ResultItem | None, *, level: str, statement: str,
                parent_id: int | None, assumptions: str = "") -> ResultItem:
    if not can_manage(actor, project):
        raise MEError("You can't change this project's results framework.")
    _require_open(project)
    if level not in dict(RESULT_LEVELS):
        raise MEError("Choose a level.")
    statement = (statement or "").strip()
    if len(statement) < 5:
        raise MEError("Write the result statement (at least 5 characters).")
    if item is not None and item.level != level and (item.children or item.indicators):
        raise MEError("This result has outputs or indicators under it, so its level can't change.")
    parent = db.session.get(ResultItem, parent_id) if parent_id else None
    if parent is not None and parent.project_id != project.id:
        raise MEError("Choose a result from this project.")
    if level == "goal":
        if parent is not None:
            raise MEError("The goal sits at the top of the framework.")
        existing = db.session.scalar(select(ResultItem).where(ResultItem.project_id == project.id,
                                                              ResultItem.level == "goal"))
        if existing is not None and existing is not item:
            raise MEError("This project already has a goal. Edit it instead.")
    elif level == "outcome":
        goal = db.session.scalar(select(ResultItem).where(ResultItem.project_id == project.id,
                                                          ResultItem.level == "goal"))
        if parent is not None and parent.level != "goal":
            raise MEError("Outcomes sit under the goal.")
        parent = parent or goal
    else:
        if parent is None or parent.level != "outcome":
            raise MEError("Choose the outcome this output contributes to.")
    creating = item is None
    if creating:
        last = db.session.scalar(select(func.max(ResultItem.sort_order)).where(
            ResultItem.project_id == project.id, ResultItem.parent_id == (parent.id if parent else None)))
        item = ResultItem(organization_id=project.organization_id, project_id=project.id, sort_order=(last or 0) + 1)
        db.session.add(item)
    item.level, item.statement, item.assumptions = level, statement[:5000], (assumptions or "").strip()[:5000]
    item.parent = parent
    _renumber(project)
    audit.record("me.result_added" if creating else "me.result_updated",
                 summary=f"{'Added' if creating else 'Updated'} {item.level_label.lower()} {item.code} in {project.code}",
                 entity=item)
    return item


def delete_result(actor: User, item: ResultItem) -> None:
    project = item.project
    if not can_manage(actor, project):
        raise MEError("You can't change this project's results framework.")
    _require_open(project)
    if item.children:
        raise MEError("Remove the results under this one first.")
    for ind in list(item.indicators):
        ind.result_id = None
    code = item.code
    db.session.delete(item)
    _renumber(project)
    audit.record("me.result_deleted", summary=f"Removed result {code} from {project.code}", entity=project)


# ---------------------------------------------------------------- indicators
def _next_indicator_seq(project: Project) -> int:
    return (db.session.scalar(select(func.max(Indicator.seq)).where(Indicator.project_id == project.id)) or 0) + 1


def save_indicator(actor: User, project: Project, ind: Indicator | None, **f) -> Indicator:
    if not can_manage(actor, project):
        raise MEError("You can't change this project's indicators.")
    _require_open(project)
    name = (f.get("name") or "").strip()
    if len(name) < 3:
        raise MEError("Give the indicator a name.")
    if f.get("measure") not in dict(INDICATOR_MEASURES) or f.get("direction") not in dict(INDICATOR_DIRECTIONS):
        raise MEError("Choose how the indicator is measured.")
    if f.get("frequency") not in dict(FREQUENCIES):
        raise MEError("Choose how often it is measured.")
    baseline = to_decimal(f.get("baseline_value") or 0, "Baseline")
    target = to_decimal(f.get("target_value"), "Target")
    endline = to_decimal(f["endline_value"], "Endline") if f.get("endline_value") not in (None, "") else None
    for label, v in (("Baseline", baseline), ("Target", target), ("Endline", endline)):
        if v is not None and f["measure"] == "percent" and not (0 <= v <= 100):
            raise MEError(f"{label} must be between 0 and 100 for a percentage.")
    if target == baseline:
        raise MEError("The target must be different from the baseline.")
    if f["direction"] == "increase" and target < baseline:
        raise MEError("For 'higher is better' the target must be above the baseline.")
    if f["direction"] == "decrease" and target > baseline:
        raise MEError("For 'lower is better' the target must be below the baseline.")
    bdate, tdate = f.get("baseline_date"), f.get("target_date")
    if not bdate or not tdate:
        raise MEError("Set the baseline date and target date.")
    if tdate <= bdate:
        raise MEError("The target date must be after the baseline date.")
    if endline is not None and not f.get("endline_date"):
        raise MEError("Add the date the endline value was measured.")
    result = db.session.get(ResultItem, f["result_id"]) if f.get("result_id") else None
    if result is not None and result.project_id != project.id:
        raise MEError("Choose a result from this project.")
    responsible_id = f.get("responsible_id") or None
    if responsible_id and db.session.get(User, responsible_id) is None:
        raise MEError("Choose someone from your organization.")
    creating = ind is None
    if creating:
        seq = _next_indicator_seq(project)
        ind = Indicator(organization_id=project.organization_id, project_id=project.id, seq=seq, code=f"IND-{seq:02d}")
        db.session.add(ind)
    ind.name, ind.definition, ind.unit = name[:255], (f.get("definition") or "").strip(), (f.get("unit") or "").strip()[:40]
    ind.measure, ind.direction, ind.frequency = f["measure"], f["direction"], f["frequency"]
    ind.baseline_value, ind.baseline_date, ind.target_value, ind.target_date = baseline, bdate, target, tdate
    ind.endline_value, ind.endline_date = endline, f.get("endline_date") if endline is not None else None
    ind.data_source = (f.get("data_source") or "").strip()
    ind.disaggregation = (f.get("disaggregation") or "").strip()[:255]
    ind.result, ind.responsible_id = result, responsible_id
    db.session.flush()
    audit.record("me.indicator_saved", summary=f"{'Added' if creating else 'Updated'} indicator {ind.code} "
                                                f"“{ind.name}” in {project.code}", entity=ind)
    if responsible_id and (creating or f.get("_responsible_changed")):
        notify([responsible_id], "me.indicator", f"You're responsible for reporting {ind.code}: {ind.name}",
               f"/app/indicators/{ind.id}", actor=actor)
    return ind


def set_active(actor: User, ind: Indicator, active: bool) -> None:
    if not can_manage(actor, ind.project):
        raise MEError("You can't change this indicator.")
    ind.is_active = active
    audit.record("me.indicator_" + ("reactivated" if active else "retired"),
                 summary=f"{'Reactivated' if active else 'Retired'} indicator {ind.code}", entity=ind)


def delete_indicator(actor: User, ind: Indicator) -> None:
    if not can_manage(actor, ind.project):
        raise MEError("You can't change this indicator.")
    if any(v.status == "verified" for v in ind.values):
        raise MEError("This indicator has verified results. Retire it instead, so the history is kept.")
    audit.record("me.indicator_deleted", summary=f"Deleted indicator {ind.code} “{ind.name}”",
                 entity=ind.project)
    db.session.delete(ind)


# ---------------------------------------------------------------- progress
@dataclass
class Progress:
    actual: Decimal | None          # latest verified value (or endline if later)
    actual_date: date | None
    achievement: float | None       # % of the way from baseline to target
    expected: float                 # % of the time from baseline date to target date that has passed (today)
    status: str                     # achieved | on_track | at_risk | off_track | no_data
    next_due: date | None
    overdue: bool
    pending: int

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def bar(self) -> float:
        return max(0.0, min(100.0, self.achievement or 0.0))


def achievement(ind: Indicator, actual: Decimal) -> float:
    span = ind.target_value - ind.baseline_value
    return float(((actual - ind.baseline_value) / span * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def expected_share(ind: Indicator, today: date | None = None) -> float:
    today = today or date.today()
    total = (ind.target_date - ind.baseline_date).days
    done = (min(max(today, ind.baseline_date), ind.target_date) - ind.baseline_date).days
    return round(done / total * 100, 1) if total > 0 else 100.0


def classify(ach: float | None, expected: float) -> str:
    """Achieved at 100%; on track within 90% of where it should be; at risk within 60%; else off track."""
    if ach is None:
        return "no_data"
    if ach >= 100:
        return "achieved"
    if expected <= 0 or ach >= expected * 0.9:
        return "on_track"
    if ach >= expected * 0.6:
        return "at_risk"
    return "off_track"


def next_due(ind: Indicator, last: date | None) -> date | None:
    months = FREQUENCY_MONTHS.get(ind.frequency)
    if months is None:
        return None if last and last >= ind.target_date else ind.target_date
    due = add_months(last or ind.baseline_date, months)
    return None if due > ind.target_date + timedelta(days=31) else min(due, ind.target_date)


def progress(ind: Indicator, today: date | None = None) -> Progress:
    today = today or date.today()
    verified = [v for v in ind.values if v.status == "verified"]
    latest = max(verified, key=lambda v: (v.period_end, v.id)) if verified else None
    actual, actual_date = (latest.value, latest.period_end) if latest else (None, None)
    if ind.endline_value is not None and ind.endline_date and (actual_date is None or ind.endline_date >= actual_date):
        actual, actual_date = ind.endline_value, ind.endline_date
    ach = achievement(ind, actual) if actual is not None else None
    # Judge each result against where the indicator should have been when it was measured;
    # results that are late show up separately as "data overdue".
    exp = expected_share(ind, actual_date or today)
    exp_today = expected_share(ind, today)
    due = None if not ind.is_active or ind.endline_value is not None else next_due(ind, latest.period_end if latest else None)
    return Progress(actual=actual, actual_date=actual_date, achievement=ach, expected=exp_today,
                    status=classify(ach, exp),
                    next_due=due, overdue=bool(due and due < today),
                    pending=sum(1 for v in ind.values if v.status == "pending"))


def indicators_for(project: Project, *, active_only: bool = False) -> list[Indicator]:
    stmt = select(Indicator).where(Indicator.project_id == project.id)
    if active_only:
        stmt = stmt.where(Indicator.is_active.is_(True))
    return db.session.scalars(stmt.order_by(Indicator.seq)).all()


def project_summary(project: Project) -> dict:
    rows = [(i, progress(i)) for i in indicators_for(project)]
    active = [(i, p) for i, p in rows if i.is_active]
    counts = {k: sum(1 for _, p in active if p.status == k) for k in STATUS_LABELS}
    measured = [p.achievement for _, p in active if p.achievement is not None]
    return {
        "rows": rows, "progress": {i.id: p for i, p in rows}, "counts": counts, "total": len(active),
        "avg_achievement": round(sum(min(a, 100) for a in measured) / len(measured), 1) if measured else None,
        "pending": sum(p.pending for _, p in rows), "overdue": sum(1 for _, p in active if p.overdue),
    }


# ---------------------------------------------------------------- reported values
def record_value(actor: User, ind: Indicator, *, period_end: date, value, notes: str = "", source: str = "") -> IndicatorValue:
    project = ind.project
    if not can_report(actor, project):
        raise MEError("You can't report results for this project.")
    _require_open(project)
    if not ind.is_active:
        raise MEError("This indicator is retired.")
    if period_end is None:
        raise MEError("Choose the end of the reporting period.")
    if period_end > date.today():
        raise MEError("You can't report a period that hasn't ended yet.")
    if period_end < ind.baseline_date:
        raise MEError("The period ends before the baseline date.")
    v = to_decimal(value)
    if ind.is_percent and not (0 <= v <= 100):
        raise MEError("A percentage must be between 0 and 100.")
    if v < 0 and ind.measure == "number" and ind.baseline_value >= 0 and ind.target_value >= 0:
        raise MEError("The value can't be negative.")
    clash = db.session.scalar(select(IndicatorValue).where(IndicatorValue.indicator_id == ind.id,
                                                           IndicatorValue.period_end == period_end,
                                                           IndicatorValue.status != "rejected"))
    if clash is not None:
        raise MEError("A value for this period has already been reported.")
    trusted = can_verify(actor)
    row = IndicatorValue(organization_id=ind.organization_id, indicator=ind, period_end=period_end, value=v,
                         notes=(notes or "").strip()[:5000], source=(source or "").strip()[:255],
                         status="verified" if trusted else "pending", reported_by_id=actor.id,
                         verified_by_id=actor.id if trusted else None, verified_at=utcnow() if trusted else None)
    db.session.add(row)
    db.session.flush()
    audit.record("me.value_reported", summary=f"Reported {v} for {ind.code} (period ending {period_end:%d %b %Y})"
                                              + ("" if trusted else ", waiting for verification"), entity=ind)
    if not trusted:
        notify(users_with("me.manage", exclude={actor.id}), "me.verify",
               f"{actor.full_name} reported a result for {ind.code}: please verify",
               f"/app/indicators/{ind.id}", actor=actor)
    return row


def review_value(actor: User, row: IndicatorValue, approve: bool, note: str = "") -> None:
    if not can_verify(actor):
        raise MEError("Only M&E Officers can verify results.")
    if row.status != "pending":
        raise MEError("This result has already been reviewed.")
    if row.reported_by_id == actor.id:
        raise MEError("Someone else must verify a result you reported.")
    note = (note or "").strip()
    if not approve and len(note) < 3:
        raise MEError("Say why the result is being rejected, so it can be corrected.")
    row.status = "verified" if approve else "rejected"
    row.verified_by_id, row.verified_at, row.review_note = actor.id, utcnow(), note[:500]
    ind = row.indicator
    audit.record("me.value_" + row.status, summary=f"{'Verified' if approve else 'Rejected'} {row.value} for "
                                                   f"{ind.code} (period ending {row.period_end:%d %b %Y})", entity=ind)
    if row.reported_by_id:
        notify([row.reported_by_id], "me.reviewed",
               f"Your result for {ind.code} was {'verified' if approve else 'rejected'}"
               + (f": {note}" if note and not approve else ""), f"/app/indicators/{ind.id}", actor=actor)


def delete_value(actor: User, row: IndicatorValue) -> None:
    project = row.indicator.project
    if not (can_manage(actor, project) or (row.reported_by_id == actor.id and row.status == "pending")):
        raise MEError("You can't remove this result.")
    audit.record("me.value_deleted", summary=f"Removed result {row.value} for {row.indicator.code}", entity=row.indicator)
    db.session.delete(row)


def pending_values() -> list[IndicatorValue]:
    return db.session.scalars(select(IndicatorValue).where(IndicatorValue.status == "pending")
                              .order_by(IndicatorValue.created_at)).all()


# ---------------------------------------------------------------- monitoring visits
def can_edit_visit(user: User, visit: MonitoringVisit) -> bool:
    return visit.status == "draft" and (visit.visited_by_id == user.id or can_manage(user, visit.project))


def can_review_visit(user: User, visit: MonitoringVisit) -> bool:
    return visit.status == "submitted" and visit.visited_by_id != user.id and can_manage(user, visit.project)


def _coords(lat, lng):
    if lat in (None, "") and lng in (None, ""):
        return None, None
    if lat in (None, "") or lng in (None, ""):
        raise MEError("Enter both latitude and longitude, or leave both empty.")
    try:
        la = Decimal(str(lat).strip()).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        lo = Decimal(str(lng).strip()).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise MEError("GPS coordinates must be numbers, like 5.603717 and -0.186964.") from None
    if not (-90 <= la <= 90) or not (-180 <= lo <= 180):
        raise MEError("Those GPS coordinates aren't valid.")
    return la, lo


def save_visit(actor: User, visit: MonitoringVisit | None, project: Project, **f) -> MonitoringVisit:
    if visit is None and not can_report(actor, project):
        raise MEError("You can't record visits for this project.")
    if visit is not None and not can_edit_visit(actor, visit):
        raise MEError("This visit can no longer be edited.")
    _require_open(project)
    if not f.get("visit_date"):
        raise MEError("Enter the visit date.")
    if f["visit_date"] > date.today():
        raise MEError("The visit date can't be in the future.")
    location = (f.get("location") or "").strip()
    if len(location) < 2:
        raise MEError("Enter where the visit took place.")
    if f.get("rating") not in dict(VISIT_RATINGS):
        raise MEError("Choose how the work is going.")
    lat, lng = _coords(f.get("latitude"), f.get("longitude"))
    creating = visit is None
    if creating:
        seq = (db.session.scalar(select(func.max(MonitoringVisit.seq))) or 0) + 1
        visit = MonitoringVisit(organization_id=project.organization_id, seq=seq,
                                reference=f"VIS-{f['visit_date'].year}-{seq:04d}", visited_by_id=actor.id)
        db.session.add(visit)
    visit.project_id, visit.project = project.id, project
    visit.visit_date, visit.location, visit.latitude, visit.longitude = f["visit_date"], location[:255], lat, lng
    for k in ("purpose", "people_met", "findings", "recommendations"):
        setattr(visit, k, (f.get(k) or "").strip()[:10000])
    visit.rating = f["rating"]
    db.session.flush()
    audit.record("me.visit_saved", summary=f"{'Recorded' if creating else 'Updated'} visit {visit.reference} "
                                           f"to {visit.location}", entity=visit)
    return visit


def submit_visit(actor: User, visit: MonitoringVisit) -> None:
    if not can_edit_visit(actor, visit):
        raise MEError("This visit can't be submitted.")
    if len(visit.findings.strip()) < 10:
        raise MEError("Write up what you found (at least a sentence) before submitting.")
    visit.status, visit.submitted_at = "submitted", utcnow()
    p = visit.project
    reviewers = {u.id for u in users_with("me.manage")} | ({p.manager_id} if p.manager_id else set())
    notify(reviewers, "me.visit", f"Monitoring visit {visit.reference} to {visit.location} needs review",
           f"/app/visits/{visit.id}", actor=actor)
    audit.record("me.visit_submitted", summary=f"Submitted visit {visit.reference} for review", entity=visit)


def review_visit(actor: User, visit: MonitoringVisit, approve: bool, note: str) -> None:
    if not can_review_visit(actor, visit):
        raise MEError("You can't review this visit.")
    note = (note or "").strip()
    if not approve and len(note) < 3:
        raise MEError("Say what needs to change before the visit can be accepted.")
    if approve:
        visit.status, visit.reviewed_by_id, visit.reviewed_at = "reviewed", actor.id, utcnow()
    else:
        visit.status, visit.submitted_at = "draft", None
    visit.review_note = note[:5000]
    if visit.visited_by_id:
        notify([visit.visited_by_id], "me.visit",
               f"Visit {visit.reference} was {'accepted' if approve else 'returned for changes'}",
               f"/app/visits/{visit.id}", actor=actor)
    audit.record("me.visit_" + ("reviewed" if approve else "returned"),
                 summary=f"{'Accepted' if approve else 'Returned'} visit {visit.reference}", entity=visit)


def delete_visit(actor: User, visit: MonitoringVisit) -> None:
    if not can_edit_visit(actor, visit):
        raise MEError("Only draft visits can be deleted.")
    for d in list(visit.photos):
        doc_svc.delete(actor, d) if doc_svc.can_manage(actor, d) else setattr(d, "visit_id", None)
    audit.record("me.visit_deleted", summary=f"Deleted draft visit {visit.reference}", entity=visit.project)
    db.session.delete(visit)


def attach_to_visit(actor: User, visit: MonitoringVisit, file_storage, caption: str = "") -> Document:
    """Photos and scans from the field are stored in the project's document library, linked to the visit."""
    if not can_edit_visit(actor, visit):
        raise MEError("Attachments can only be added while the visit is a draft.")
    name, ext, _ = doc_svc.validate_upload(file_storage)
    file_storage.stream.seek(0)
    if ext not in ("png", "jpg", "jpeg", "pdf"):
        raise MEError("Attach photos (JPG or PNG) or scanned forms (PDF).")
    doc = Document(organization_id=visit.organization_id, project_id=visit.project_id, visit_id=visit.id,
                   title=((caption or "").strip() or f"{visit.reference} · {name}")[:200], category="media",
                   description=f"From monitoring visit {visit.reference} ({visit.location})", created_by_id=actor.id)
    db.session.add(doc)
    db.session.flush()
    try:
        doc_svc._store_version(actor, doc, file_storage, "From the field")
    except doc_svc.DocumentError as exc:
        raise MEError(str(exc)) from exc
    audit.record("document.uploaded", summary=f"Attached “{doc.title}” to visit {visit.reference}", entity=doc)
    return doc


# ---------------------------------------------------------------- evaluations
def save_evaluation(actor: User, project: Project, ev: Evaluation | None, **f) -> Evaluation:
    if not can_manage(actor, project):
        raise MEError("You can't manage this project's evaluations.")
    title = (f.get("title") or "").strip()
    if len(title) < 3:
        raise MEError("Give the evaluation a title.")
    if f.get("kind") not in dict(EVALUATION_KINDS) or f.get("status") not in dict(EVALUATION_STATUSES):
        raise MEError("Choose the type and status.")
    if f.get("start_date") and f.get("end_date") and f["end_date"] < f["start_date"]:
        raise MEError("The end date can't be before the start date.")
    if f["status"] == "completed" and len((f.get("findings") or "").strip()) < 10:
        raise MEError("Record the key findings before marking the evaluation completed.")
    doc_id = f.get("report_document_id") or None
    if doc_id:
        d = db.session.get(Document, doc_id)
        if d is None or d.project_id != project.id:
            raise MEError("Choose a report from this project's documents.")
    for key, _ in DAC_CRITERIA:
        r = f.get(key)
        if r not in (None, "", 0) and int(r) not in (1, 2, 3, 4):
            raise MEError("Ratings go from 1 to 4.")
    creating = ev is None
    if creating:
        ev = Evaluation(organization_id=project.organization_id, project_id=project.id, created_by_id=actor.id)
        db.session.add(ev)
    was_completed = not creating and ev.status == "completed"
    ev.title, ev.kind, ev.status = title[:200], f["kind"], f["status"]
    ev.evaluator, ev.is_external = (f.get("evaluator") or "").strip()[:200], bool(f.get("is_external"))
    ev.start_date, ev.end_date = f.get("start_date"), f.get("end_date")
    for k in ("scope", "findings", "recommendations", "management_response"):
        setattr(ev, k, (f.get(k) or "").strip()[:20000])
    for key, _ in DAC_CRITERIA:
        setattr(ev, key, int(f[key]) if f.get(key) not in (None, "", 0) else None)
    ev.report_document_id = doc_id
    db.session.flush()
    audit.record("me.evaluation_saved", summary=f"{'Added' if creating else 'Updated'} evaluation “{ev.title}” "
                                                f"({ev.status_label.lower()})", entity=ev)
    if ev.status == "completed" and not was_completed:
        team = [m.user_id for m in project.members] + ([project.manager_id] if project.manager_id else [])
        notify(team, "me.evaluation", f"Evaluation completed for {project.code}: {ev.title}",
               f"/app/evaluations/{ev.id}", actor=actor)
    return ev


def delete_evaluation(actor: User, ev: Evaluation) -> None:
    if not can_manage(actor, ev.project):
        raise MEError("You can't delete this evaluation.")
    audit.record("me.evaluation_deleted", summary=f"Deleted evaluation “{ev.title}”", entity=ev.project)
    db.session.delete(ev)


# ---------------------------------------------------------------- organization overview
def overview() -> dict:
    projects = db.session.scalars(select(Project).where(Project.status.in_(["planning", "active", "on_hold"]))
                                  .order_by(Project.name)).all()
    rows, totals = [], {k: 0 for k in STATUS_LABELS}
    for p in projects:
        s = project_summary(p)
        for k, n in s["counts"].items():
            totals[k] += n
        rows.append({"project": p, **s})
    visits = db.session.scalars(select(MonitoringVisit).order_by(MonitoringVisit.visit_date.desc(),
                                                                 MonitoringVisit.id.desc()).limit(6)).all()
    upcoming = db.session.scalars(select(Evaluation).where(Evaluation.status != "completed")
                                  .order_by(Evaluation.start_date.asc().nulls_last()).limit(6)).all()
    since = date.today() - timedelta(days=90)
    return {
        "rows": rows, "totals": totals, "indicators": sum(r["total"] for r in rows),
        "pending": len(pending_values()), "overdue": sum(r["overdue"] for r in rows),
        "visits_90": db.session.scalar(select(func.count(MonitoringVisit.id)).where(
            MonitoringVisit.visit_date >= since, MonitoringVisit.status != "draft")) or 0,
        "visits_waiting": db.session.scalar(select(func.count(MonitoringVisit.id)).where(
            MonitoringVisit.status == "submitted")) or 0,
        "recent_visits": visits, "evaluations": upcoming,
    }


# ---------------------------------------------------------------- reminders (Celery beat, daily)
def send_due_reminders() -> int:
    """Remind each indicator's responsible person once when its data collection is overdue."""
    from app.models import Notification, Organization
    from app.tenancy import tenant_scope

    sent = 0
    with tenant_scope(platform=True):
        org_ids = db.session.scalars(select(Organization.id)).all()
    for org_id in org_ids:
        with tenant_scope(org_id):
            inds = db.session.scalars(select(Indicator).join(Project).where(
                Indicator.is_active.is_(True), Indicator.responsible_id.is_not(None),
                Project.status.in_(["planning", "active", "on_hold"]))).all()
            for ind in inds:
                p = progress(ind)
                if not p.overdue:
                    continue
                link = f"/app/indicators/{ind.id}"
                already = db.session.scalar(select(func.count(Notification.id)).where(
                    Notification.user_id == ind.responsible_id, Notification.kind == "me.due",
                    Notification.link == link, func.date(Notification.created_at) >= p.next_due))
                if already:
                    continue
                sent += notify([ind.responsible_id], "me.due",
                               f"Data for {ind.code} ({ind.name}) was due on {p.next_due:%d %b %Y}", link,
                               organization_id=org_id)
            db.session.commit()
    return sent
