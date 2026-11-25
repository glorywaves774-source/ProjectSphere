"""PDF reports built with ReportLab: the project M&E report and the monitoring visit report.

Everything written into a PDF is escaped first, because ReportLab paragraphs understand a little markup.
DejaVu Sans is bundled (app/assets/fonts) so names with accents and the cedi sign print on any server.
"""
import io
import os
from datetime import date, datetime
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor("#0B1530")
PURPLE = colors.HexColor("#6D5BF6")
GREY = colors.HexColor("#64748B")
LINE = colors.HexColor("#D9DEEA")
SOFT = colors.HexColor("#F4F6FB")
STATUS_COLORS = {"achieved": "#15803D", "on_track": "#2F7CF6", "at_risk": "#B45309", "off_track": "#B91C1C",
                 "no_data": "#64748B", "good": "#15803D", "concerns": "#B45309", "serious": "#B91C1C"}
W = 175 * mm  # usable width inside the page frame
CURRENCY = {"GHS": "GH₵", "USD": "$", "EUR": "€", "GBP": "£"}

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts")
_fonts_ready = False


def _fonts() -> tuple[str, str]:
    global _fonts_ready
    if not _fonts_ready:
        try:
            pdfmetrics.registerFont(TTFont("PS", os.path.join(_FONT_DIR, "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont("PS-Bold", os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")))
            pdfmetrics.registerFontFamily("PS", normal="PS", bold="PS-Bold", italic="PS", boldItalic="PS-Bold")
            _fonts_ready = True
        except Exception:  # fonts missing: fall back to the built-in Helvetica
            return "Helvetica", "Helvetica-Bold"
    return "PS", "PS-Bold"


def _styles():
    regular, bold = _fonts()
    base = dict(fontName=regular, fontSize=9, leading=12.5, textColor=NAVY, alignment=TA_LEFT)
    return {
        "title": ParagraphStyle("title", **{**base, "fontName": bold, "fontSize": 19, "leading": 24}),
        "subtitle": ParagraphStyle("subtitle", **{**base, "fontSize": 10.5, "textColor": GREY, "leading": 15}),
        "h2": ParagraphStyle("h2", **{**base, "fontName": bold, "fontSize": 12.5, "leading": 17, "spaceBefore": 12,
                                      "spaceAfter": 6, "textColor": PURPLE}),
        "h3": ParagraphStyle("h3", **{**base, "fontName": bold, "fontSize": 10, "leading": 14, "spaceBefore": 8,
                                      "spaceAfter": 3}),
        "body": ParagraphStyle("body", **base),
        "small": ParagraphStyle("small", **{**base, "fontSize": 8, "leading": 10.5, "textColor": GREY}),
        "cell": ParagraphStyle("cell", **{**base, "fontSize": 8.2, "leading": 10.5}),
        "cellb": ParagraphStyle("cellb", **{**base, "fontName": bold, "fontSize": 8.2, "leading": 10.5}),
        "head": ParagraphStyle("head", **{**base, "fontName": bold, "fontSize": 8, "leading": 10,
                                          "textColor": colors.white}),
        "bold": bold, "regular": regular,
    }


def esc(text) -> str:
    """Escape user text for a Paragraph and keep line breaks."""
    return escape("" if text is None else str(text)).replace("\n", "<br/>")


def num(v, percent=False) -> str:
    if v is None:
        return "—"
    v = Decimal(v)
    s = f"{v:,.0f}" if v == v.to_integral_value() else f"{v:,.2f}"
    return s + ("%" if percent else "")


def money(v, cur) -> str:
    if v is None:
        return "—"
    return f"{CURRENCY.get(cur, cur + ' ')}{Decimal(v):,.2f}"


def d(x) -> str:
    if x is None:
        return "—"
    return x.strftime("%d %b %Y") if isinstance(x, (date, datetime)) else str(x)


def _chip(st, key, label):
    color = STATUS_COLORS.get(key, "#64748B")
    return Paragraph(f'<font color="{color}"><b>{esc(label)}</b></font>', st["cell"])


def _table(st, header, rows, widths, zebra=True):
    data = [[Paragraph(esc(h), st["head"]) for h in header]] + rows
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if zebra:
        style += [("BACKGROUND", (0, i), (-1, i), SOFT) for i in range(2, len(data), 2)]
    t.setStyle(TableStyle(style))
    return t


