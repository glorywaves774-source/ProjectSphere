"""Stage 11: subscription billing with Paystack.

Flow: choose a plan and cycle → an invoice is issued → "Pay now" starts a Paystack checkout (card or mobile money)
→ Paystack sends the customer back (callback) and also calls our webhook → we verify the payment with Paystack's
API (amount, currency and status must match the invoice) → the invoice is marked paid and the subscription
switches to the plan and runs to the end of the paid period. Processing is idempotent: a payment reference is
only ever applied once, however many times the callback or webhook arrives.

Renewal invoices are issued a few days before each period ends; unpaid subscriptions become past due after a
grace period, which pauses new work (existing data stays readable) until the invoice is paid.

Paystack needs an email for each transaction. We send the organization's billing email (from its profile),
never a team member's personal address.
"""
import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from flask import current_app
from sqlalchemy import func, select, text

from app.extensions import db
from app.models import Invoice, Organization, Payment, Plan, Subscription, User, utcnow

from . import audit
from .email_outbox import queue
from .limits import count_usage
from .notify import users_with

TWO = Decimal("0.01")


class BillingError(ValueError):
    pass


# ---------------------------------------------------------------- helpers
def can_manage(user: User) -> bool:
    return user.can("subscription.manage")


def price_for(plan: Plan, cycle: str) -> Decimal | None:
    return plan.price_annual if cycle == "annual" else plan.price_monthly


def cycle_end(start: date, cycle: str) -> date:
    months = 12 if cycle == "annual" else 1
    y, m = divmod(start.month - 1 + months, 12)
    import calendar

    year, month = start.year + y, m + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def _next_number() -> str:
    n = db.session.execute(text("SELECT nextval('invoice_number_seq')")).scalar()
    return f"INV-{date.today().year}-{n:06d}"


def _admins(org_id) -> list[User]:
    return [u for u in users_with("subscription.manage") if u.organization_id == org_id]


def _email_admins(org_id, subject: str, body: str, link: str) -> None:
    for u in _admins(org_id):
        queue(u, subject, body, link, organization_id=org_id)


def usage_blockers(org: Organization, plan: Plan) -> list[str]:
    """What stops a move to this plan (downgrades must fit its limits)."""
    out = []
    for res, label in (("users", "active team members"), ("projects", "open projects"), ("storage_mb", "MB of storage")):
        limit = plan.limit_for(res)
        used = count_usage(org, res)
        if limit is not None and used > limit:
            out.append(f"{used} {label} (the {plan.name} plan allows {limit})")
    return out


def last_paid(org_id) -> Invoice | None:
    return db.session.scalar(select(Invoice).where(Invoice.organization_id == org_id, Invoice.status == "paid",
                                                   Invoice.period_end.is_not(None))
                             .order_by(Invoice.paid_at.desc()).limit(1))


def _credit(sub: Subscription, today: date) -> Decimal:
    """Unused time on the current paid period, credited when changing plan."""
    if sub is None or sub.status != Subscription.ACTIVE or not sub.current_period_end:
        return Decimal(0)
    inv = last_paid(sub.organization_id)
    if inv is None or not inv.period_start or not inv.period_end or inv.period_end <= today:
        return Decimal(0)
    total = (inv.period_end - inv.period_start).days or 1
    left = (inv.period_end - today).days
    return (inv.subtotal * Decimal(left) / Decimal(total)).quantize(TWO, ROUND_HALF_UP)


