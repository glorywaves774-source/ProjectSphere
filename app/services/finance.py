"""Stage 6: budgets, expenditure, approvals, exchange rates and variance analysis.

Money rules:
* All amounts are Decimal, rounded to 2 places; rates to 6 places.
* Spending is recorded against an *approved* budget line.
* Nobody can approve their own expense (segregation of duties).
* Approving spending beyond a line's budget needs an explicit, recorded override.
"""
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select

from app.extensions import db
from app.models import (BUDGET_CATEGORIES, COMMITTED_STATUSES, SPENT_STATUSES, BudgetLine, ExchangeRate, Expense,
                        Project, User, utcnow)

from . import audit
from .notify import notify, users_with
from . import projects as project_svc

CENT = Decimal("0.01")


class FinanceError(ValueError):
    pass


def money(x) -> Decimal:
    return Decimal(x or 0).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- access
def can_view(user: User, project: Project) -> bool:
    return user.can("budget.view") or project_svc.can_edit(user, project)


def can_plan(user: User, project: Project) -> bool:
    return user.can("budget.manage") or project_svc.can_edit(user, project)


def can_request(user: User, project: Project) -> bool:
    return can_plan(user, project)


def can_approve(user: User) -> bool:
    return user.can("budget.approve")


def can_pay(user: User) -> bool:
    return user.can("budget.manage")


# ---------------------------------------------------------------- exchange rates
def rates() -> dict[str, Decimal]:
    return {r.currency: Decimal(r.rate_to_base) for r in db.session.scalars(select(ExchangeRate))}


def base_currency(org) -> str:
    return org.currency


def rate_between(from_cur: str, to_cur: str, org) -> Decimal | None:
    """Units of `to_cur` per 1 `from_cur`, via the organization's base currency."""
    if from_cur == to_cur:
        return Decimal(1)
    base = base_currency(org)
    table = rates()
    table[base] = Decimal(1)
    if from_cur not in table or to_cur not in table:
        return None
    return (table[from_cur] / table[to_cur]).quantize(Decimal("0.000001"))


def convert(amount, from_cur: str, to_cur: str, org) -> Decimal | None:
    r = rate_between(from_cur, to_cur, org)
    return None if r is None else money(Decimal(amount) * r)


def set_rate(actor: User, currency: str, rate: Decimal, effective_on: date) -> ExchangeRate:
    if not can_pay(actor):
        raise FinanceError("Only finance staff can set exchange rates.")
    currency = currency.upper()
    if currency == base_currency(actor.organization):
        raise FinanceError("The base currency is always 1.")
    if rate is None or rate <= 0:
        raise FinanceError("The rate must be greater than zero.")
    row = db.session.scalar(select(ExchangeRate).where(ExchangeRate.currency == currency))
    if row is None:
        row = ExchangeRate(organization_id=actor.organization_id, currency=currency)
        db.session.add(row)
    row.rate_to_base, row.effective_on, row.updated_by_id = rate, effective_on, actor.id
    audit.record("finance.rate_set", summary=f"1 {currency} = {rate} {base_currency(actor.organization)}", entity=row)
    return row


# ---------------------------------------------------------------- budget lines
def lines(project: Project) -> list[BudgetLine]:
    return db.session.scalars(select(BudgetLine).where(BudgetLine.project_id == project.id)
                              .order_by(BudgetLine.code)).all()


def _next_code(project: Project) -> str:
    n = len(db.session.scalars(select(BudgetLine.id).where(BudgetLine.project_id == project.id)).all())
    while True:
        n += 1
        code = f"BL-{n:02d}"
        if not db.session.scalar(select(BudgetLine.id).where(BudgetLine.project_id == project.id,
                                                             BudgetLine.code == code)):
            return code


def _check_draft(actor: User, project: Project) -> None:
    if not can_plan(actor, project):
        raise FinanceError("Only the project manager or finance staff can change the budget.")
    if not project.is_open:
        raise FinanceError("This project is closed.")
    if project.budget_status != "draft":
        raise FinanceError("The budget is submitted or approved. An approver must reopen it for revision first.")