def _facts(st, pairs, cols=2):
    """A grid of label / value pairs."""
    cells, row = [], []
    for label, value in pairs:
        row += [Paragraph(esc(label), st["small"]), Paragraph(esc(value), st["cellb"])]
        if len(row) == cols * 2:
            cells.append(row)
            row = []
    if row:
        cells.append(row + [""] * (cols * 2 - len(row)))
    width = W / cols
    t = Table(cells, colWidths=[width * 0.38, width * 0.62] * cols, hAlign="LEFT")
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _build(story, *, title: str, org_name: str, generated_by: str) -> bytes:
    st = _styles()
    stamp = datetime.now().strftime("%d %b %Y, %H:%M")

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(PURPLE)
        canvas.rect(0, A4[1] - 6, A4[0], 6, stroke=0, fill=1)
        canvas.setFont(st["bold"], 8)
        canvas.setFillColor(NAVY)
        canvas.drawString(15 * mm, A4[1] - 13 * mm, org_name[:80])
        canvas.setFont(st["regular"], 8)
        canvas.setFillColor(GREY)
        canvas.drawRightString(A4[0] - 15 * mm, A4[1] - 13 * mm, title[:80])
        canvas.setStrokeColor(LINE)
        canvas.line(15 * mm, 13 * mm, A4[0] - 15 * mm, 13 * mm)
        canvas.drawString(15 * mm, 8.5 * mm, f"Generated by {generated_by[:60]} on {stamp} · Laari ProjectSphere")
        canvas.drawRightString(A4[0] - 15 * mm, 8.5 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=20 * mm,
                            bottomMargin=18 * mm, title=title, author=org_name, creator="Laari ProjectSphere")
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buf.getvalue()


