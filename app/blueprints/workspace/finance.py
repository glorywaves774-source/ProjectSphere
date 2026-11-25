"""Stage 6: project budgets, expenses, approvals, finance overview and exchange rates."""
from datetime import date

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_, select

from app.extensions import db
from app.models import EXPENSE_STATUSES, BudgetLine, ExchangeRate, Expense, Project, User
from app.security.rbac import permission_required, tenant_required
from app.services import excel
from app.services import finance as svc
from app.services import tasks as task_svc

from .forms import BudgetLineForm, DecisionForm, ExpenseForm, PaymentForm, RateForm
from .routes import PER_PAGE, bp

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _actor() -> User:
    return current_user._get_current_object()


def _project(pid: int) -> Project:
    p = db.session.get(Project, pid)
    if p is None:
        abort(404)
    return p


def _xlsx(body: bytes, name: str) -> Response:
    return Response(body, mimetype=XLSX, headers={"Content-Disposition": f"attachment; filename={name}",
                                                  "Cache-Control": "no-store"})


def _guard(fn, *args, ok: str, **kwargs):
    try:
        result = fn(*args, **kwargs)
    except svc.FinanceError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return False, None
    db.session.commit()
    if ok:
        flash(ok, "success")
    return True, result


# ---------------------------------------------------------------- project budget
@bp.route("/projects/<int:project_id>/budget")
@tenant_required
def project_budget(project_id):
    project = _project(project_id)
    if not svc.can_view(current_user, project):
        abort(403)
    f = svc.project_finance(project)
    expenses = db.session.scalars(select(Expense).where(Expense.project_id == project.id)
                                  .order_by(Expense.spent_on.desc(), Expense.id.desc()).limit(10)).all()
    chart = {"labels": list(f["by_category"]),
             "series": [[float(v["budget"]) for v in f["by_category"].values()],
                        [float(v["spent"]) for v in f["by_category"].values()]]}
    return render_template("workspace/project_budget.html", project=project, f=f, expenses=expenses, chart=chart,
                           can_plan=svc.can_plan(current_user, project), can_approve=svc.can_approve(current_user),
                           can_request=svc.can_request(current_user, project))


@bp.route("/projects/<int:project_id>/budget.xlsx")
@tenant_required
def project_budget_xlsx(project_id):
    project = _project(project_id)
    if not svc.can_view(current_user, project):
        abort(403)
    body = excel.budget_vs_actual(project, current_user.organization, svc.project_finance(project),
                                  current_user.full_name)
    return _xlsx(body, f"{project.code}-budget-vs-actual.xlsx")


@bp.route("/projects/<int:project_id>/budget/<action>", methods=["POST"])
@tenant_required
def project_budget_action(project_id, action):
    project = _project(project_id)
    note = request.form.get("note", "")
    if action == "submit":
        _guard(svc.submit_budget, _actor(), project, ok="Budget submitted for approval.")
    elif action == "approve":
        _guard(svc.approve_budget, _actor(), project, ok="Budget approved. Spending can now be recorded.")
    elif action == "return":
        _guard(svc.return_budget, _actor(), project, note, ok="Budget returned to draft.")
    else:
        abort(404)
    return redirect(url_for("workspace.project_budget", project_id=project.id))


def _line_form(project, line=None):
    form = BudgetLineForm(obj=line)
    form.task_id.choices = [(0, "None")] + [(t.id, f"{code}  {t.title}") for t, code, _ in
                                            task_svc.wbs(task_svc.project_tasks(project))]
    if request.method == "GET" and line:
        form.task_id.data = line.task_id or 0
    return form


def _line_fields(form):
    return {"category": form.category.data, "description": form.description.data, "quantity": form.quantity.data,
            "unit": form.unit.data or "", "unit_cost": form.unit_cost.data, "task_id": form.task_id.data or None,
            "notes": form.notes.data or ""}


@bp.route("/projects/<int:project_id>/budget/lines/new", methods=["GET", "POST"])
@tenant_required
def budget_line_new(project_id):
    project = _project(project_id)
    if not svc.can_plan(current_user, project):
        abort(403)
    form = _line_form(project)
    if form.validate_on_submit():
        ok, _ = _guard(svc.save_line, _actor(), project, None, ok="Budget line added.", **_line_fields(form))
        if ok:
            if request.form.get("again"):
                return redirect(url_for("workspace.budget_line_new", project_id=project.id))
            return redirect(url_for("workspace.project_budget", project_id=project.id))
    return render_template("workspace/budget_line_form.html", form=form, project=project, line=None)