def _validate_line(fields: dict, project: Project) -> None:
    if fields.get("category") not in dict(BUDGET_CATEGORIES):
        raise FinanceError("Choose a budget category.")
    if fields.get("quantity") is None or fields["quantity"] <= 0:
        raise FinanceError("Quantity must be more than zero.")
    if fields.get("unit_cost") is None or fields["unit_cost"] < 0:
        raise FinanceError("Unit cost can't be negative.")
    if fields.get("task_id"):
        from app.models import Task

        t = db.session.get(Task, fields["task_id"])
        if t is None or t.project_id != project.id:
            raise FinanceError("Link the line to a task in this project.")


def save_line(actor: User, project: Project, line: BudgetLine | None, **fields) -> BudgetLine:
    _check_draft(actor, project)
    fields["task_id"] = fields.get("task_id") or None
    _validate_line(fields, project)
    creating = line is None
    if creating:
        line = BudgetLine(organization_id=project.organization_id, project_id=project.id, code=_next_code(project))
        db.session.add(line)
    for k in ("category", "description", "quantity", "unit", "unit_cost", "task_id", "notes"):
        if k in fields:
            setattr(line, k, fields[k] if fields[k] is not None or k == "task_id" else "")
    line.description = line.description.strip()
    db.session.flush()
    audit.record("finance.line_saved", summary=f"{'Added' if creating else 'Edited'} budget line {line.code} on {project.code}: "
                 f"{line.description} ({money(line.amount):,} {project.currency})", entity=line)
    return line


def delete_line(actor: User, line: BudgetLine) -> None:
    _check_draft(actor, line.project)
    used = db.session.scalar(select(func.count(Expense.id)).where(Expense.budget_line_id == line.id))
    if used:
        raise FinanceError("This line has expenses recorded against it, so it can't be deleted.")
    audit.record("finance.line_deleted", summary=f"Deleted budget line {line.code} on {line.project.code}", entity=line)
    db.session.delete(line)


def allocated(project: Project) -> Decimal:
    return money(sum((l.amount for l in lines(project)), Decimal(0)))


def submit_budget(actor: User, project: Project) -> None:
    _check_draft(actor, project)
    if not lines(project):
        raise FinanceError("Add at least one budget line before submitting.")
    project.budget_status = "submitted"
    notify(users_with("budget.approve"), "approval", f"Budget for {project.name} is waiting for approval",
           f"/app/projects/{project.id}/budget", actor=actor)
    audit.record("finance.budget_submitted", summary=f"Budget for {project.code} submitted for approval "
                 f"({allocated(project):,} {project.currency})", entity=project)


def approve_budget(actor: User, project: Project) -> None:
    if not can_approve(actor):
        raise FinanceError("You don't have permission to approve budgets.")
    if project.budget_status != "submitted":
        raise FinanceError("Only a submitted budget can be approved.")
    project.budget_status = "approved"
    project.budget_approved_by_id, project.budget_approved_at = actor.id, utcnow()
    if project.budget is None:
        project.budget = allocated(project)
    notify([project.manager_id] if project.manager_id else [], "approval",
           f"The budget for {project.name} was approved", f"/app/projects/{project.id}/budget", actor=actor)
    audit.record("finance.budget_approved", summary=f"Approved the budget for {project.code} ({allocated(project):,} {project.currency})",
                 entity=project)


def return_budget(actor: User, project: Project, note: str) -> None:
    """Send a submitted budget back, or reopen an approved budget for revision."""
    if not can_approve(actor):
        raise FinanceError("You don't have permission to approve budgets.")
    if project.budget_status == "draft":
        raise FinanceError("The budget is already a draft.")
    if len(note.strip()) < 3:
        raise FinanceError("Explain why the budget is being returned or reopened.")
    was = project.budget_status
    project.budget_status = "draft"
    notify([project.manager_id] if project.manager_id else [], "approval",
           f"The budget for {project.name} was returned to draft: {note.strip()[:80]}",
           f"/app/projects/{project.id}/budget", actor=actor)
    project.budget_approved_by_id = project.budget_approved_at = None
    audit.record("finance.budget_reopened", summary=f"Budget for {project.code} {'returned' if was == 'submitted' else 'reopened for revision'}: {note.strip()[:150]}",
                 entity=project)


