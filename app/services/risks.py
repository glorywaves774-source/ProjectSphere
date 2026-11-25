"""Stage 10: risk registers (5×5 likelihood × impact) and issue logs."""
from collections import Counter
from datetime import date

from sqlalchemy import func, select

from app.extensions import db
from app.models import (ISSUE_PRIORITIES, ISSUE_STATUSES, RISK_CATEGORIES, RISK_RESPONSES, Issue, IssueComment,
                        Project, Risk, RiskReview, User, utcnow)

from . import audit
from . import projects as project_svc
from .notify import notify


class RiskError(ValueError):
    pass


# ---------------------------------------------------------------- access
def can_view(user: User) -> bool:
    return user.can("risks.view") and user.can("projects.view")


def can_manage(user: User, project: Project) -> bool:
    """Risk managers (risks.manage) for any project they can edit, or the project's own manager."""
    return (user.can("risks.manage") and (user.can("portfolio.view") or project_svc.can_edit(user, project)
                                          or project.manager_id == user.id)) or project_svc.can_edit(user, project)


def _on_team(user: User, project: Project) -> bool:
    return project.manager_id == user.id or any(m.user_id == user.id for m in project.members)


def can_raise_issue(user: User, project: Project) -> bool:
    return can_view(user) and (can_manage(user, project) or _on_team(user, project))


def can_work_issue(user: User, issue: Issue) -> bool:
    return can_manage(user, issue.project) or issue.assignee_id == user.id


def _require_open(project: Project) -> None:
    if not project.is_open:
        raise RiskError("This project is closed.")


def _score(v, label) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise RiskError(f"Choose the {label}.") from None
    if not 1 <= v <= 5:
        raise RiskError(f"The {label} must be from 1 to 5.")
    return v


def _owner(owner_id):
    if not owner_id:
        return None
    u = db.session.get(User, owner_id)
    if u is None or not u.is_active_user:
        raise RiskError("Choose an active person from your organization.")
    return u


# ---------------------------------------------------------------- risks
def save_risk(actor: User, project: Project, risk: Risk | None, **f) -> Risk:
    if not can_manage(actor, project):
        raise RiskError("You can't change this project's risk register.")
    _require_open(project)
    title = (f.get("title") or "").strip()
    if len(title) < 5:
        raise RiskError("Describe the risk in a few words (at least 5 characters).")
    if f.get("category") not in dict(RISK_CATEGORIES) or f.get("response") not in dict(RISK_RESPONSES):
        raise RiskError("Choose a category and a response.")
    likelihood, impact = _score(f.get("likelihood"), "likelihood"), _score(f.get("impact"), "impact")
    review = f.get("review_date")
    if review and review < date.today() and risk is None:
        raise RiskError("The next review date can't be in the past.")
    if f["response"] != "accept" and likelihood * impact >= 10 and len((f.get("mitigation") or "").strip()) < 10:
        raise RiskError("High and critical risks need a mitigation plan (unless you accept the risk).")
    owner = _owner(f.get("owner_id"))
    creating = risk is None
    if creating:
        seq = (db.session.scalar(select(func.max(Risk.seq)).where(Risk.project_id == project.id)) or 0) + 1
        risk = Risk(organization_id=project.organization_id, project_id=project.id, seq=seq, code=f"RSK-{seq:02d}",
                    created_by_id=actor.id)
        db.session.add(risk)
    old_owner = risk.owner_id
    moved = not creating and (risk.likelihood, risk.impact) != (likelihood, impact)
    risk.title, risk.description = title[:200], (f.get("description") or "").strip()
    risk.category, risk.response, risk.likelihood, risk.impact = f["category"], f["response"], likelihood, impact
    risk.mitigation, risk.contingency = (f.get("mitigation") or "").strip(), (f.get("contingency") or "").strip()
    risk.owner_id, risk.review_date = owner.id if owner else None, review
    db.session.flush()
    if creating or moved:
        db.session.add(RiskReview(organization_id=risk.organization_id, risk_id=risk.id, likelihood=likelihood,
                                  impact=impact, note="Identified" if creating else "Rating changed on edit",
                                  reviewed_by_id=actor.id))
    audit.record("risk.saved", summary=f"{'Logged' if creating else 'Updated'} {risk.code} “{risk.title}” "
                                       f"({risk.level_label.lower()}, {risk.score}) in {project.code}", entity=risk)
    if owner and owner.id != old_owner:
        notify([owner.id], "risk", f"You own risk {risk.code} on {project.code}: {risk.title}", f"/app/risks/{risk.id}",
               actor=actor)
    if creating and risk.level == "critical" and project.manager_id:
        notify([project.manager_id], "risk", f"Critical risk logged on {project.code}: {risk.title}",
               f"/app/risks/{risk.id}", actor=actor)
    return risk