# ---------------------------------------------------------------- invoices
def checkout_invoice(actor: User, plan: Plan, cycle: str) -> Invoice:
    """The invoice to pay for moving to (or renewing) this plan and cycle."""
    if not can_manage(actor):
        raise BillingError("Only people who manage the subscription can change the plan.")
    if cycle not in ("monthly", "annual"):
        raise BillingError("Choose monthly or yearly billing.")
    if not plan.is_active or not plan.is_public:
        raise BillingError("That plan isn't available.")
    price = price_for(plan, cycle)
    if price is None:
        raise BillingError("This plan is priced individually. Contact our sales team for a quote.")
    org = db.session.get(Organization, actor.organization_id)
    blockers = usage_blockers(org, plan)
    if blockers:
        raise BillingError("Your organization has more than this plan allows: " + "; ".join(blockers) + ".")
    sub = org.subscription
    today = date.today()
    open_inv = db.session.scalar(select(Invoice).where(Invoice.organization_id == org.id, Invoice.status == "open",
                                                       Invoice.kind != "manual"))
    if open_inv is not None:
        if open_inv.plan_id == plan.id and open_inv.billing_cycle == cycle:
            return open_inv
        open_inv.status, open_inv.note = "void", "Replaced by a different plan choice"
        audit.record("billing.invoice_voided", summary=f"Voided {open_inv.number} (plan changed)", entity=open_inv)
    same = sub is not None and sub.status == Subscription.ACTIVE and sub.plan_id == plan.id \
        and sub.billing_cycle == cycle and sub.current_period_end
    if same:  # paying ahead: the next period follows the current one
        start = max(today, sub.current_period_end.date())
        kind, credit = "renewal", Decimal(0)
    else:
        start = today
        kind = "change" if sub is not None and sub.status == Subscription.ACTIVE else "new"
        credit = min(_credit(sub, today), price) if kind == "change" else Decimal(0)
    inv = Invoice(organization_id=org.id, number=_next_number(), kind=kind, plan_id=plan.id, billing_cycle=cycle,
                  period_start=start, period_end=cycle_end(start, cycle),
                  description=f"{plan.name} plan, {'yearly' if cycle == 'annual' else 'monthly'}",
                  subtotal=price, credit=credit, amount=(price - credit).quantize(TWO), currency=plan.currency,
                  status="open", due_date=today + timedelta(days=current_app.config["INVOICE_DUE_DAYS"]),
                  created_by_id=actor.id)
    db.session.add(inv)
    db.session.flush()
    audit.record("billing.invoice_issued", summary=f"Issued {inv.number}: {inv.description}, "
                                                   f"{inv.amount:,} {inv.currency}", entity=inv)
    if inv.amount == 0:  # fully covered by credit
        mark_paid(inv, method="credit", reference="Unused time on previous plan", actor=actor)
    return inv


def manual_invoice(admin: User, org: Organization, *, description: str, amount, currency: str, due_date: date,
                   plan_id: int | None = None, cycle: str = "monthly", period_start: date | None = None) -> Invoice:
    """Super Admin: invoice anything (e.g. an Enterprise contract)."""
    if not admin.is_platform_admin:
        raise BillingError("Only Kayron staff can issue manual invoices.")
    try:
        amt = Decimal(str(amount)).quantize(TWO)
    except Exception:
        raise BillingError("Enter the amount.") from None
    if amt <= 0:
        raise BillingError("The amount must be more than zero.")
    if len((description or "").strip()) < 3:
        raise BillingError("Describe what the invoice is for.")
    start = period_start if plan_id else None
    inv = Invoice(organization_id=org.id, number=_next_number(), kind="manual", plan_id=plan_id or None,
                  billing_cycle=cycle, period_start=start, period_end=cycle_end(start, cycle) if start else None,
                  description=description.strip()[:255], subtotal=amt, credit=Decimal(0), amount=amt,
                  currency=(currency or "GHS").upper()[:3], status="open", due_date=due_date, created_by_id=None)
    db.session.add(inv)
    db.session.flush()
    audit.record("billing.invoice_issued", summary=f"Issued manual invoice {inv.number} to {org.name}: "
                                                   f"{amt:,} {inv.currency}", entity=inv, organization_id=org.id)
    _email_admins(org.id, f"New invoice {inv.number} from Laari ProjectSphere",
                  f"Invoice {inv.number} for {inv.amount:,} {inv.currency} ({inv.description}) is due on "
                  f"{inv.due_date:%d %b %Y}.", "/app/billing")
    return inv