# ---------------------------------------------------------------- expenses
def _line_spend(line_id: int, statuses: set[str], exclude_id: int | None = None) -> Decimal:
    stmt = select(func.coalesce(func.sum(Expense.amount_project), 0)).where(
        Expense.budget_line_id == line_id, Expense.status.in_(statuses))
    if exclude_id:
        stmt = stmt.where(Expense.id != exclude_id)
    return money(db.session.scalar(stmt))


def _next_reference() -> tuple[int, str]:
    seq = (db.session.scalar(select(func.max(Expense.seq))) or 0) + 1
    return seq, f"EXP-{date.today().year}-{seq:04d}"


def save_expense(actor: User, project: Project, expense: Expense | None, *, budget_line_id: int, spent_on: date,
                 description: str, payee: str, invoice_ref: str, amount: Decimal, currency: str,
                 exchange_rate: Decimal | None) -> Expense:
    if not can_request(actor, project):
        raise FinanceError("Only the project manager or finance staff can record spending.")
    if not project.is_open:
        raise FinanceError("This project is closed.")
    if project.budget_status != "approved":
        raise FinanceError("Spending can only be recorded once the project budget is approved.")
    if expense is not None and (expense.status not in ("draft", "rejected") or expense.requested_by_id != actor.id):
        raise FinanceError("Only the person who drafted this expense can edit it, and only before it's submitted.")
    line = db.session.get(BudgetLine, budget_line_id)
    if line is None or line.project_id != project.id:
        raise FinanceError("Choose a budget line from this project.")
    if amount is None or amount <= 0:
        raise FinanceError("The amount must be more than zero.")
    if spent_on > date.today():
        raise FinanceError("The expense date can't be in the future.")
    currency = currency.upper()
    if currency == project.currency:
        exchange_rate = Decimal(1)
    elif not exchange_rate:
        exchange_rate = rate_between(currency, project.currency, actor.organization)
        if exchange_rate is None:
            raise FinanceError(f"There's no exchange rate for {currency}. Enter one, or ask finance to set it.")
    elif exchange_rate <= 0:
        raise FinanceError("The exchange rate must be greater than zero.")
    if expense is None:
        seq, ref = _next_reference()
        expense = Expense(organization_id=project.organization_id, project_id=project.id, seq=seq, reference=ref,
                          requested_by_id=actor.id)
        db.session.add(expense)
    expense.budget_line_id, expense.spent_on = line.id, spent_on
    expense.description, expense.payee, expense.invoice_ref = description.strip(), payee.strip(), invoice_ref.strip()
    expense.amount, expense.currency, expense.exchange_rate = money(amount), currency, Decimal(exchange_rate)
    expense.amount_project = money(Decimal(amount) * Decimal(exchange_rate))
    expense.status = "draft"
    db.session.flush()
    audit.record("finance.expense_saved", summary=f"Drafted {expense.reference}: {expense.description} "
                 f"({expense.amount:,} {currency})", entity=expense)
    return expense


def submit_expense(actor: User, expense: Expense) -> bool:
    """Returns True when the line would go over budget (flagged for the approver)."""
    if expense.requested_by_id != actor.id and not can_pay(actor):
        raise FinanceError("Only the requester or finance staff can submit this expense.")
    if expense.status not in ("draft", "rejected"):
        raise FinanceError("This expense has already been submitted.")
    line = expense.budget_line
    projected = _line_spend(line.id, SPENT_STATUSES | COMMITTED_STATUSES, exclude_id=expense.id) + expense.amount_project
    expense.over_budget = projected > line.amount
    expense.status, expense.submitted_at = "submitted", utcnow()
    expense.decided_by_id = expense.decided_at = None
    expense.decision_note = ""
    notify(users_with("budget.approve", exclude={expense.requested_by_id}), "approval",
           f"{expense.reference} ({expense.amount:,} {expense.currency}) is waiting for approval"
           + (" — over budget" if expense.over_budget else ""), f"/app/expenses/{expense.id}", actor=actor)
    audit.record("finance.expense_submitted", summary=f"Submitted {expense.reference} for approval"
                 + (" (would exceed the line budget)" if expense.over_budget else ""), entity=expense)
    return expense.over_budget