# ---------------------------------------------------------------- project M&E report
def project_report(*, project, org_name: str, summary: dict, framework: list, visits: list, evaluations: list,
                   finance: dict | None, generated_by: str) -> bytes:
    st = _styles()
    s = []
    s.append(Paragraph(esc(project.name), st["title"]))
    s.append(Paragraph(f"{esc(project.code)} · Monitoring and evaluation report · {d(date.today())}", st["subtitle"]))
    s.append(Spacer(1, 8))

    s.append(Paragraph("Project overview", st["h2"]))
    s.append(_facts(st, [
        ("Status", project.status_label), ("Health", project.health_label),
        ("Manager", project.manager.full_name if project.manager else "Not assigned"),
        ("Progress (tasks)", f"{project.progress}%"),
        ("Start", d(project.start_date)), ("Planned end", d(project.end_date)),
        ("Location", project.location or "—"), ("Funder", project.funder or "—"),
    ]))
    if project.objectives:
        s.append(Paragraph("Objectives", st["h3"]))
        s.append(Paragraph(esc(project.objectives), st["body"]))

    if finance is not None:
        s.append(Paragraph("Budget snapshot", st["h2"]))
        s.append(_facts(st, [
            ("Approved budget lines", money(finance["bac"], project.currency)),
            ("Spent", f"{money(finance['ac'], project.currency)} ({finance['used_pct']}%)"),
            ("Committed", money(finance["committed"], project.currency)),
            ("Remaining", money(finance["remaining"], project.currency)),
            ("Cost performance (CPI)", str(finance["cpi"]) if finance["cpi"] is not None else "—"),
            ("Schedule performance (SPI)", str(finance["spi"]) if finance["spi"] is not None else "—"),
        ]))

    c = summary["counts"]
    s.append(Paragraph("Indicator performance", st["h2"]))
    s.append(_facts(st, [
        ("Active indicators", str(summary["total"])),
        ("Average achievement", f"{summary['avg_achievement']}%" if summary["avg_achievement"] is not None else "—"),
        ("Achieved", str(c["achieved"])), ("On track", str(c["on_track"])),
        ("At risk", str(c["at_risk"])), ("Off track", str(c["off_track"])),
        ("No data yet", str(c["no_data"])), ("Data collection overdue", str(summary["overdue"])),
    ]))
    s.append(Paragraph("Achievement is how far each indicator has moved from its baseline towards its target, using "
                       "verified results only. Status compares it with the share of time between the baseline and "
                       "target dates that had passed when the result was measured: on track within 90% of that, "
                       "at risk within 60%, otherwise off track.", st["small"]))

    s.append(Paragraph("Results framework", st["h2"]))
    prog = summary["progress"]
    widths = [16 * mm, 56 * mm, 19 * mm, 19 * mm, 19 * mm, 19 * mm, 27 * mm]
    header = ["Code", "Indicator", "Baseline", "Target", "Actual", "Achieved", "Status"]

    def ind_rows(inds):
        out = []
        for i in inds:
            p = prog[i.id]
            sub = " · ".join(x for x in ((i.unit if not i.is_percent else ""), ("retired" if not i.is_active else "")) if x)
            out.append([
                Paragraph(esc(i.code), st["cell"]),
                Paragraph(esc(i.name) + (f'<br/><font color="#64748B" size="7">{esc(sub)}</font>' if sub else ""),
                          st["cell"]),
                Paragraph(num(i.baseline_value, i.is_percent), st["cell"]),
                Paragraph(num(i.target_value, i.is_percent), st["cell"]),
                Paragraph(num(p.actual, i.is_percent) if p.actual is not None else "—", st["cell"]),
                Paragraph(f"{p.achievement}%" if p.achievement is not None else "—", st["cell"]),
                _chip(st, p.status, p.status_label),
            ])
        return out

    def walk(item, depth=0):
        blocks = [Paragraph(f'<b>{esc(item.code)}</b>  <font color="#64748B">{esc(item.level_label)}</font> — '
                            f'{esc(item.statement)}', st["h3"])]
        if item.assumptions:
            blocks.append(Paragraph("Assumptions: " + esc(item.assumptions), st["small"]))
        if item.indicators:
            blocks.append(_table(st, header, ind_rows(item.indicators), widths))
        s.append(KeepTogether(blocks) if len(item.indicators) < 12 else blocks[0])
        if len(item.indicators) >= 12:
            s.extend(blocks[1:])
        for child in item.children:
            walk(child, depth + 1)

    for item in framework:
        walk(item)
    loose = [i for i, _ in summary["rows"] if i.result_id is None]
    if loose:
        s.append(Paragraph("Other indicators", st["h3"]))
        s.append(_table(st, header, ind_rows(loose), widths))
    if not framework and not loose:
        s.append(Paragraph("No results framework or indicators have been set up yet.", st["body"]))

    s.append(Paragraph("Field monitoring visits", st["h2"]))
    if visits:
        rows = [[Paragraph(esc(v.reference), st["cell"]), Paragraph(d(v.visit_date), st["cell"]),
                 Paragraph(esc(v.location), st["cell"]), _chip(st, v.rating, v.rating_label),
                 Paragraph(esc(_clip(v.findings, 320)), st["cell"])] for v in visits]
        s.append(_table(st, ["Visit", "Date", "Location", "Assessment", "Key findings"], rows,
                        [27 * mm, 21 * mm, 32 * mm, 25 * mm, 70 * mm]))
    else:
        s.append(Paragraph("No monitoring visits have been submitted.", st["body"]))

    s.append(Paragraph("Evaluations", st["h2"]))
    if evaluations:
        for ev in evaluations:
            block = [Paragraph(f"{esc(ev.title)}  <font color='#64748B'>· {esc(ev.kind_label)} · "
                               f"{esc(ev.status_label)}</font>", st["h3"])]
            meta = []
            if ev.evaluator:
                meta.append(("Evaluator", ev.evaluator + (" (external)" if ev.is_external else " (internal)")))
            if ev.start_date or ev.end_date:
                meta.append(("Period", f"{d(ev.start_date)} – {d(ev.end_date)}"))
            if ev.overall is not None:
                meta.append(("Overall rating", f"{ev.overall} / 4"))
            if meta:
                block.append(_facts(st, meta))
            rated = [(label, r) for _, label, r in ev.ratings if r]
            if rated:
                names = {4: "Highly satisfactory", 3: "Satisfactory", 2: "Partly satisfactory", 1: "Unsatisfactory"}
                block.append(_table(st, ["Criterion", "Rating"],
                                    [[Paragraph(esc(lbl), st["cell"]), Paragraph(f"{r} · {names[r]}", st["cell"])]
                                     for lbl, r in rated], [60 * mm, 60 * mm]))
            for label, text in (("Key findings", ev.findings), ("Recommendations", ev.recommendations),
                                ("Management response", ev.management_response)):
                if text:
                    block += [Paragraph(f"<b>{label}</b>", st["body"]), Paragraph(esc(text), st["body"]),
                              Spacer(1, 4)]
            s.append(KeepTogether(block[:3]))
            s.extend(block[3:])
    else:
        s.append(Paragraph("No evaluations have been recorded.", st["body"]))

    return _build(s, title=f"{project.code} · M&E report", org_name=org_name, generated_by=generated_by)


