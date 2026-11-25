"""Stage 11: platform billing console (Super Admins)."""
from datetime import date, timedelta

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_, select

from app.extensions import db
from app.models import INVOICE_STATUSES, EmailOutbox, Invoice, Organization, Plan
from app.security.rbac import platform_admin_required
from app.services import billing, pdf

from .forms import ManualInvoiceForm, OfflinePaymentForm
from .routes import PER_PAGE, _get_org, bp


def _fail(exc):
    db.session.rollback()
    flash(str(exc), "danger")


@bp.route("/billing")
@platform_admin_required
def billing_home():
    a = request.args
    stmt = select(Invoice).join(Organization, Organization.id == Invoice.organization_id)
    status = a.get("status", "")
    if status == "overdue":
        stmt = stmt.where(Invoice.status == "open", Invoice.due_date < date.today())
    elif status in dict(INVOICE_STATUSES):
        stmt = stmt.where(Invoice.status == status)
    q = (a.get("q") or "").strip()
    if q:
        stmt = stmt.where(or_(Invoice.number.ilike(f"%{q}%"), Organization.name.ilike(f"%{q}%")))
    page = db.paginate(stmt.order_by(Invoice.created_at.desc()), per_page=PER_PAGE, error_out=False)
    mail = {s: db.session.query(EmailOutbox).filter(EmailOutbox.status == s).count() for s in ("queued", "failed")}
    return render_template("admin/billing.html", page=page, rev=billing.revenue_summary(), status=status,
                           statuses=INVOICE_STATUSES, online=billing.is_configured(), mail=mail)


@bp.route("/billing/invoices/<int:invoice_id>", methods=["GET", "POST"])
@platform_admin_required
def billing_invoice(invoice_id):
    inv = db.session.get(Invoice, invoice_id) or abort(404)
    pform = OfflinePaymentForm(prefix="p")
    if request.method == "GET":
        pform.paid_on.data = date.today()
    if request.method == "POST":
        try:
            if request.form.get("action") == "paid":
                if not pform.validate_on_submit():
                    raise billing.BillingError("Enter the reference and the date it was paid.")
                billing.record_manual_payment(current_user, inv, method=pform.method.data,
                                              reference=pform.reference.data, paid_on=pform.paid_on.data)
                msg = f"{inv.number} marked paid. The organization's admins have been emailed a receipt."
            elif request.form.get("action") == "void":
                billing.void(current_user, inv, request.form.get("note", ""))
                msg = f"{inv.number} voided."
            else:
                raise billing.BillingError("Unknown action.")
        except billing.BillingError as exc:
            _fail(exc)
        else:
            db.session.commit()
            flash(msg, "success")
        return redirect(url_for("admin.billing_invoice", invoice_id=inv.id))
    return render_template("admin/billing_invoice.html", inv=inv, pform=pform)


@bp.route("/billing/invoices/<int:invoice_id>.pdf")
@platform_admin_required
def billing_invoice_pdf(invoice_id):
    from flask import current_app

    inv = db.session.get(Invoice, invoice_id) or abort(404)
    return Response(pdf.invoice(inv=inv, bank=current_app.config.get("PLATFORM_BANK_DETAILS")),
                    mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={inv.number}.pdf"})


def invoice_form(org):
    form = ManualInvoiceForm(prefix="inv")
    form.plan_id.choices = [(0, "None (one-off charge)")] + [
        (p.id, p.name) for p in db.session.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order))]
    if request.method == "GET":
        form.due_date.data = date.today() + timedelta(days=14)
        form.period_start.data = date.today()
        form.currency.data = org.currency or "GHS"
    return form


@bp.route("/organizations/<org_id>/invoice", methods=["POST"])
@platform_admin_required
def organization_invoice(org_id):
    org = _get_org(org_id)
    form = invoice_form(org)
    if not form.validate_on_submit():
        flash("Please complete the invoice details.", "danger")
        return redirect(url_for("admin.organization_detail", org_id=org.id))
    try:
        inv = billing.manual_invoice(current_user, org, description=form.description.data, amount=form.amount.data,
                                     currency=form.currency.data, due_date=form.due_date.data,
                                     plan_id=form.plan_id.data or None, cycle=form.cycle.data,
                                     period_start=form.period_start.data or date.today())
    except billing.BillingError as exc:
        _fail(exc)
        return redirect(url_for("admin.organization_detail", org_id=org.id))
    db.session.commit()
    flash(f"Invoice {inv.number} issued. The organization's admins have been emailed.", "success")
    return redirect(url_for("admin.billing_invoice", invoice_id=inv.id))