def decide_expense(actor: User, expense: Expense, *, approve: bool, note: str = "", allow_over: bool = False) -> None:
    if not can_approve(actor):
        raise FinanceError("You don't have permission to approve spending.")
    if expense.status != "submitted":
        raise FinanceError("This expense isn't awaiting approval.")
    if expense.requested_by_id == actor.id:
        raise FinanceError("You can't approve your own expense. Another approver must do it.")
    if approve:
        line = expense.budget_line
        after = _line_spend(line.id, SPENT_STATUSES, exclude_id=expense.id) + expense.amount_project
        if after > line.amount:
            if not allow_over:
                raise FinanceError(f"Approving this takes {line.code} over budget by "
                                   f"{after - line.amount:,} {expense.project.currency}. Tick the over-budget box to approve anyway.")
            if len(note.strip()) < 5:
                raise FinanceError("Explain why you're approving spending over budget.")
            expense.over_budget = True
        expense.status = "approved"
    else:
        if len(note.strip()) < 3:
            raise FinanceError("Say why the expense is rejected.")
        expense.status = "rejected"
    expense.decided_by_id, expense.decided_at, expense.decision_note = actor.id, utcnow(), note.strip()
    notify([expense.requested_by_id] if expense.requested_by_id else [], "approval",
           f"{expense.reference} was {'approved' if approve else 'rejected'} by {actor.full_name}",
           f"/app/expenses/{expense.id}", actor=actor)
    audit.record("finance.expense_approved" if approve else "finance.expense_rejected",
                 summary=f"{'Approved' if approve else 'Rejected'} {expense.reference} ({expense.amount:,} {expense.currency})"
                 + (" over budget" if approve and expense.over_budget else ""), entity=expense)


def mark_paid(actor: User, expense: Expense, *, paid_at: date, method: str, ref: str) -> None:
    if not can_pay(actor):
        raise FinanceError("Only finance staff can record payments.")
    if expense.status != "approved":
        raise FinanceError("Only approved expenses can be marked paid.")
    if paid_at > date.today():
        raise FinanceError("The payment date can't be in the future.")
    expense.status, expense.paid_at, expense.payment_method, expense.payment_ref = "paid", paid_at, method, ref.strip()
    notify([expense.requested_by_id] if expense.requested_by_id else [], "approval",
           f"{expense.reference} has been paid", f"/app/expenses/{expense.id}", actor=actor)
    audit.record("finance.expense_paid", summary=f"Paid {expense.reference}", entity=expense)


def delete_expense(actor: User, expense: Expense) -> None:
    if expense.status not in ("draft", "rejected") or expense.requested_by_id != actor.id:
        raise FinanceError("Only the requester can delete a draft or rejected expense.")
    audit.record("finance.expense_deleted", summary=f"Deleted {expense.reference}", entity=expense)
    db.session.delete(expense)