def _clip(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# ---------------------------------------------------------------- visit report
def visit_report(*, visit, org_name: str, generated_by: str) -> bytes:
    st = _styles()
    s = [Paragraph(f"Monitoring visit {esc(visit.reference)}", st["title"]),
         Paragraph(f"{esc(visit.project.code)} · {esc(visit.project.name)}", st["subtitle"]), Spacer(1, 8)]
    gps = f"{visit.latitude}, {visit.longitude}" if visit.has_gps else "Not recorded"
    s.append(_facts(st, [
        ("Date", d(visit.visit_date)), ("Location", visit.location), ("GPS", gps),
        ("Assessment", visit.rating_label), ("Visited by", visit.visited_by.full_name if visit.visited_by else "—"),
        ("Status", visit.status_label),
        ("Reviewed by", visit.reviewed_by.full_name if visit.reviewed_by else "—"),
        ("Reviewed on", d(visit.reviewed_at)),
    ]))
    for label, text in (("Purpose", visit.purpose), ("People met", visit.people_met), ("Findings", visit.findings),
                        ("Recommendations and follow-up", visit.recommendations), ("Reviewer's note", visit.review_note)):
        if text:
            s += [Paragraph(label, st["h2"]), Paragraph(esc(text), st["body"])]
    if visit.photos:
        s.append(Paragraph("Attachments", st["h2"]))
        s.append(Paragraph("<br/>".join(esc(p.title) for p in visit.photos), st["body"]))
        s.append(Paragraph("Photos and scans are kept in the project's document library.", st["small"]))
    return _build(s, title=f"Visit {visit.reference}", org_name=org_name, generated_by=generated_by)


# ---------------------------------------------------------------- purchase order
def purchase_order(*, pr, org, generated_by: str) -> bytes:
    st = _styles()
    q = pr.awarded_quotation
    v = q.vendor
    s = [Paragraph(f"Purchase order {esc(pr.po_number)}", st["title"]),
         Paragraph(f"{esc(pr.reference)} · {esc(pr.project.code)} · {esc(pr.project.name)}", st["subtitle"]),
         Spacer(1, 10)]
    buyer = "<br/>".join(esc(x) for x in (org.name, getattr(org, "address", "") or "", getattr(org, "email", "") or "",
                                          getattr(org, "phone", "") or "") if x)
    seller = "<br/>".join(esc(x) for x in (v.name, v.contact_name, v.address, v.email, v.phone,
                                           f"TIN {v.tax_id}" if v.tax_id else "") if x)
    parties = Table([[Paragraph("<b>Buyer</b>", st["small"]), Paragraph("<b>Supplier</b>", st["small"])],
                     [Paragraph(buyer, st["body"]), Paragraph(seller, st["body"])]],
                    colWidths=[W / 2, W / 2], hAlign="LEFT")
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    s.append(parties)
    s.append(Spacer(1, 8))
    s.append(_facts(st, [
        ("Order date", d(pr.ordered_at)), ("Vendor code", v.code),
        ("Deliver by", d(pr.needed_by) if pr.needed_by else (f"{q.delivery_days} days" if q.delivery_days else "—")),
        ("Deliver to", pr.delivery_location or "—"),
        ("Quotation", q.reference or "—"), ("Procurement method", pr.method_label),
        ("Budget line", f"{pr.budget_line.code} · {pr.budget_line.description}" if pr.budget_line else "—"),
        ("Approved by", pr.decided_by.full_name if pr.decided_by else "—"),
    ]))
    s.append(Paragraph("Items", st["h2"]))
    rows = [[Paragraph(str(n), st["cell"]),
             Paragraph(esc(i.description) + (f'<br/><font size="7" color="#64748B">{esc(i.specification)}</font>'
                                              if i.specification else ""), st["cell"]),
             Paragraph(num(i.quantity), st["cell"]), Paragraph(esc(i.unit or ""), st["cell"])]
            for n, i in enumerate(pr.items, start=1)]
    s.append(_table(st, ["#", "Description", "Quantity", "Unit"], rows, [10 * mm, 115 * mm, 25 * mm, 25 * mm]))
    s.append(Spacer(1, 8))
    total = Table([[Paragraph("<b>Order total</b>", st["body"]),
                    Paragraph(f"<b>{money(q.amount, q.currency)}</b>", st["body"])]],
                  colWidths=[W - 50 * mm, 50 * mm], hAlign="LEFT")
    total.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    s.append(total)
    s.append(Paragraph("The total is as quoted by the supplier. Please quote the purchase order number on your "
                       "delivery note and invoice. Goods and services are accepted only after inspection on receipt.",
                       st["small"]))
    s.append(Spacer(1, 26))
    sig = Table([[Paragraph("Authorised by", st["small"]), Paragraph("Accepted by supplier", st["small"])],
                 [Paragraph(f"{esc(pr.ordered_by.full_name if pr.ordered_by else '')}<br/>"
                            "_______________________________", st["body"]),
                  Paragraph("Name, signature and date<br/>_______________________________", st["body"])]],
                colWidths=[W / 2, W / 2], hAlign="LEFT")
    sig.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    s.append(sig)
    return _build(s, title=f"Purchase order {pr.po_number}", org_name=org.name, generated_by=generated_by)


# ---------------------------------------------------------------- subscription invoice
def invoice(*, inv, bank: str = "") -> bytes:
    st = _styles()
    org = inv.organization
    seller = "Kayron Global Tech Services<br/>Laari ProjectSphere"
    buyer = "<br/>".join(esc(x) for x in (org.name, org.address, org.email, org.phone) if x)
    status_color = {"paid": "#15803D", "open": "#B45309", "void": "#64748B"}[inv.status]
    s = [Paragraph(f"Invoice {esc(inv.number)}", st["title"]),
         Paragraph(f'<font color="{status_color}"><b>{esc(inv.status_label.upper())}</b></font>'
                   + (f" · paid {d(inv.paid_at)}" if inv.paid_at else f" · due {d(inv.due_date)}"), st["subtitle"]),
         Spacer(1, 10)]
    parties = Table([[Paragraph("<b>From</b>", st["small"]), Paragraph("<b>Bill to</b>", st["small"])],
                     [Paragraph(seller, st["body"]), Paragraph(buyer, st["body"])]],
                    colWidths=[W / 2, W / 2], hAlign="LEFT")
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    s += [parties, Spacer(1, 8)]
    s.append(_facts(st, [("Invoice date", d(inv.created_at)), ("Due date", d(inv.due_date)),
                         ("Type", inv.kind_label),
                         ("Service period", f"{d(inv.period_start)} – {d(inv.period_end)}" if inv.period_start else "—")]))
    rows = [[Paragraph(esc(inv.description), st["cell"]), Paragraph(money(inv.subtotal, inv.currency), st["cell"])]]
    if inv.credit:
        rows.append([Paragraph("Credit for unused time on your previous plan", st["cell"]),
                     Paragraph("−" + money(inv.credit, inv.currency), st["cell"])])
    s += [Paragraph("Details", st["h2"]), _table(st, ["Description", "Amount"], rows, [W - 45 * mm, 45 * mm])]
    total = Table([[Paragraph("<b>Total</b>", st["body"]), Paragraph(f"<b>{money(inv.amount, inv.currency)}</b>",
                                                                       st["body"])]],
                  colWidths=[W - 45 * mm, 45 * mm], hAlign="LEFT")
    total.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("TOPPADDING", (0, 0), (-1, -1), 6),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    s += [Spacer(1, 4), total]
    if inv.status == "paid":
        s.append(Paragraph(f"Paid by {esc(inv.payment_method)}" + (f", reference {esc(inv.payment_reference)}"
                                                                    if inv.payment_reference else "") + ".", st["small"]))
    elif inv.status == "open":
        s.append(Paragraph("How to pay", st["h2"]))
        s.append(Paragraph("Pay online by card or mobile money from Subscription in ProjectSphere."
                           + (f"<br/>Or by bank transfer, quoting {esc(inv.number)}:<br/>{esc(bank)}" if bank else ""),
                           st["body"]))
    return _build(s, title=f"Invoice {inv.number}", org_name="Laari ProjectSphere", generated_by=org.name)