def review_risk(actor: User, risk: Risk, *, likelihood, impact, note: str, next_review: date | None) -> RiskReview:
    if not (can_manage(actor, risk.project) or risk.owner_id == actor.id):
        raise RiskError("Only the risk owner or the project's managers can reassess this risk.")
    if risk.status != "open":
        raise RiskError("Only open risks can be reassessed.")
    note = (note or "").strip()
    if len(note) < 3:
        raise RiskError("Add a short note on what has changed (or why it hasn't).")
    if next_review and next_review <= date.today():
        raise RiskError("The next review date must be in the future.")
    l, i = _score(likelihood, "likelihood"), _score(impact, "impact")
    before = risk.score
    risk.likelihood, risk.impact, risk.review_date = l, i, next_review
    r = RiskReview(organization_id=risk.organization_id, risk_id=risk.id, likelihood=l, impact=i, note=note[:5000],
                   reviewed_by_id=actor.id)
    db.session.add(r)
    risk.updated_at = utcnow()
    audit.record("risk.reviewed", summary=f"Reviewed {risk.code}: score {before} → {risk.score}", entity=risk)
    if risk.level == "critical" and before < 16 and risk.project.manager_id:
        notify([risk.project.manager_id], "risk", f"{risk.code} on {risk.project.code} is now critical",
               f"/app/risks/{risk.id}", actor=actor)
    return r


def close_risk(actor: User, risk: Risk, note: str) -> None:
    if not can_manage(actor, risk.project):
        raise RiskError("You can't close this risk.")
    if risk.status != "open":
        raise RiskError("This risk is already closed.")
    if len((note or "").strip()) < 3:
        raise RiskError("Say why the risk can be closed.")
    risk.status, risk.closed_at, risk.closure_note = "closed", utcnow(), note.strip()
    audit.record("risk.closed", summary=f"Closed {risk.code}", entity=risk)


def reopen_risk(actor: User, risk: Risk) -> None:
    if not can_manage(actor, risk.project):
        raise RiskError("You can't reopen this risk.")
    _require_open(risk.project)
    risk.status, risk.closed_at = "open", None
    audit.record("risk.reopened", summary=f"Reopened {risk.code}", entity=risk)


def escalate(actor: User, risk: Risk, *, priority: str, assignee_id, due_date: date | None, description: str) -> Issue:
    """The risk has happened: log it as an issue linked back to the risk."""
    if not can_manage(actor, risk.project):
        raise RiskError("You can't escalate this risk.")
    if risk.status != "open":
        raise RiskError("Only open risks can be escalated.")
    issue = save_issue(actor, risk.project, None, title=risk.title, priority=priority, assignee_id=assignee_id,
                       due_date=due_date, description=description or risk.description, status="open")
    issue.risk_id = risk.id
    risk.status, risk.closed_at, risk.closure_note = "occurred", utcnow(), f"Became issue {issue.code}"
    audit.record("risk.escalated", summary=f"{risk.code} occurred: logged as {issue.code}", entity=risk)
    return issue


# ---------------------------------------------------------------- issues
def save_issue(actor: User, project: Project, issue: Issue | None, **f) -> Issue:
    if issue is None and not can_raise_issue(actor, project):
        raise RiskError("You can't log issues on this project.")
    if issue is not None and not can_manage(actor, project) and issue.raised_by_id != actor.id:
        raise RiskError("Only the person who raised this issue or the project's managers can edit it.")
    _require_open(project)
    title = (f.get("title") or "").strip()
    if len(title) < 5:
        raise RiskError("Describe the issue in a few words (at least 5 characters).")
    if f.get("priority") not in dict(ISSUE_PRIORITIES):
        raise RiskError("Choose a priority.")
    assignee = _owner(f.get("assignee_id"))
    if f.get("due_date") and issue is None and f["due_date"] < date.today():
        raise RiskError("The due date can't be in the past.")
    creating = issue is None
    if creating:
        seq = (db.session.scalar(select(func.max(Issue.seq)).where(Issue.project_id == project.id)) or 0) + 1
        issue = Issue(organization_id=project.organization_id, project_id=project.id, seq=seq, code=f"ISS-{seq:02d}",
                      raised_by_id=actor.id, status="open")
        db.session.add(issue)
    old_assignee = issue.assignee_id
    issue.title, issue.description, issue.priority = title[:200], (f.get("description") or "").strip(), f["priority"]
    issue.assignee_id, issue.due_date = assignee.id if assignee else None, f.get("due_date")
    db.session.flush()
    audit.record("issue.saved", summary=f"{'Logged' if creating else 'Updated'} {issue.code} “{issue.title}” "
                                        f"({issue.priority}) in {project.code}", entity=issue)
    if assignee and assignee.id != old_assignee:
        notify([assignee.id], "issue", f"Issue {issue.code} on {project.code} is assigned to you: {issue.title}",
               f"/app/issues/{issue.id}", actor=actor)
    if creating and issue.priority in ("high", "critical") and project.manager_id:
        notify([project.manager_id], "issue", f"{issue.priority_label} issue logged on {project.code}: {issue.title}",
               f"/app/issues/{issue.id}", actor=actor)
    return issue