def void(admin: User, inv: Invoice, note: str) -> None:
    if not admin.is_platform_admin:
        raise BillingError("Only Kayron staff can void invoices.")
    if inv.status != "open":
        raise BillingError("Only unpaid invoices can be voided.")
    if len((note or "").strip()) < 3:
        raise BillingError("Say why the invoice is voided.")
    inv.status, inv.note = "void", note.strip()
    audit.record("billing.invoice_voided", summary=f"Voided {inv.number}: {inv.note}", entity=inv,
                 organization_id=inv.organization_id)


def mark_paid(inv: Invoice, *, method: str, reference: str, actor: User | None = None,
              paid_at: datetime | None = None) -> None:
    """Apply a payment: the invoice is paid and the subscription moves to the paid plan and period."""
    if inv.status == "paid":
        return  # idempotent
    if inv.status == "void":
        raise BillingError("This invoice was voided.")
    inv.status, inv.paid_at = "paid", paid_at or utcnow()
    inv.payment_method, inv.payment_reference = method, (reference or "")[:100]
    org = db.session.get(Organization, inv.organization_id)
    sub = org.subscription
    if inv.plan_id and inv.period_end:
        end = datetime.combine(inv.period_end, time(23, 59, 59), tzinfo=timezone.utc)
        if sub is None:
            sub = Subscription(organization_id=org.id, plan_id=inv.plan_id)
            db.session.add(sub)
        sub.plan_id, sub.billing_cycle = inv.plan_id, inv.billing_cycle
        sub.status, sub.current_period_end, sub.cancelled_at = Subscription.ACTIVE, end, None
        sub.trial_ends_at = None
        db.session.flush()
        db.session.expire(sub, ["plan"])
    audit.record("billing.invoice_paid", summary=f"{inv.number} paid ({inv.amount:,} {inv.currency}) by "
                 f"{method}" + (f", ref {reference}" if reference else ""), entity=inv, user=actor,
                 organization_id=inv.organization_id)
    _email_admins(inv.organization_id, f"Payment received for {inv.number}",
                  f"Thank you. We received {inv.amount:,} {inv.currency} for {inv.number} ({inv.description})."
                  + (f" Your plan runs until {inv.period_end:%d %b %Y}." if inv.period_end else ""),
                  "/app/billing")


def record_manual_payment(admin: User, inv: Invoice, *, method: str, reference: str, paid_on: date) -> None:
    if not admin.is_platform_admin:
        raise BillingError("Only Kayron staff can record offline payments.")
    if method not in ("bank", "cash", "cheque", "momo", "other"):
        raise BillingError("Choose how it was paid.")
    if len((reference or "").strip()) < 3:
        raise BillingError("Enter the bank or receipt reference.")
    if paid_on is None or paid_on > date.today():
        raise BillingError("Enter the date it was paid (not in the future).")
    mark_paid(inv, method=method, reference=reference.strip(), actor=admin,
              paid_at=datetime.combine(paid_on, time(12), tzinfo=timezone.utc))


# ---------------------------------------------------------------- Paystack
def is_configured() -> bool:
    return bool(current_app.config.get("PAYSTACK_SECRET_KEY"))


