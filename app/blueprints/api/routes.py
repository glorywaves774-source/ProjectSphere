"""Stage 11: REST API v1 (read access plus indicator results) and the Paystack webhook.

Authenticate with `Authorization: Bearer ps_live_…`. Every request is bound to the key's organization, so the
same two-layer tenant isolation applies as in the web app. Responses are JSON; amounts are strings to keep
their exact decimal value. Lists support `limit` (max 200) and `offset`.
"""
import hashlib
from datetime import date, datetime
from decimal import Decimal
from functools import wraps

from flask import Blueprint, abort, g, jsonify, request
from sqlalchemy import func, select

from app.extensions import csrf, db, limiter
from app.models import (BudgetLine, Expense, Indicator, IndicatorValue, Issue, Organization, Project, Risk, Task,
                        User, utcnow)
from app.services import apikeys, billing
from app.services import finance as fin
from app.services import me as me_svc
from app.tenancy import TenantContext, set_context

bp = Blueprint("api", __name__)
csrf.exempt(bp)


def _key_func():
    auth = request.headers.get("Authorization", "")
    if auth:
        return "api:" + hashlib.sha256(auth.encode()).hexdigest()[:20]
    return "ip:" + (request.remote_addr or "?")


def _err(status: int, message: str):
    r = jsonify({"error": {"status": status, "message": message}})
    r.status_code = status
    return r


def api_view(scope: str):
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            key = apikeys.authenticate(token)
            if key is None:
                return _err(401, "Missing, invalid, expired or revoked API key.")
            set_context(TenantContext(tenant_id=key.organization_id))
            org = db.session.get(Organization, key.organization_id)
            if org is None or org.is_suspended:
                return _err(403, "This organization is suspended.")
            if not apikeys.plan_allows(org):
                return _err(402, "API access needs an active Professional plan or above.")
            if scope not in (key.scopes or []):
                return _err(403, f"This key doesn't have the '{scope}' scope.")
            live = db.session.get(type(key), key.id)
            live.last_used_at, live.use_count = utcnow(), live.use_count + 1
            db.session.commit()
            g.api_key = live
            return view(*args, **kwargs)
        return limiter.limit("120 per minute", key_func=_key_func)(wrapped)
    return deco