def set_issue_status(actor: User, issue: Issue, status: str, resolution: str = "") -> None:
    if status not in dict(ISSUE_STATUSES):
        raise RiskError("Choose a status.")
    if not can_work_issue(actor, issue):
        raise RiskError("Only the assignee or the project's managers can update this issue.")
    if status == "closed" and not can_manage(actor, issue.project):
        raise RiskError("Only the project's managers can close an issue.")
    resolution = (resolution or "").strip()
    if status in ("resolved", "closed") and len(resolution or issue.resolution) < 5:
        raise RiskError("Describe how the issue was resolved.")
    before = issue.status
    issue.status = status
    if resolution:
        issue.resolution = resolution
    if status in ("resolved", "closed") and issue.resolved_at is None:
        issue.resolved_at = utcnow()
    if status in ("open", "in_progress"):
        issue.resolved_at = None
    audit.record("issue.status", summary=f"{issue.code}: {before.replace('_', ' ')} → {issue.status_label.lower()}",
                 entity=issue)
    watchers = {issue.raised_by_id, issue.assignee_id, issue.project.manager_id} - {None}
    notify(watchers, "issue", f"{issue.code} is now {issue.status_label.lower()}: {issue.title}",
           f"/app/issues/{issue.id}", actor=actor)


def comment_issue(actor: User, issue: Issue, body: str) -> IssueComment:
    if not can_raise_issue(actor, issue.project) and not can_work_issue(actor, issue):
        raise RiskError("You can't comment on this issue.")
    body = (body or "").strip()
    if not body:
        raise RiskError("Write a comment first.")
    c = IssueComment(organization_id=issue.organization_id, issue_id=issue.id, author_id=actor.id, body=body[:5000])
    db.session.add(c)
    watchers = {issue.raised_by_id, issue.assignee_id} - {None}
    notify(watchers, "issue", f"{actor.full_name} commented on {issue.code}", f"/app/issues/{issue.id}", actor=actor)
    return c


# ---------------------------------------------------------------- analysis
def heatmap(risks) -> dict:
    """Counts per (likelihood, impact) cell for open risks."""
    c = Counter((r.likelihood, r.impact) for r in risks if r.status == "open")
    return {f"{l}-{i}": c.get((l, i), 0) for l in range(1, 6) for i in range(1, 6)}


def summary(project: Project | None = None) -> dict:
    rstmt, istmt = select(Risk), select(Issue)
    if project is not None:
        rstmt, istmt = rstmt.where(Risk.project_id == project.id), istmt.where(Issue.project_id == project.id)
    risks = db.session.scalars(rstmt).all()
    issues = db.session.scalars(istmt).all()
    open_risks = [r for r in risks if r.status == "open"]
    levels = Counter(r.level for r in open_risks)
    open_issues = [i for i in issues if i.is_open]
    return {
        "risks": risks, "open_risks": len(open_risks), "levels": {k: levels.get(k, 0) for k in
                                                                  ("critical", "high", "medium", "low")},
        "review_overdue": sum(1 for r in open_risks if r.review_overdue), "heat": heatmap(risks),
        "issues": issues, "open_issues": len(open_issues),
        "overdue_issues": sum(1 for i in open_issues if i.is_overdue),
        "critical_issues": sum(1 for i in open_issues if i.priority in ("high", "critical")),
    }


def send_review_reminders() -> int:
    """Daily: tell risk owners once when a risk's review date has passed."""
    from app.models import Notification, Organization
    from app.tenancy import tenant_scope

    sent = 0
    with tenant_scope(platform=True):
        org_ids = db.session.scalars(select(Organization.id)).all()
    for org_id in org_ids:
        with tenant_scope(org_id):
            due = db.session.scalars(select(Risk).where(Risk.status == "open", Risk.owner_id.is_not(None),
                                                        Risk.review_date < date.today())).all()
            for r in due:
                link = f"/app/risks/{r.id}"
                already = db.session.scalar(select(func.count(Notification.id)).where(
                    Notification.user_id == r.owner_id, Notification.kind == "risk.review", Notification.link == link,
                    func.date(Notification.created_at) >= r.review_date))
                if not already:
                    sent += notify([r.owner_id], "risk.review",
                                   f"Risk {r.code} ({r.project.code}) was due for review on {r.review_date:%d %b %Y}",
                                   link, organization_id=org_id)
            db.session.commit()
    return sent