@bp.route("/budget-lines/<int:line_id>", methods=["GET", "POST"])
@tenant_required
def budget_line_edit(line_id):
    line = db.session.get(BudgetLine, line_id) or abort(404)
    project = line.project
    if not svc.can_plan(current_user, project):
        abort(403)
    if request.method == "POST" and request.form.get("action") == "delete":
        ok, _ = _guard(svc.delete_line, _actor(), line, ok="Budget line deleted.")
        return redirect(url_for("workspace.project_budget", project_id=project.id) if ok
                        else url_for("workspace.budget_line_edit", line_id=line_id))
    form = _line_form(project, line)
    if form.validate_on_submit():
        ok, _ = _guard(svc.save_line, _actor(), project, line, ok="Budget line saved.", **_line_fields(form))
        if ok:
            return redirect(url_for("workspace.project_budget", project_id=project.id))
    return render_template("workspace/budget_line_form.html", form=form, project=project, line=line)


# ---------------------------------------------------------------- expenses
def _expense_form(project, expense=None):
    form = ExpenseForm(obj=expense)
    form.budget_line_id.choices = [(l.id, f"{l.code}  {l.description}") for l in svc.lines(project)]
    if request.method == "GET" and expense is None:
        form.spent_on.data = date.today()
        form.currency.data = project.currency
    if request.method == "GET" and expense is not None and expense.currency == project.currency:
        form.exchange_rate.data = None
    return form


def _expense_fields(form):
    return {"budget_line_id": form.budget_line_id.data, "spent_on": form.spent_on.data,
            "description": form.description.data, "payee": form.payee.data or "",
            "invoice_ref": form.invoice_ref.data or "", "amount": form.amount.data, "currency": form.currency.data,
            "exchange_rate": form.exchange_rate.data}


@bp.route("/projects/<int:project_id>/expenses/new", methods=["GET", "POST"])
@tenant_required
def expense_new(project_id):
    project = _project(project_id)
    if not svc.can_request(current_user, project):
        abort(403)
    if project.budget_status != "approved":
        flash("Spending can only be recorded once the project budget is approved.", "warning")
        return redirect(url_for("workspace.project_budget", project_id=project.id))
    form = _expense_form(project)
    if form.validate_on_submit():
        ok, exp = _guard(svc.save_expense, _actor(), project, None, ok="", **_expense_fields(form))
        if ok:
            if request.form.get("submit_now"):
                ok2, over = _guard(svc.submit_expense, _actor(), exp, ok="")
                if ok2:
                    flash(f"{exp.reference} sent for approval." + (" It would take the line over budget, so the approver will see a warning." if over else ""),
                          "warning" if over else "success")
            else:
                flash(f"{exp.reference} saved as a draft.", "success")
            return redirect(url_for("workspace.expense", expense_id=exp.id))
    return render_template("workspace/expense_form.html", form=form, project=project, expense=None,
                           rates=svc.rates())


@bp.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
@tenant_required
def expense_edit(expense_id):
    exp = db.session.get(Expense, expense_id) or abort(404)
    project = exp.project
    if exp.requested_by_id != current_user.id or exp.status not in ("draft", "rejected"):
        abort(403)
    form = _expense_form(project, exp)
    if form.validate_on_submit():
        ok, _ = _guard(svc.save_expense, _actor(), project, exp, ok="Expense saved as a draft.", **_expense_fields(form))
        if ok:
            return redirect(url_for("workspace.expense", expense_id=exp.id))
    return render_template("workspace/expense_form.html", form=form, project=project, expense=exp, rates=svc.rates())


def _can_see_expense(exp: Expense) -> bool:
    return svc.can_view(current_user, exp.project) or exp.requested_by_id == current_user.id