def _val(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _page(stmt, serialize):
    limit = max(1, min(request.args.get("limit", 50, type=int), 200))
    offset = max(0, request.args.get("offset", 0, type=int))
    total = db.session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.session.scalars(stmt.limit(limit).offset(offset)).all()
    return jsonify({"data": [serialize(r) for r in rows], "meta": {"total": total, "limit": limit, "offset": offset}})


def _name(u: User | None):
    return u.full_name if u else None


def project_json(p: Project) -> dict:
    return {k: _val(v) for k, v in {
        "id": p.id, "code": p.code, "name": p.name, "status": p.status, "health": p.health, "priority": p.priority,
        "progress": p.progress, "start_date": p.start_date, "end_date": p.end_date, "budget": p.budget,
        "currency": p.currency, "location": p.location, "funder": p.funder, "manager": _name(p.manager),
        "updated_at": p.updated_at}.items()}


def _project_or_404(pid) -> Project:
    p = db.session.get(Project, pid)
    if p is None:
        abort(404)
    return p


@bp.errorhandler(404)
def _404(e):
    return _err(404, "Not found.")


@bp.errorhandler(429)
def _429(e):
    return _err(429, "Too many requests: the limit is 120 a minute per key.")


@bp.route("/v1/projects")
@api_view("projects")
def projects():
    stmt = select(Project).order_by(Project.id)
    if request.args.get("status"):
        stmt = stmt.where(Project.status == request.args["status"])
    return _page(stmt, project_json)


@bp.route("/v1/projects/<int:pid>")
@api_view("projects")
def project(pid):
    return jsonify({"data": project_json(_project_or_404(pid))})


@bp.route("/v1/projects/<int:pid>/tasks")
@api_view("projects")
def tasks(pid):
    _project_or_404(pid)
    stmt = select(Task).where(Task.project_id == pid).order_by(Task.id)
    return _page(stmt, lambda t: {k: _val(v) for k, v in {
        "id": t.id, "title": t.title, "status": t.status, "priority": t.priority, "progress": t.progress,
        "is_milestone": t.is_milestone, "parent_id": t.parent_id, "start_date": t.start_date, "due_date": t.due_date,
        "assignee": _name(t.assignee), "completed_at": t.completed_at}.items()})


@bp.route("/v1/projects/<int:pid>/budget")
@api_view("finance")
def budget(pid):
    p = _project_or_404(pid)
    f = fin.project_finance(p)
    return jsonify({"data": {
        "project_id": p.id, "currency": p.currency, "budget_status": p.budget_status,
        "total_budget": _val(f["bac"]), "spent": _val(f["ac"]), "committed": _val(f["committed"]),
        "remaining": _val(f["remaining"]), "cpi": f["cpi"], "spi": f["spi"],
        "lines": [{"id": r["line"].id, "code": r["line"].code, "category": r["line"].category,
                   "description": r["line"].description, "budget": _val(r["budget"]), "spent": _val(r["spent"]),
                   "committed": _val(r["committed"]), "remaining": _val(r["remaining"])} for r in f["rows"]]}})


@bp.route("/v1/projects/<int:pid>/expenses")
@api_view("finance")
def expenses(pid):
    _project_or_404(pid)
    stmt = select(Expense).where(Expense.project_id == pid).order_by(Expense.spent_on, Expense.id)
    return _page(stmt, lambda e: {k: _val(v) for k, v in {
        "id": e.id, "reference": e.reference, "date": e.spent_on, "description": e.description,
        "budget_line": e.budget_line.code, "payee": e.payee, "amount": e.amount, "currency": e.currency,
        "amount_project_currency": e.amount_project, "status": e.status, "paid_at": e.paid_at}.items()})


def indicator_json(i: Indicator) -> dict:
    p = me_svc.progress(i)
    return {k: _val(v) for k, v in {
        "id": i.id, "project_id": i.project_id, "code": i.code, "name": i.name, "unit": i.unit,
        "measure": i.measure, "direction": i.direction, "result": i.result.code if i.result else None,
        "baseline": i.baseline_value, "baseline_date": i.baseline_date, "target": i.target_value,
        "target_date": i.target_date, "endline": i.endline_value, "frequency": i.frequency,
        "actual": p.actual, "actual_date": p.actual_date, "achievement_pct": p.achievement, "status": p.status,
        "next_data_due": p.next_due, "active": i.is_active}.items()}


@bp.route("/v1/indicators")
@api_view("me")
def indicators():
    stmt = select(Indicator).order_by(Indicator.project_id, Indicator.seq)
    if request.args.get("project", type=int):
        stmt = stmt.where(Indicator.project_id == request.args.get("project", type=int))
    return _page(stmt, indicator_json)


@bp.route("/v1/indicators/<int:iid>/values", methods=["GET"])
@api_view("me")
def indicator_values(iid):
    ind = db.session.get(Indicator, iid) or abort(404)
    stmt = select(IndicatorValue).where(IndicatorValue.indicator_id == ind.id).order_by(IndicatorValue.period_end)
    return _page(stmt, lambda v: {k: _val(x) for k, x in {
        "id": v.id, "period_end": v.period_end, "value": v.value, "status": v.status, "source": v.source,
        "notes": v.notes}.items()})


@bp.route("/v1/indicators/<int:iid>/values", methods=["POST"])
@api_view("me:write")
def indicator_value_create(iid):
    """Submit a result from another system (e.g. a data-collection tool). It always waits for an M&E Officer."""
    ind = db.session.get(Indicator, iid) or abort(404)
    body = request.get_json(silent=True) or {}
    try:
        period_end = date.fromisoformat(str(body.get("period_end", "")))
    except ValueError:
        return _err(422, "period_end must be a date like 2026-10-31.")
    try:
        value = me_svc.to_decimal(body.get("value"))
    except me_svc.MEError as exc:
        return _err(422, str(exc))
    if not ind.is_active or not ind.project.is_open:
        return _err(422, "This indicator isn't accepting results.")
    if period_end > date.today() or period_end < ind.baseline_date:
        return _err(422, "period_end must be between the baseline date and today.")
    if ind.is_percent and not (0 <= value <= 100):
        return _err(422, "A percentage must be between 0 and 100.")
    clash = db.session.scalar(select(IndicatorValue).where(IndicatorValue.indicator_id == ind.id,
                                                           IndicatorValue.period_end == period_end,
                                                           IndicatorValue.status != "rejected"))
    if clash is not None:
        return _err(409, "A result for this period already exists.")
    key = g.api_key
    row = IndicatorValue(organization_id=ind.organization_id, indicator_id=ind.id, period_end=period_end,
                         value=value, status="pending", reported_by_id=key.created_by_id,
                         source=(str(body.get("source") or f"API: {key.name}"))[:255],
                         notes=str(body.get("notes") or "")[:5000])
    db.session.add(row)
    from app.services import audit
    from app.services.notify import notify, users_with

    audit.record("me.value_reported", summary=f"API key “{key.name}” reported {value} for {ind.code}",
                 entity=ind, user=None)
    notify(users_with("me.manage"), "me.verify", f"A result for {ind.code} arrived through the API: please verify",
           f"/app/indicators/{ind.id}")
    db.session.commit()
    return jsonify({"data": {"id": row.id, "status": row.status}}), 201


@bp.route("/v1/risks")
@api_view("risks")
def risks():
    stmt = select(Risk).order_by(Risk.project_id, Risk.seq)
    if request.args.get("project", type=int):
        stmt = stmt.where(Risk.project_id == request.args.get("project", type=int))
    return _page(stmt, lambda r: {k: _val(v) for k, v in {
        "id": r.id, "project_id": r.project_id, "code": r.code, "title": r.title, "category": r.category,
        "likelihood": r.likelihood, "impact": r.impact, "score": r.score, "level": r.level, "response": r.response,
        "status": r.status, "owner": _name(r.owner), "review_date": r.review_date}.items()})


@bp.route("/v1/issues")
@api_view("risks")
def issues():
    stmt = select(Issue).order_by(Issue.project_id, Issue.seq)
    if request.args.get("project", type=int):
        stmt = stmt.where(Issue.project_id == request.args.get("project", type=int))
    return _page(stmt, lambda i: {k: _val(v) for k, v in {
        "id": i.id, "project_id": i.project_id, "code": i.code, "title": i.title, "priority": i.priority,
        "status": i.status, "assignee": _name(i.assignee), "due_date": i.due_date, "risk_id": i.risk_id,
        "resolved_at": i.resolved_at}.items()})


@bp.route("/v1/budget-lines/<int:lid>")
@api_view("finance")
def budget_line(lid):
    line = db.session.get(BudgetLine, lid) or abort(404)
    return jsonify({"data": {"id": line.id, "project_id": line.project_id, "code": line.code,
                             "description": line.description, "amount": _val(line.amount)}})


# ---------------------------------------------------------------- Paystack webhook
@bp.route("/webhooks/paystack", methods=["POST"])
@limiter.limit("300 per minute")
def paystack_webhook():
    raw = request.get_data(cache=False)
    if len(raw) > 100_000:
        return _err(413, "Too large.")
    result = billing.handle_webhook(raw, request.headers.get("X-Paystack-Signature", ""))
    if result == "bad-signature":
        return _err(401, "Invalid signature.")
    return jsonify({"received": True, "result": result})
