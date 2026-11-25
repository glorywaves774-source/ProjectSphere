"""Stage 10: vendor register and the procurement cycle.

draft → submitted → approved (or rejected) → ordered (vendor awarded, purchase order issued) → received.
Controls built in:
  * the method (single quotation / request for quotations / open tender) follows the estimated value;
  * the person who raised a request can't approve it;
  * approval checks the budget line's remaining funds, counting other approved purchases not yet received;
  * awards go only to approved vendors, need the method's number of quotes, and choosing anything but the
    cheapest valid quote needs a written justification;
  * on receipt the purchase becomes an approved expense on the budget line, ready for finance to pay.
"""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import func, select

from app.extensions import db
from app.models import (METHOD_THRESHOLDS, SPENT_STATUSES, VENDOR_CATEGORIES, VENDOR_STATUSES, BudgetLine, Document,
                        Expense, ProcurementItem, ProcurementRequest, Project, Quotation, User, Vendor, utcnow)

from . import audit
from . import finance as fin
from . import projects as project_svc
from .notify import notify, users_with

TWO = Decimal("0.01")
OPEN_COMMITMENTS = ("approved", "ordered")  # approved purchases whose expense doesn't exist yet


class ProcurementError(ValueError):
    pass


def money(v, field="Amount") -> Decimal:
    try:
        d = Decimal(str(v)).quantize(TWO, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ProcurementError(f"{field} must be a number.") from None
    if not d.is_finite() or d < 0 or d >= Decimal("1e13"):
        raise ProcurementError(f"{field} isn't valid.")
    return d


# ---------------------------------------------------------------- access
def can_view(user: User) -> bool:
    return user.can("procurement.view")


def is_officer(user: User) -> bool:
    return user.can("procurement.manage")


def can_request(user: User, project: Project) -> bool:
    return can_view(user) and project.is_open and (
        is_officer(user) or project_svc.can_edit(user, project) or project.manager_id == user.id)


def can_approve(user: User) -> bool:
    return user.can("budget.approve")


def can_edit_request(user: User, pr: ProcurementRequest) -> bool:
    return pr.status in ("draft", "rejected") and (pr.requested_by_id == user.id or is_officer(user))


# ---------------------------------------------------------------- vendors
def save_vendor(actor: User, vendor: Vendor | None, **f) -> Vendor:
    if not is_officer(actor):
        raise ProcurementError("Only procurement officers can manage vendors.")
    name = (f.get("name") or "").strip()
    if len(name) < 2:
        raise ProcurementError("Enter the vendor's name.")
    if f.get("category") not in dict(VENDOR_CATEGORIES):
        raise ProcurementError("Choose a category.")
    clash = db.session.scalar(select(Vendor).where(func.lower(Vendor.name) == name.lower()))
    if clash is not None and clash is not vendor:
        raise ProcurementError(f"{clash.code} already has that name.")
    creating = vendor is None
    if creating:
        seq = (db.session.scalar(select(func.max(Vendor.seq))) or 0) + 1
        vendor = Vendor(organization_id=actor.organization_id, seq=seq, code=f"VEN-{seq:04d}", created_by_id=actor.id,
                        status="pending")
        db.session.add(vendor)
    vendor.name, vendor.category = name[:200], f["category"]
    for k, n in (("contact_name", 120), ("email", 255), ("phone", 40), ("tax_id", 40), ("registration_no", 60)):
        setattr(vendor, k, (f.get(k) or "").strip()[:n])
    vendor.address, vendor.notes = (f.get("address") or "").strip(), (f.get("notes") or "").strip()
    db.session.flush()
    audit.record("procurement.vendor_saved", summary=f"{'Registered' if creating else 'Updated'} vendor {vendor.code} "
                                                     f"“{vendor.name}”", entity=vendor)
    return vendor


def set_vendor_status(actor: User, vendor: Vendor, status: str, note: str = "") -> None:
    if not is_officer(actor):
        raise ProcurementError("Only procurement officers can change a vendor's status.")
    if status not in dict(VENDOR_STATUSES) or status == "pending":
        raise ProcurementError("Choose a status.")
    note = (note or "").strip()
    if status in ("suspended", "blacklisted") and len(note) < 5:
        raise ProcurementError("Record the reason for suspending or blacklisting this vendor.")
    if status == "approved" and vendor.created_by_id == actor.id and len(users_with("procurement.manage")) > 1:
        raise ProcurementError("Another procurement officer must approve a vendor you registered.")
    if status == "approved" and not vendor.tax_id:
        raise ProcurementError("Record the vendor's tax ID (TIN) before approving them.")
    vendor.status, vendor.status_note = status, note
    if status == "approved":
        vendor.approved_by_id, vendor.approved_at = actor.id, utcnow()
    audit.record("procurement.vendor_" + status, summary=f"Vendor {vendor.code} {vendor.status_label.lower()}"
                                                         + (f": {note}" if note else ""), entity=vendor)


def vendor_stats(vendor: Vendor) -> dict:
    awarded = db.session.scalars(select(ProcurementRequest).join(
        Quotation, Quotation.id == ProcurementRequest.awarded_quotation_id).where(Quotation.vendor_id == vendor.id)
    ).all()
    ratings = [p.vendor_rating for p in awarded if p.vendor_rating]
    return {"awards": awarded, "count": len(awarded),
            "value": sum((p.awarded_amount_project or 0 for p in awarded), Decimal(0)),
            "rating": round(sum(ratings) / len(ratings), 1) if ratings else None,
            "quotes": len(vendor.quotations)}


# ---------------------------------------------------------------- requests
def method_for(amount_project: Decimal, project: Project) -> str:
    """Pick the method from the estimate converted to the organization's base currency."""
    from app.models import Organization

    org = db.session.get(Organization, project.organization_id)
    base = fin.convert(amount_project, project.currency, org.currency, org) if project.currency != org.currency else amount_project
    base = amount_project if base is None else base
    for limit, method in METHOD_THRESHOLDS:
        if base < limit:
            return method
    return "tender"


def _items(rows) -> list[dict]:
    out = []
    for r in rows:
        desc = (r.get("description") or "").strip()
        if not desc and not r.get("quantity") and not r.get("unit_cost"):
            continue  # blank row
        if len(desc) < 2:
            raise ProcurementError("Each item needs a description.")
        qty, cost = money(r.get("quantity") or 0, "Quantity"), money(r.get("unit_cost") or 0, "Unit cost")
        if qty <= 0:
            raise ProcurementError(f"Enter a quantity for “{desc}”.")
        if cost <= 0:
            raise ProcurementError(f"Enter an estimated unit cost for “{desc}”.")
        out.append({"description": desc[:255], "specification": (r.get("specification") or "").strip()[:2000],
                    "quantity": qty, "unit": (r.get("unit") or "").strip()[:30], "unit_cost": cost})
    if not out:
        raise ProcurementError("Add at least one item.")
    if len(out) > 50:
        raise ProcurementError("A request can have at most 50 items.")
    return out


def save_request(actor: User, project: Project, pr: ProcurementRequest | None, *, title: str, justification: str,
                 needed_by: date | None, delivery_location: str, budget_line_id: int | None, items: list[dict]
                 ) -> ProcurementRequest:
    if pr is None and not can_request(actor, project):
        raise ProcurementError("You can't raise purchase requests for this project.")
    if pr is not None and not can_edit_request(actor, pr):
        raise ProcurementError("This request can't be edited now.")
    if not project.is_open:
        raise ProcurementError("This project is closed.")
    title = (title or "").strip()
    if len(title) < 3:
        raise ProcurementError("Give the request a title.")
    if needed_by and needed_by < date.today() and pr is None:
        raise ProcurementError("The 'needed by' date can't be in the past.")
    line = None
    if budget_line_id:
        line = db.session.get(BudgetLine, budget_line_id)
        if line is None or line.project_id != project.id:
            raise ProcurementError("Choose a budget line from this project.")
    rows = _items(items)
    if pr is None:
        seq = (db.session.scalar(select(func.max(ProcurementRequest.seq))) or 0) + 1
        pr = ProcurementRequest(organization_id=project.organization_id, project_id=project.id, seq=seq,
                                reference=f"PR-{date.today().year}-{seq:04d}", requested_by_id=actor.id)
        db.session.add(pr)
        creating = True
    else:
        creating = False
        for it in list(pr.items):
            db.session.delete(it)
        pr.items = []
    pr.project_id, pr.project = project.id, project
    pr.title, pr.justification = title[:200], (justification or "").strip()
    pr.needed_by, pr.delivery_location = needed_by, (delivery_location or "").strip()[:255]
    pr.budget_line_id = line.id if line else None
    for r in rows:
        pr.items.append(ProcurementItem(organization_id=project.organization_id, **r))
    db.session.flush()
    pr.method = method_for(pr.estimated_total, project)
    pr.status = "draft"
    audit.record("procurement.request_saved", summary=f"{'Raised' if creating else 'Updated'} {pr.reference} "
                 f"“{pr.title}” ({pr.estimated_total:,} {project.currency})", entity=pr)
    return pr


def line_position(pr: ProcurementRequest) -> dict | None:
    """What's left on the request's budget line once spending and other open purchases are counted."""
    line = pr.budget_line
    if line is None:
        return None
    spent = db.session.scalar(select(func.coalesce(func.sum(Expense.amount_project), 0)).where(
        Expense.budget_line_id == line.id, Expense.status.in_(SPENT_STATUSES | {"submitted"}))) or Decimal(0)
    others = db.session.scalars(select(ProcurementRequest).where(
        ProcurementRequest.budget_line_id == line.id, ProcurementRequest.status.in_(OPEN_COMMITMENTS),
        ProcurementRequest.id != pr.id)).all()
    committed = sum(((o.awarded_amount_project or o.estimated_total) for o in others), Decimal(0))
    remaining = (line.amount - Decimal(spent) - committed).quantize(TWO)
    need = pr.awarded_amount_project or pr.estimated_total
    return {"line": line, "budget": line.amount, "spent": Decimal(spent).quantize(TWO), "committed": committed,
            "remaining": remaining, "need": need, "short": max(Decimal(0), need - remaining)}


def submit(actor: User, pr: ProcurementRequest) -> None:
    if not can_edit_request(actor, pr):
        raise ProcurementError("This request can't be submitted now.")
    if not pr.items:
        raise ProcurementError("Add at least one item.")
    if len(pr.justification.strip()) < 10:
        raise ProcurementError("Explain why the purchase is needed before submitting.")
    pr.status, pr.submitted_at = "submitted", utcnow()
    pr.decided_by_id = pr.decided_at = None
    pr.decision_note = ""
    pos = line_position(pr)
    pr.over_budget = bool(pos and pos["short"] > 0)
    notify(users_with("budget.approve", exclude={pr.requested_by_id}), "approval",
           f"Purchase request {pr.reference} ({pr.estimated_total:,} {pr.project.currency}) is waiting for approval"
           + (" — over budget" if pr.over_budget else ""), f"/app/procurement/{pr.id}", actor=actor)
    audit.record("procurement.request_submitted", summary=f"Submitted {pr.reference} for approval", entity=pr)


def decide(actor: User, pr: ProcurementRequest, *, approve: bool, note: str = "", allow_over: bool = False) -> None:
    if not can_approve(actor):
        raise ProcurementError("You don't have permission to approve purchases.")
    if pr.status != "submitted":
        raise ProcurementError("This request isn't waiting for approval.")
    if pr.requested_by_id == actor.id:
        raise ProcurementError("You can't approve your own request. Another approver must do it.")
    note = (note or "").strip()
    if approve:
        if pr.project.budget_status != "approved":
            raise ProcurementError("The project's budget must be approved before purchases can be approved.")
        if pr.budget_line is None:
            raise ProcurementError("Link the request to a budget line before it can be approved.")
        pos = line_position(pr)
        if pos["short"] > 0:
            if not allow_over:
                raise ProcurementError(f"{pos['line'].code} has {pos['remaining']:,} {pr.project.currency} left, "
                                       f"{pos['short']:,} short. Tick the over-budget box to approve anyway.")
            if len(note) < 5:
                raise ProcurementError("Explain why you're approving a purchase over budget.")
            pr.over_budget = True
        pr.status = "approved"
    else:
        if len(note) < 3:
            raise ProcurementError("Say why the request is rejected.")
        pr.status = "rejected"
    pr.decided_by_id, pr.decided_at, pr.decision_note = actor.id, utcnow(), note
    recipients = {pr.requested_by_id} | ({u.id for u in users_with("procurement.manage")} if approve else set())
    notify(recipients - {None}, "approval", f"{pr.reference} was {'approved' if approve else 'rejected'} by "
                                            f"{actor.full_name}" + ("" if approve else f": {note}"),
           f"/app/procurement/{pr.id}", actor=actor)
    audit.record("procurement.request_" + ("approved" if approve else "rejected"),
                 summary=f"{'Approved' if approve else 'Rejected'} {pr.reference}"
                 + (" over budget" if approve and pr.over_budget else ""), entity=pr)


def cancel(actor: User, pr: ProcurementRequest, note: str) -> None:
    if pr.status in ("received", "cancelled"):
        raise ProcurementError("This request can't be cancelled.")
    if not (pr.requested_by_id == actor.id or is_officer(actor)):
        raise ProcurementError("Only the requester or a procurement officer can cancel this request.")
    if pr.status == "ordered" and not is_officer(actor):
        raise ProcurementError("The order has been placed; ask procurement to cancel it with the vendor.")
    if len((note or "").strip()) < 3:
        raise ProcurementError("Say why the request is cancelled.")
    pr.status, pr.cancel_note = "cancelled", note.strip()
    audit.record("procurement.request_cancelled", summary=f"Cancelled {pr.reference}: {pr.cancel_note}", entity=pr)
    notify({pr.requested_by_id} - {None}, "approval", f"{pr.reference} was cancelled", f"/app/procurement/{pr.id}",
           actor=actor)


def delete(actor: User, pr: ProcurementRequest) -> None:
    if pr.status != "draft" or pr.requested_by_id != actor.id:
        raise ProcurementError("Only the requester can delete a draft request.")
    audit.record("procurement.request_deleted", summary=f"Deleted draft {pr.reference}", entity=pr.project)
    db.session.delete(pr)


# ---------------------------------------------------------------- quotations & award
def add_quotation(actor: User, pr: ProcurementRequest, *, vendor_id: int, amount, currency: str,
                  exchange_rate=None, delivery_days=None, valid_until: date | None = None, reference: str = "",
                  notes: str = "", document_id: int | None = None) -> Quotation:
    if not is_officer(actor):
        raise ProcurementError("Only procurement officers record quotations.")
    if pr.status != "approved":
        raise ProcurementError("Quotations are recorded after the request is approved and before the award.")
    vendor = db.session.get(Vendor, vendor_id or 0)
    if vendor is None:
        raise ProcurementError("Choose a vendor.")
    if vendor.status == "blacklisted":
        raise ProcurementError(f"{vendor.name} is blacklisted.")
    if any(q.vendor_id == vendor.id for q in pr.quotations):
        raise ProcurementError(f"{vendor.name} has already quoted on this request.")
    amt = money(amount)
    if amt <= 0:
        raise ProcurementError("The quoted amount must be more than zero.")
    currency = (currency or pr.project.currency).upper()[:3]
    if currency == pr.project.currency:
        rate = Decimal(1)
    elif exchange_rate:
        rate = Decimal(str(exchange_rate))
        if rate <= 0:
            raise ProcurementError("The exchange rate must be more than zero.")
    else:
        rate = fin.rate_between(currency, pr.project.currency, actor.organization)
        if rate is None:
            raise ProcurementError(f"There's no exchange rate for {currency}. Enter one, or ask finance to set it.")
    if valid_until and valid_until < date.today():
        raise ProcurementError("This quotation has already expired.")
    if document_id:
        d = db.session.get(Document, document_id)
        if d is None or d.project_id != pr.project_id:
            raise ProcurementError("Choose a document from this project.")
    q = Quotation(organization_id=pr.organization_id, request_id=pr.id, vendor_id=vendor.id, amount=amt,
                  currency=currency, exchange_rate=rate, amount_project=(amt * rate).quantize(TWO, ROUND_HALF_UP),
                  delivery_days=int(delivery_days) if delivery_days else None, valid_until=valid_until,
                  reference=(reference or "").strip()[:60], notes=(notes or "").strip(), document_id=document_id or None,
                  recorded_by_id=actor.id)
    db.session.add(q)
    db.session.flush()
    audit.record("procurement.quote_added", summary=f"Quotation from {vendor.name} on {pr.reference}: "
                                                    f"{amt:,} {currency}", entity=pr)
    return q


def remove_quotation(actor: User, q: Quotation) -> None:
    if not is_officer(actor) or q.request.status != "approved":
        raise ProcurementError("Quotations can only be removed before the award.")
    db.session.delete(q)


def valid_quotes(pr: ProcurementRequest) -> list[Quotation]:
    today = date.today()
    return [q for q in pr.quotations if q.vendor.status == "approved" and (q.valid_until is None or q.valid_until >= today)]


def award(actor: User, pr: ProcurementRequest, quotation_id: int, justification: str = "") -> None:
    """Choose the winning quotation and issue the purchase order."""
    if not is_officer(actor):
        raise ProcurementError("Only procurement officers can award and issue purchase orders.")
    if pr.status != "approved":
        raise ProcurementError("Only approved requests can be awarded.")
    q = db.session.get(Quotation, quotation_id or 0)
    if q is None or q.request_id != pr.id:
        raise ProcurementError("Choose one of this request's quotations.")
    if q.vendor.status != "approved":
        raise ProcurementError(f"{q.vendor.name} isn't an approved vendor ({q.vendor.status_label.lower()}).")
    if q.valid_until and q.valid_until < date.today():
        raise ProcurementError("That quotation has expired.")
    justification = (justification or "").strip()
    valid = valid_quotes(pr)
    reasons = []
    if len(valid) < pr.min_quotes:
        reasons.append(f"{pr.method_label.lower()} needs {pr.min_quotes} valid quotations from approved vendors "
                       f"and there {'is' if len(valid) == 1 else 'are'} {len(valid)}")
    cheapest = min(valid, key=lambda x: x.amount_project) if valid else q
    if q.amount_project > cheapest.amount_project:
        reasons.append(f"{cheapest.vendor.name} quoted less")
    if reasons and len(justification) < 15:
        raise ProcurementError("Write a justification for this award: " + "; ".join(reasons) + ".")
    pr.awarded_quotation_id, pr.award_justification = q.id, justification
    pr.awarded_amount_project = q.amount_project
    pos = line_position(pr)
    if pos and pos["short"] > 0 and not pr.over_budget:
        raise ProcurementError(f"The award is {pos['short']:,} {pr.project.currency} more than the budget line has "
                               "left. Ask an approver to re-approve it over budget, or choose another quotation.")
    pr.po_seq = (db.session.scalar(select(func.max(ProcurementRequest.po_seq))) or 0) + 1
    pr.po_number = f"PO-{date.today().year}-{pr.po_seq:04d}"
    pr.status, pr.ordered_by_id, pr.ordered_at = "ordered", actor.id, utcnow()
    audit.record("procurement.ordered", summary=f"Awarded {pr.reference} to {q.vendor.name} "
                 f"({q.amount:,} {q.currency}); issued {pr.po_number}", entity=pr)
    notify({pr.requested_by_id} - {None}, "approval",
           f"{pr.po_number} issued to {q.vendor.name} for {pr.reference}", f"/app/procurement/{pr.id}", actor=actor)


def receive(actor: User, pr: ProcurementRequest, *, received_on: date, note: str, rating, invoice_ref: str = "") -> None:
    """Goods or services received: rate the vendor and record the spend on the budget line."""
    if not (is_officer(actor) or pr.requested_by_id == actor.id):
        raise ProcurementError("Only the requester or a procurement officer can confirm receipt.")
    if pr.status != "ordered":
        raise ProcurementError("Only ordered purchases can be received.")
    if received_on is None or received_on > date.today():
        raise ProcurementError("Enter the date it was received (not in the future).")
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise ProcurementError("Rate the vendor from 1 to 5.") from None
    if not 1 <= rating <= 5:
        raise ProcurementError("Rate the vendor from 1 to 5.")
    q = pr.awarded_quotation
    pr.status, pr.received_on, pr.received_by_id = "received", received_on, actor.id
    pr.receipt_note, pr.vendor_rating = (note or "").strip(), rating
    if pr.budget_line_id:
        seq, ref = fin._next_reference()
        exp = Expense(organization_id=pr.organization_id, project_id=pr.project_id, budget_line_id=pr.budget_line_id,
                      seq=seq, reference=ref, spent_on=received_on,
                      description=f"{pr.po_number}: {pr.title}"[:255], payee=q.vendor.name[:160],
                      invoice_ref=(invoice_ref or q.reference or pr.po_number).strip()[:60], amount=q.amount,
                      currency=q.currency, exchange_rate=q.exchange_rate, amount_project=q.amount_project,
                      status="approved", requested_by_id=pr.requested_by_id, submitted_at=pr.submitted_at,
                      decided_by_id=pr.decided_by_id, decided_at=pr.decided_at,
                      decision_note=f"Approved through procurement {pr.reference}", over_budget=pr.over_budget)
        db.session.add(exp)
        db.session.flush()
        pr.expense_id = exp.id
        notify(users_with("budget.manage"), "approval",
               f"{exp.reference} from {pr.po_number} ({q.amount:,} {q.currency} to {q.vendor.name}) is ready to pay",
               f"/app/expenses/{exp.id}", actor=actor)
    audit.record("procurement.received", summary=f"Received {pr.po_number} from {q.vendor.name}; vendor rated "
                                                 f"{rating}/5", entity=pr)
    notify({pr.requested_by_id, pr.ordered_by_id} - {None}, "approval", f"{pr.po_number} has been received",
           f"/app/procurement/{pr.id}", actor=actor)


# ---------------------------------------------------------------- overview
def overview() -> dict:
    reqs = db.session.scalars(select(ProcurementRequest)).all()
    by = {}
    for r in reqs:
        by[r.status] = by.get(r.status, 0) + 1
    year = date.today().year
    ordered = [r for r in reqs if r.status in ("ordered", "received") and r.ordered_at and r.ordered_at.year == year]
    savings = sum(((r.estimated_total - r.awarded_amount_project) for r in ordered
                   if r.awarded_amount_project is not None), Decimal(0))
    late = [r for r in reqs if r.status in ("submitted", "approved", "ordered") and r.needed_by
            and r.needed_by < date.today()]
    return {"by_status": by, "waiting": by.get("submitted", 0), "to_award": by.get("approved", 0),
            "open_orders": by.get("ordered", 0), "ordered_year": len(ordered),
            "savings": savings.quantize(TWO), "late": len(late),
            "vendors": db.session.scalar(select(func.count(Vendor.id)).where(Vendor.status == "approved")) or 0,
            "vendors_pending": db.session.scalar(select(func.count(Vendor.id)).where(Vendor.status == "pending")) or 0}