@bp.route("/expenses/<int:expense_id>", methods=["GET", "POST"])
@tenant_required
def expense(expense_id):
    exp = db.session.get(Expense, expense_id) or abort(404)
    if not _can_see_expense(exp):
        abort(403)
    decision, payment = DecisionForm(prefix="d"), PaymentForm(prefix="p")
    if request.method == "GET":
        payment.paid_at.data = date.today()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "submit":
            ok, over = _guard(svc.submit_expense, _actor(), exp, ok="")
            if ok:
                flash("Sent for approval." + (" It would take the line over budget." if over else ""),
                      "warning" if over else "success")
        elif action in ("approve", "reject") and decision.validate_on_submit():
            _guard(svc.decide_expense, _actor(), exp, approve=action == "approve", note=decision.note.data or "",
                   allow_over=bool(decision.allow_over.data),
                   ok="Expense approved." if action == "approve" else "Expense rejected.")
        elif action == "pay" and payment.validate_on_submit():
            _guard(svc.mark_paid, _actor(), exp, paid_at=payment.paid_at.data, method=payment.method.data,
                   ref=payment.ref.data or "", ok="Payment recorded.")
        elif action == "delete":
            pid = exp.project_id
            ok, _ = _guard(svc.delete_expense, _actor(), exp, ok="Expense deleted.")
            if ok:
                return redirect(url_for("workspace.project_budget", project_id=pid))
        else:
            flash("Please check the form and try again.", "danger")
        return redirect(url_for("workspace.expense", expense_id=exp.id))
    line_f = next((r for r in svc.project_finance(exp.project)["rows"] if r["line"].id == exp.budget_line_id), None)
    return render_template("workspace/expense.html", e=exp, decision=decision, payment=payment, line_f=line_f,
                           can_approve=svc.can_approve(current_user) and exp.requested_by_id != current_user.id,
                           can_pay=svc.can_pay(current_user))


def _register_query():
    a = request.args
    stmt = select(Expense).join(Project, Project.id == Expense.project_id)
    if not current_user.can("budget.view"):
        stmt = stmt.where(or_(Expense.requested_by_id == current_user.id,
                              Project.manager_id == current_user.id))
    status = a.get("status", "")
    if status == "mine":
        stmt = stmt.where(Expense.requested_by_id == current_user.id)
    elif status in dict(EXPENSE_STATUSES):
        stmt = stmt.where(Expense.status == status)
    if a.get("project", type=int):
        stmt = stmt.where(Expense.project_id == a.get("project", type=int))
    q = (a.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Expense.description.ilike(like), Expense.payee.ilike(like),
                              Expense.reference.ilike(like), Expense.invoice_ref.ilike(like)))
    for key, op in (("from", ">="), ("to", "<=")):
        try:
            d = date.fromisoformat(a.get(key, ""))
        except ValueError:
            continue
        stmt = stmt.where(Expense.spent_on >= d if op == ">=" else Expense.spent_on <= d)
    return stmt.order_by(Expense.spent_on.desc(), Expense.id.desc()), status


@bp.route("/expenses")
@tenant_required
def expenses():
    stmt, status = _register_query()
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)
    projects = db.session.scalars(select(Project).order_by(Project.name)).all()
    return render_template("workspace/expenses.html", page=page, status=status, projects=projects,
                           statuses=EXPENSE_STATUSES)


@bp.route("/expenses.xlsx")
@tenant_required
def expenses_xlsx():
    stmt, _ = _register_query()
    body = excel.expense_register(db.session.scalars(stmt).all(), current_user.organization, svc.rates(),
                                  current_user.full_name)
    return _xlsx(body, f"expenses-{date.today():%Y%m%d}.xlsx")


# ---------------------------------------------------------------- overview & rates
@bp.route("/finance")
@permission_required("budget.view")
def finance():
    o = svc.org_finance(current_user.organization)
    return render_template("workspace/finance.html", o=o, can_approve=svc.can_approve(current_user))


@bp.route("/finance/rates", methods=["GET", "POST"])
@permission_required("budget.view")
def exchange_rates():
    form = RateForm()
    base = current_user.organization.currency
    form.currency.choices = [c for c in form.currency.choices if c[0] != base]
    if request.method == "GET":
        form.effective_on.data = date.today()
    if request.method == "POST":
        if not svc.can_pay(current_user):
            abort(403)
        if form.validate_on_submit():
            ok, _ = _guard(svc.set_rate, _actor(), form.currency.data, form.rate.data, form.effective_on.data,
                           ok="Exchange rate saved.")
            if ok:
                return redirect(url_for("workspace.exchange_rates"))
    rows = db.session.scalars(select(ExchangeRate).order_by(ExchangeRate.currency)).all()
    return render_template("workspace/exchange_rates.html", form=form, rows=rows, base=base,
                           can_edit=svc.can_pay(current_user))
