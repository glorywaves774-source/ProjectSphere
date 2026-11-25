"""Stage 10: procurement requests, quotations, purchase orders and vendors."""
from datetime import date

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.extensions import db
from app.models import (PROCUREMENT_STATUSES, VENDOR_CATEGORIES, VENDOR_STATUSES, BudgetLine, Document,
                        ProcurementRequest, Project, Quotation, User, Vendor)
from app.security.rbac import permission_required
from app.services import excel, pdf
from app.services import procurement as svc

from .forms import AwardForm, DecisionForm, ProcurementForm, QuotationForm, ReceiveForm, VendorForm
from .routes import PER_PAGE, bp

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_ROWS = 50


def _actor() -> User:
    return current_user._get_current_object()


def _fail(exc):
    db.session.rollback()
    flash(str(exc), "danger")


def _download(data, mimetype, filename):
    return Response(data, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={filename}",
                                                      "Cache-Control": "private, no-store"})


# ---------------------------------------------------------------- requests
@bp.route("/procurement")
@permission_required("procurement.view")
def procurement():
    a = request.args
    stmt = select(ProcurementRequest)
    if a.get("project", type=int):
        stmt = stmt.where(ProcurementRequest.project_id == a.get("project", type=int))
    status = a.get("status", "")
    if status == "active":
        stmt = stmt.where(ProcurementRequest.status.in_(["draft", "submitted", "approved", "ordered"]))
    elif status in dict(PROCUREMENT_STATUSES):
        stmt = stmt.where(ProcurementRequest.status == status)
    if a.get("mine") == "1":
        stmt = stmt.where(ProcurementRequest.requested_by_id == current_user.id)
    q = (a.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(ProcurementRequest.title.ilike(like) | ProcurementRequest.reference.ilike(like)
                          | ProcurementRequest.po_number.ilike(like))
    # drafts stay private to the requester and procurement staff
    if not svc.is_officer(current_user):
        stmt = stmt.where((ProcurementRequest.status != "draft") | (ProcurementRequest.requested_by_id == current_user.id))
    page = db.paginate(stmt.order_by(ProcurementRequest.created_at.desc()), per_page=PER_PAGE, error_out=False)
    projects = db.session.scalars(select(Project).order_by(Project.name)).all()
    return render_template("workspace/procurement.html", page=page, o=svc.overview(), projects=projects,
                           statuses=PROCUREMENT_STATUSES, status=status,
                           can_add=any(svc.can_request(current_user, p) for p in projects))


@bp.route("/procurement.xlsx")
@permission_required("procurement.view")
def procurement_xlsx():
    rows = db.session.scalars(select(ProcurementRequest).where(ProcurementRequest.status != "draft")
                              .order_by(ProcurementRequest.seq)).all()
    data = excel.procurement_register(rows, current_user.organization, current_user.full_name)
    return _download(data, XLSX, f"procurement-register-{date.today():%Y%m%d}.xlsx")


def _rows_from_form() -> list[dict]:
    rows = []
    for n in range(MAX_ROWS):
        key = f"item-{n}-"
        if not any(request.form.get(key + f) for f in ("description", "quantity", "unit_cost")):
            continue
        rows.append({f: request.form.get(key + f, "") for f in ("description", "specification", "quantity", "unit",
                                                                 "unit_cost")})
    return rows


def _form(pr=None):
    form = ProcurementForm(obj=pr)
    projects = [p for p in db.session.scalars(select(Project).where(Project.status.in_(["planning", "active",
                                                                                          "on_hold"]))
                                              .order_by(Project.name)) if svc.can_request(current_user, p)]
    if pr is not None and pr.project not in projects:
        projects.append(pr.project)
    if not projects:
        abort(403)
    form.project_id.choices = [(p.id, f"{p.code} · {p.name}") for p in projects]
    if request.method == "GET" and pr is None:
        form.project_id.data = request.args.get("project", type=int) or projects[0].id
    pid = form.project_id.data or projects[0].id
    lines = db.session.scalars(select(BudgetLine).where(BudgetLine.project_id.in_([p.id for p in projects]))
                               .order_by(BudgetLine.project_id, BudgetLine.code)).all()
    form.budget_line_id.choices = [(0, "Choose later")] + [(l.id, f"{l.code} · {l.description}") for l in lines
                                                           if l.project_id == pid] + \
        [(l.id, f"{l.code} · {l.description}") for l in lines if l.project_id != pid]
    if request.method == "GET" and pr is not None:
        form.budget_line_id.data = pr.budget_line_id or 0
    lines_by_project = {}
    for l in lines:
        lines_by_project.setdefault(l.project_id, []).append({"id": l.id, "label": f"{l.code} · {l.description}"})
    return form, lines_by_project


def _save(form, pr):
    project = db.session.get(Project, form.project_id.data) or abort(404)
    return svc.save_request(_actor(), project, pr, title=form.title.data, justification=form.justification.data or "",
                            needed_by=form.needed_by.data, delivery_location=form.delivery_location.data or "",
                            budget_line_id=form.budget_line_id.data or None, items=_rows_from_form())


@bp.route("/procurement/new", methods=["GET", "POST"])
@permission_required("procurement.view")
def procurement_new():
    form, lines = _form()
    rows = []
    if form.validate_on_submit():
        rows = _rows_from_form()
        try:
            pr = _save(form, None)
            if request.form.get("submit_now"):
                svc.submit(_actor(), pr)
        except svc.ProcurementError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash("Request sent for approval." if pr.status == "submitted" else "Request saved as a draft.", "success")
            return redirect(url_for("workspace.procurement_request", pr_id=pr.id))
    return render_template("workspace/procurement_form.html", form=form, pr=None, rows=rows, lines=lines,
                           max_rows=MAX_ROWS)


@bp.route("/procurement/<int:pr_id>/edit", methods=["GET", "POST"])
@permission_required("procurement.view")
def procurement_edit(pr_id):
    pr = db.session.get(ProcurementRequest, pr_id) or abort(404)
    if not svc.can_edit_request(current_user, pr):
        abort(403)
    form, lines = _form(pr)
    rows = [{"description": i.description, "specification": i.specification, "quantity": i.quantity,
             "unit": i.unit, "unit_cost": i.unit_cost} for i in pr.items]
    if form.validate_on_submit():
        rows = _rows_from_form()
        try:
            _save(form, pr)
            if request.form.get("submit_now"):
                svc.submit(_actor(), pr)
        except svc.ProcurementError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash("Request sent for approval." if pr.status == "submitted" else "Request updated.", "success")
            return redirect(url_for("workspace.procurement_request", pr_id=pr.id))
    return render_template("workspace/procurement_form.html", form=form, pr=pr, rows=rows, lines=lines,
                           max_rows=MAX_ROWS)


def _can_see(pr) -> bool:
    return pr.status != "draft" or pr.requested_by_id == current_user.id or svc.is_officer(current_user)


@bp.route("/procurement/<int:pr_id>", methods=["GET", "POST"])
@permission_required("procurement.view")
def procurement_request(pr_id):
    pr = db.session.get(ProcurementRequest, pr_id) or abort(404)
    if not _can_see(pr):
        abort(403)
    dform, qform, aform, rform = DecisionForm(prefix="d"), QuotationForm(prefix="q"), AwardForm(prefix="a"), \
        ReceiveForm(prefix="r")
    vendors = db.session.scalars(select(Vendor).where(Vendor.status.in_(["approved", "pending"]))
                                 .order_by(Vendor.name)).all()
    qform.vendor_id.choices = [(v.id, v.name + ("" if v.status == "approved" else " (not yet approved)"))
                               for v in vendors if all(q.vendor_id != v.id for q in pr.quotations)]
    docs = db.session.scalars(select(Document).where(Document.project_id == pr.project_id)
                              .order_by(Document.title)).all()
    qform.document_id.choices = [(0, "None")] + [(d.id, d.title) for d in docs]
    aform.quotation_id.choices = [(q.id, f"{q.vendor.name} · {q.amount:,} {q.currency}") for q in pr.quotations]
    if request.method == "GET":
        qform.currency.data = pr.project.currency
        rform.received_on.data = date.today()
        cheapest = svc.valid_quotes(pr)
        if cheapest:
            aform.quotation_id.data = min(cheapest, key=lambda q: q.amount_project).id
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "submit":
                svc.submit(_actor(), pr)
                msg = "Request sent for approval."
            elif action in ("approve", "reject"):
                dform.validate_on_submit()
                svc.decide(_actor(), pr, approve=action == "approve", note=dform.note.data or "",
                           allow_over=bool(dform.allow_over.data))
                msg = "Request approved. Procurement has been told." if action == "approve" else "Request rejected."
            elif action == "quote":
                if not qform.validate_on_submit():
                    errs = [e for es in qform.errors.values() for e in es]
                    raise svc.ProcurementError(errs[0] if errs else "Please check the quotation.")
                svc.add_quotation(_actor(), pr, vendor_id=qform.vendor_id.data, amount=qform.amount.data,
                                  currency=qform.currency.data, exchange_rate=qform.exchange_rate.data,
                                  delivery_days=qform.delivery_days.data, valid_until=qform.valid_until.data,
                                  reference=qform.reference.data or "", notes=qform.notes.data or "",
                                  document_id=qform.document_id.data or None)
                msg = "Quotation recorded."
            elif action == "remove_quote":
                q = db.session.get(Quotation, request.form.get("quote", type=int) or 0)
                if q is None or q.request_id != pr.id:
                    abort(404)
                svc.remove_quotation(_actor(), q)
                msg = "Quotation removed."
            elif action == "award":
                aform.validate_on_submit()
                svc.award(_actor(), pr, aform.quotation_id.data, aform.justification.data or "")
                msg = f"Awarded. Purchase order {pr.po_number} issued."
            elif action == "receive":
                if not rform.validate_on_submit():
                    raise svc.ProcurementError("Enter the date it was received.")
                svc.receive(_actor(), pr, received_on=rform.received_on.data, note=rform.note.data or "",
                            rating=rform.rating.data, invoice_ref=rform.invoice_ref.data or "")
                msg = "Receipt recorded." + (" The expense is ready for finance to pay." if pr.expense_id else "")
            elif action == "cancel":
                svc.cancel(_actor(), pr, request.form.get("note", ""))
                msg = "Request cancelled."
            elif action == "delete":
                svc.delete(_actor(), pr)
                db.session.commit()
                flash("Draft deleted.", "info")
                return redirect(url_for("workspace.procurement"))
            else:
                raise svc.ProcurementError("Unknown action.")
        except svc.ProcurementError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash(msg, "success")
        return redirect(url_for("workspace.procurement_request", pr_id=pr.id))
    me = current_user
    return render_template(
        "workspace/procurement_request.html", pr=pr, pos=svc.line_position(pr), dform=dform, qform=qform,
        aform=aform, rform=rform, valid=svc.valid_quotes(pr),
        can_edit=svc.can_edit_request(me, pr),
        can_decide=pr.status == "submitted" and svc.can_approve(me) and pr.requested_by_id != me.id,
        is_officer=svc.is_officer(me),
        can_receive=pr.status == "ordered" and (svc.is_officer(me) or pr.requested_by_id == me.id),
        can_cancel=pr.status not in ("received", "cancelled") and (pr.requested_by_id == me.id or svc.is_officer(me))
        and (pr.status != "ordered" or svc.is_officer(me)))


@bp.route("/procurement/<int:pr_id>/po.pdf")
@permission_required("procurement.view")
def procurement_po(pr_id):
    pr = db.session.get(ProcurementRequest, pr_id) or abort(404)
    if not pr.po_number:
        abort(404)
    data = pdf.purchase_order(pr=pr, org=current_user.organization, generated_by=current_user.full_name)
    return _download(data, "application/pdf", f"{pr.po_number}.pdf")


# ---------------------------------------------------------------- vendors
@bp.route("/vendors")
@permission_required("procurement.view")
def vendors():
    a = request.args
    stmt = select(Vendor)
    if a.get("status") in dict(VENDOR_STATUSES):
        stmt = stmt.where(Vendor.status == a["status"])
    if a.get("category") in dict(VENDOR_CATEGORIES):
        stmt = stmt.where(Vendor.category == a["category"])
    q = (a.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Vendor.name.ilike(like) | Vendor.code.ilike(like) | Vendor.tax_id.ilike(like))
    page = db.paginate(stmt.order_by(Vendor.name), per_page=PER_PAGE, error_out=False)
    stats = {v.id: svc.vendor_stats(v) for v in page.items}
    return render_template("workspace/vendors.html", page=page, stats=stats, statuses=VENDOR_STATUSES,
                           categories=VENDOR_CATEGORIES, is_officer=svc.is_officer(current_user))


@bp.route("/vendors/new", methods=["GET", "POST"])
@permission_required("procurement.manage")
def vendor_new():
    form = VendorForm()
    if form.validate_on_submit():
        try:
            v = svc.save_vendor(_actor(), None, **{f.name: f.data for f in form if f.name != "csrf_token"})
        except svc.ProcurementError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash(f"Vendor {v.code} registered. Another procurement officer approves them before awards.", "success")
            return redirect(url_for("workspace.vendor", vendor_id=v.id))
    return render_template("workspace/vendor_form.html", form=form, vendor=None)


@bp.route("/vendors/<int:vendor_id>/edit", methods=["GET", "POST"])
@permission_required("procurement.manage")
def vendor_edit(vendor_id):
    v = db.session.get(Vendor, vendor_id) or abort(404)
    form = VendorForm(obj=v)
    if form.validate_on_submit():
        try:
            svc.save_vendor(_actor(), v, **{f.name: f.data for f in form if f.name != "csrf_token"})
        except svc.ProcurementError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash("Vendor updated.", "success")
            return redirect(url_for("workspace.vendor", vendor_id=v.id))
    return render_template("workspace/vendor_form.html", form=form, vendor=v)


@bp.route("/vendors/<int:vendor_id>", methods=["GET", "POST"])
@permission_required("procurement.view")
def vendor(vendor_id):
    v = db.session.get(Vendor, vendor_id) or abort(404)
    if request.method == "POST":
        try:
            svc.set_vendor_status(_actor(), v, request.form.get("status", ""), request.form.get("note", ""))
        except svc.ProcurementError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash(f"{v.name} is now {v.status_label.lower()}.", "success")
        return redirect(url_for("workspace.vendor", vendor_id=v.id))
    return render_template("workspace/vendor.html", v=v, s=svc.vendor_stats(v), is_officer=svc.is_officer(current_user))