def _http(method: str, path: str, payload: dict | None = None) -> dict:
    """Call Paystack. Tests replace this through app.extensions['paystack_http']."""
    fake = current_app.extensions.get("paystack_http")
    if fake is not None:
        return fake(method, path, payload)
    req = urllib.request.Request(current_app.config["PAYSTACK_API"] + path, method=method,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Authorization": f"Bearer {current_app.config['PAYSTACK_SECRET_KEY']}",
                                          "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (fixed https endpoint)
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except Exception:
            return {"status": False, "message": f"Payment provider error ({exc.code})"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"status": False, "message": "Couldn't reach the payment provider. Please try again."}


def start_checkout(actor: User, inv: Invoice, callback_url: str) -> str:
    """Create a Paystack transaction and return the checkout page to send the customer to."""
    if not can_manage(actor):
        raise BillingError("Only people who manage the subscription can pay invoices.")
    if inv.status != "open":
        raise BillingError("This invoice isn't due.")
    if not is_configured():
        raise BillingError("Online payment isn't switched on yet. Please pay by bank transfer (details on the invoice).")
    org = db.session.get(Organization, inv.organization_id)
    if not org.email:
        raise BillingError("Add your organization's billing email in Organization profile first. Paystack sends the "
                           "receipt there.")
    reference = f"PS-{inv.number}-{secrets.token_hex(4)}"
    pay = Payment(organization_id=inv.organization_id, invoice_id=inv.id, reference=reference, amount=inv.amount,
                  currency=inv.currency, status="pending", started_by_id=actor.id)
    db.session.add(pay)
    db.session.flush()
    res = _http("POST", "/transaction/initialize", {
        "email": org.email, "amount": int((inv.amount * 100).to_integral_value()), "currency": inv.currency,
        "reference": reference, "callback_url": callback_url,
        "metadata": {"invoice": inv.number, "organization": str(org.id)},
    })
    if not res.get("status") or not (res.get("data") or {}).get("authorization_url"):
        pay.status, pay.gateway_message = "failed", str(res.get("message", "Initialisation failed"))[:255]
        raise BillingError(f"The payment couldn't be started: {pay.gateway_message}")
    audit.record("billing.checkout_started", summary=f"Started Paystack payment for {inv.number}", entity=inv)
    return res["data"]["authorization_url"]


def _apply_verified(pay: Payment, data: dict) -> bool:
    """Check Paystack's answer against what we expected; apply it once. Returns True when paid."""
    if pay.status == "success":
        return True
    status = data.get("status")
    expected = int((pay.amount * 100).to_integral_value())
    if status == "success" and int(data.get("amount") or 0) == expected and \
            (data.get("currency") or pay.currency).upper() == pay.currency:
        pay.status, pay.channel = "success", str(data.get("channel") or "")[:30]
        pay.gateway_message = str(data.get("gateway_response") or "Approved")[:255]
        pay.paid_at = utcnow()
        mark_paid(pay.invoice, method="paystack", reference=pay.reference)
        return True
    if status == "success":  # paid, but not what we asked for
        pay.status, pay.gateway_message = "failed", "Amount or currency didn't match the invoice"
        audit.record("billing.payment_mismatch", summary=f"Paystack {pay.reference}: amount/currency mismatch",
                     entity=pay.invoice, organization_id=pay.organization_id)
        return False
    if status in ("failed", "abandoned", "reversed"):
        pay.status = "failed" if status != "abandoned" else "abandoned"
        pay.gateway_message = str(data.get("gateway_response") or status)[:255]
    return False


def verify(reference: str) -> Payment | None:
    """Ask Paystack about a reference (after the customer returns, or from the webhook)."""
    pay = db.session.scalar(select(Payment).where(Payment.reference == reference))
    if pay is None:
        return None
    res = _http("GET", f"/transaction/verify/{urllib.request.quote(reference, safe='')}")
    if res.get("status") and isinstance(res.get("data"), dict) and res["data"].get("reference") == reference:
        _apply_verified(pay, res["data"])
    return pay


def valid_signature(raw: bytes, signature: str) -> bool:
    key = current_app.config.get("PAYSTACK_SECRET_KEY") or ""
    if not key or not signature:
        return False
    expected = hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle_webhook(raw: bytes, signature: str) -> str:
    """Paystack event. Signature first; then we re-verify with the API rather than trusting the body."""
    from app.tenancy import tenant_scope

    if not valid_signature(raw, signature):
        return "bad-signature"
    try:
        event = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        return "bad-body"
    if event.get("event") != "charge.success":
        return "ignored"
    reference = str((event.get("data") or {}).get("reference") or "")
    with tenant_scope(platform=True):
        pay = verify(reference)
        db.session.commit()
    return "ok" if pay is not None else "unknown-reference"


# ---------------------------------------------------------------- renewals (daily job)
def run_renewals(today: date | None = None) -> dict:
    """Issue renewal invoices ahead of each period end, and mark lapsed subscriptions past due."""
    from app.tenancy import tenant_scope

    today = today or date.today()
    notice, grace = current_app.config["RENEWAL_NOTICE_DAYS"], current_app.config["BILLING_GRACE_DAYS"]
    issued = lapsed = 0
    with tenant_scope(platform=True):
        subs = db.session.scalars(select(Subscription).where(Subscription.status == Subscription.ACTIVE,
                                                             Subscription.current_period_end.is_not(None))).all()
        for sub in subs:
            end = sub.current_period_end.date()
            open_inv = db.session.scalar(select(Invoice).where(Invoice.organization_id == sub.organization_id,
                                                               Invoice.status == "open"))
            price = price_for(sub.plan, sub.billing_cycle)
            if end - timedelta(days=notice) <= today and open_inv is None and price is not None:
                inv = Invoice(organization_id=sub.organization_id, number=_next_number(), kind="renewal",
                              plan_id=sub.plan_id, billing_cycle=sub.billing_cycle, period_start=end,
                              period_end=cycle_end(end, sub.billing_cycle),
                              description=f"{sub.plan.name} plan, {'yearly' if sub.billing_cycle == 'annual' else 'monthly'} renewal",
                              subtotal=price, credit=Decimal(0), amount=price, currency=sub.plan.currency,
                              status="open", due_date=end)
                db.session.add(inv)
                db.session.flush()
                audit.record("billing.invoice_issued", summary=f"Issued renewal {inv.number}", entity=inv,
                             organization_id=sub.organization_id)
                _email_admins(sub.organization_id, f"Your ProjectSphere renewal invoice {inv.number}",
                              f"Your {sub.plan.name} plan renews on {end:%d %b %Y}. Invoice {inv.number} for "
                              f"{inv.amount:,} {inv.currency} is ready to pay.", "/app/billing")
                issued += 1
            if end + timedelta(days=grace) < today:
                sub.status = Subscription.PAST_DUE
                audit.record("subscription.past_due", summary="Subscription lapsed: renewal not paid", entity=sub,
                             organization_id=sub.organization_id)
                _email_admins(sub.organization_id, "Your ProjectSphere subscription is past due",
                              "Your renewal hasn't been paid, so new work is paused. Pay the open invoice to carry on; "
                              "nothing has been deleted.", "/app/billing")
                lapsed += 1
        db.session.commit()
    return {"issued": issued, "lapsed": lapsed}


# ---------------------------------------------------------------- reporting
def revenue_summary() -> dict:
    """Super Admin: money actually collected (paid invoices), by currency."""
    from app.tenancy import tenant_scope

    today = date.today()
    month_start, year_start = today.replace(day=1), today.replace(month=1, day=1)
    out = {"month": {}, "year": {}, "outstanding": {}, "overdue": 0}
    with tenant_scope(platform=True):
        for key, since in (("month", month_start), ("year", year_start)):
            rows = db.session.execute(select(Invoice.currency, func.sum(Invoice.amount)).where(
                Invoice.status == "paid", func.date(Invoice.paid_at) >= since).group_by(Invoice.currency)).all()
            out[key] = {c: a for c, a in rows}
        rows = db.session.execute(select(Invoice.currency, func.sum(Invoice.amount)).where(
            Invoice.status == "open").group_by(Invoice.currency)).all()
        out["outstanding"] = {c: a for c, a in rows}
        out["overdue"] = db.session.scalar(select(func.count(Invoice.id)).where(
            Invoice.status == "open", Invoice.due_date < today)) or 0
    return out