# ---------------------------------------------------------------- analysis (live data only)
def project_finance(project: Project) -> dict:
    ls = lines(project)
    exp = db.session.scalars(select(Expense).where(Expense.project_id == project.id)).all()
    spent_by, committed_by = defaultdict(Decimal), defaultdict(Decimal)
    for e in exp:
        if e.status in SPENT_STATUSES:
            spent_by[e.budget_line_id] += e.amount_project
        elif e.status in COMMITTED_STATUSES:
            committed_by[e.budget_line_id] += e.amount_project
    rows, cats = [], defaultdict(lambda: {"budget": Decimal(0), "spent": Decimal(0)})
    for l in ls:
        b, s, c = l.amount, money(spent_by[l.id]), money(committed_by[l.id])
        rows.append({"line": l, "budget": b, "spent": s, "committed": c, "remaining": money(b - s - c),
                     "used_pct": round(float(s / b * 100)) if b else (100 if s else 0),
                     "variance": money(b - s), "over": s > b})
        cats[l.category_label]["budget"] += b
        cats[l.category_label]["spent"] += s
    bac = money(sum((r["budget"] for r in rows), Decimal(0)))
    ac = money(sum((r["spent"] for r in rows), Decimal(0)))
    committed = money(sum((r["committed"] for r in rows), Decimal(0)))
    ev = money(bac * Decimal(project.progress) / 100)
    elapsed = project.time_elapsed_pct
    pv = money(bac * Decimal(elapsed) / 100) if elapsed is not None else None
    monthly = defaultdict(Decimal)
    for e in exp:
        if e.status in SPENT_STATUSES:
            monthly[e.spent_on.strftime("%Y-%m")] += e.amount_project
    months = sorted(monthly)
    return {
        "rows": rows, "bac": bac, "ac": ac, "committed": committed, "remaining": money(bac - ac - committed),
        "used_pct": round(float(ac / bac * 100)) if bac else 0, "ev": ev, "pv": pv,
        "cpi": round(float(ev / ac), 2) if ac else None,
        "spi": round(float(ev / pv), 2) if pv else None,
        "envelope": money(project.budget) if project.budget is not None else None,
        "unallocated": money(project.budget - bac) if project.budget is not None else None,
        "by_category": {k: {"budget": money(v["budget"]), "spent": money(v["spent"])} for k, v in cats.items()},
        "monthly": {"labels": [_month_label(m) for m in months], "values": [float(money(monthly[m])) for m in months]},
        "pending": sum(1 for e in exp if e.status == "submitted"),
        "over_lines": sum(1 for r in rows if r["over"]),
    }


def _month_label(key: str) -> str:
    y, m = key.split("-")
    return f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(m) - 1]} {y[2:]}"


def org_finance(org) -> dict:
    """Organization-wide figures in the base currency. Projects whose currency has no rate are listed separately."""
    base = base_currency(org)
    out_rows, missing = [], []
    total_budget = total_spent = Decimal(0)
    cats = defaultdict(Decimal)
    monthly = defaultdict(Decimal)
    projects = db.session.scalars(select(Project).where(Project.status.in_(["planning", "active", "on_hold"]))
                                  .order_by(Project.name)).all()
    for p in projects:
        f = project_finance(p)
        r = rate_between(p.currency, base, org)
        if r is None:
            missing.append(p)
            continue
        b, s = money(f["bac"] * r), money(f["ac"] * r)
        total_budget += b
        total_spent += s
        out_rows.append({"project": p, "budget": b, "spent": s, "used_pct": f["used_pct"], "cpi": f["cpi"],
                         "progress": p.progress, "status": p.budget_status, "pending": f["pending"],
                         "over_lines": f["over_lines"]})
        for cat, v in f["by_category"].items():
            cats[cat] += money(v["spent"] * r)
        for e in db.session.scalars(select(Expense).where(Expense.project_id == p.id,
                                                          Expense.status.in_(SPENT_STATUSES))):
            monthly[e.spent_on.strftime("%Y-%m")] += money(e.amount_project * r)
    months = sorted(monthly)[-12:]
    pending = db.session.scalars(select(Expense).where(Expense.status == "submitted")
                                 .order_by(Expense.submitted_at)).all()
    budgets_waiting = db.session.scalars(select(Project).where(Project.budget_status == "submitted")).all()
    return {
        "base": base, "rows": out_rows, "missing": missing, "total_budget": money(total_budget),
        "total_spent": money(total_spent),
        "used_pct": round(float(total_spent / total_budget * 100)) if total_budget else 0,
        "by_category": {"labels": list(cats), "values": [float(v) for v in cats.values()]},
        "monthly": {"labels": [_month_label(m) for m in months], "values": [float(monthly[m]) for m in months]},
        "pending": pending, "budgets_waiting": budgets_waiting,
    }
