"""Stage 11: subscription, invoices, Paystack checkout and API keys."""
from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.extensions import db
from app.models import ApiKey, Invoice, Plan, User
from app.security.rbac import permission_required
from app.services import apikeys, billing, pdf
from app.services.limits import usage_summary

from .forms import ApiKeyForm
from .routes import bp


def _actor() -> User:
    return current_user._get_current_object()


def _fail(exc):
    db.session.rollback()
    flash(str(exc), "danger")


@bp.route("/subscription")
@permission_required("subscription.view")
def subscription():
    org = current_user.organization
    plans = db.session.scalars(select(Plan).where(Plan.is_active.is_(True), Plan.is_public.is_(True))
                               .order_by(Plan.sort_order)).all()
    invoices = db.session.scalars(select(Invoice).order_by(Invoice.created_at.desc()).limit(50)).all()
    blockers = {p.id: billing.usage_blockers(org, p) for p in plans}
    return render_template("workspace/subscription.html", org=org, sub=org.subscription, usage=usage_summary(org),
                           plans=plans, invoices=invoices, blockers=blockers,
                           can_manage=billing.can_manage(current_user), online=billing.is_configured())


@bp.route("/billing")
@permission_required("subscription.view")
def billing_home():
    return redirect(url_for("workspace.subscription"))


@bp.route("/billing/checkout", methods=["POST"])
@permission_required("subscription.manage")
def billing_checkout():
    plan = db.session.get(Plan, request.form.get("plan", type=int) or 0) or abort(404)
    try:
        inv = billing.checkout_invoice(_actor(), plan, request.form.get("cycle", "monthly"))
    except billing.BillingError as exc:
        _fail(exc)
        return redirect(url_for("workspace.subscription"))
    db.session.commit()
    if inv.status == "paid":
        flash(f"You're now on the {plan.name} plan. Unused time on your previous plan covered the cost.", "success")
        return redirect(url_for("workspace.subscription"))
    return redirect(url_for("workspace.invoice", invoice_id=inv.id))


@bp.route("/billing/invoices/<int:invoice_id>")
@permission_required("subscription.view")
def invoice(invoice_id):
    inv = db.session.get(Invoice, invoice_id) or abort(404)
    from flask import current_app

    return render_template("workspace/invoice.html", inv=inv, can_manage=billing.can_manage(current_user),
                           online=billing.is_configured(), bank=current_app.config.get("PLATFORM_BANK_DETAILS"))


@bp.route("/billing/invoices/<int:invoice_id>/pay", methods=["POST"])
@permission_required("subscription.manage")
def invoice_pay(invoice_id):
    inv = db.session.get(Invoice, invoice_id) or abort(404)
    try:
        url = billing.start_checkout(_actor(), inv, url_for("workspace.billing_return", _external=True))
    except billing.BillingError as exc:
        db.session.commit()  # keep the failed attempt on record
        flash(str(exc), "danger")
        return redirect(url_for("workspace.invoice", invoice_id=inv.id))
    db.session.commit()
    if not url.startswith("https://checkout.paystack.com/"):
        flash("The payment page address looked wrong, so we stopped. Please try again.", "danger")
        return redirect(url_for("workspace.invoice", invoice_id=inv.id))
    return redirect(url)


@bp.route("/billing/return")
@permission_required("subscription.view")
def billing_return():
    """Paystack sends the customer back here; we confirm the payment with Paystack before believing it."""
    ref = (request.args.get("reference") or request.args.get("trxref") or "")[:100]
    pay = billing.verify(ref) if ref else None
    db.session.commit()
    if pay is None:
        flash("We couldn't find that payment.", "danger")
        return redirect(url_for("workspace.subscription"))
    if pay.status == "success":
        flash(f"Payment received. Thank you! {pay.invoice.number} is paid.", "success")
    elif pay.status == "pending":
        flash("Your payment is still being confirmed. This page will show it as paid once Paystack confirms.", "info")
    else:
        flash(f"The payment didn't go through ({pay.gateway_message or pay.status_label.lower()}). You can try again.",
              "warning")
    return redirect(url_for("workspace.invoice", invoice_id=pay.invoice_id))


@bp.route("/billing/invoices/<int:invoice_id>.pdf")
@permission_required("subscription.view")
def invoice_pdf(invoice_id):
    inv = db.session.get(Invoice, invoice_id) or abort(404)
    from flask import current_app

    data = pdf.invoice(inv=inv, bank=current_app.config.get("PLATFORM_BANK_DETAILS"))
    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": f"attachment; filename={inv.number}.pdf", "Cache-Control": "private, no-store"})


# ---------------------------------------------------------------- integrations
@bp.route("/integrations", methods=["GET", "POST"])
@permission_required("org.manage")
def integrations():
    form = ApiKeyForm()
    new_token = None
    if request.method == "POST":
        if request.form.get("action") == "revoke":
            key = db.session.get(ApiKey, request.form.get("key", type=int) or 0) or abort(404)
            apikeys.revoke(_actor(), key)
            db.session.commit()
            flash(f"Key “{key.name}” revoked. Anything using it stops working now.", "info")
            return redirect(url_for("workspace.integrations"))
        if form.validate_on_submit():
            try:
                key, token = apikeys.create(_actor(), form.name.data, form.scopes.data or [], form.expires.data or None)
            except apikeys.ApiKeyError as exc:
                _fail(exc)
            else:
                db.session.commit()
                new_token = token  # shown once on this page; never stored anywhere in readable form
                form = ApiKeyForm(formdata=None)
    keys = db.session.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    resp = Response(render_template("workspace/integrations.html", form=form, keys=keys, new_token=new_token,
                                    allowed=apikeys.plan_allows(current_user.organization),
                                    base=url_for("api.projects", _external=True).rsplit("/projects", 1)[0]))
    resp.headers["Cache-Control"] = "no-store"
    return resp
