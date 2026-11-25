"""Excel (.xlsx) financial reports built with openpyxl."""
import io
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

NAVY = "0B1530"
PURPLE = "6D5BF6"
MONEY = '#,##0.00;[Red]-#,##0.00'
THIN = Side(style="thin", color="D9DEEA")


def safe(value):
    """Text that starts like a formula is stored as plain text (no formula injection)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _title(ws, title: str, subtitle_rows: list[tuple[str, str]]):
    ws["A1"] = title
    ws["A1"].font = Font(size=16, bold=True, color=NAVY)
    r = 2
    for label, value in subtitle_rows:
        ws.cell(row=r, column=1, value=label).font = Font(bold=True, color="64748B")
        ws.cell(row=r, column=2, value=safe(value))
        r += 1
    return r + 1


def _header(ws, row: int, headers: list[str]):
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 28


def _widths(ws, widths: list[int]):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _save(wb) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def budget_vs_actual(project, org, f: dict, generated_by: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget vs actual"
    start = _title(ws, "Budget vs actual", [
        ("Organization", org.name), ("Project", f"{project.code} · {project.name}"),
        ("Currency", project.currency), ("Budget status", project.budget_status.capitalize()),
        ("Progress", f"{project.progress}%"),
        ("Generated", f"{datetime.now():%d %b %Y %H:%M} by {generated_by}"),
    ])
    headers = ["Code", "Category", "Description", "Qty", "Unit", "Unit cost", "Budget", "Spent", "Committed",
               "Remaining", "Used %"]
    _header(ws, start, headers)
    r = start + 1
    first = r
    for row in f["rows"]:
        l = row["line"]
        values = [l.code, l.category_label, l.description, float(l.quantity), l.unit, float(l.unit_cost),
                  float(row["budget"]), float(row["spent"]), float(row["committed"]), None, None]
        for i, v in enumerate(values, start=1):
            c = ws.cell(row=r, column=i, value=safe(v))
            c.border = Border(bottom=THIN)
        ws.cell(row=r, column=10, value=f"=G{r}-H{r}-I{r}")
        ws.cell(row=r, column=11, value=f"=IF(G{r}=0,0,H{r}/G{r})")
        for col in (6, 7, 8, 9, 10):
            ws.cell(row=r, column=col).number_format = MONEY
        ws.cell(row=r, column=11).number_format = "0%"
        if row["over"]:
            for col in range(1, 12):
                ws.cell(row=r, column=col).fill = PatternFill("solid", fgColor="FDE8E8")
        r += 1
    last = r - 1
    ws.cell(row=r, column=3, value="Total").font = Font(bold=True)
    for col, letter in ((7, "G"), (8, "H"), (9, "I"), (10, "J")):
        c = ws.cell(row=r, column=col, value=f"=SUM({letter}{first}:{letter}{last})" if last >= first else 0)
        c.font, c.number_format = Font(bold=True), MONEY
    c = ws.cell(row=r, column=11, value=f"=IF(G{r}=0,0,H{r}/G{r})")
    c.font, c.number_format = Font(bold=True), "0%"
    _widths(ws, [10, 22, 40, 8, 10, 14, 16, 16, 16, 16, 9])
    ws.freeze_panes = ws.cell(row=start + 1, column=1)

    ev = wb.create_sheet("Performance")
    _title(ev, "Cost & schedule performance", [("Project", f"{project.code} · {project.name}")])
    data = [("Budget at completion (BAC)", f["bac"]), ("Actual cost (AC)", f["ac"]),
            ("Earned value (EV = progress × BAC)", f["ev"]), ("Planned value (PV = time elapsed × BAC)", f["pv"]),
            ("Cost performance index (CPI = EV / AC)", f["cpi"]), ("Schedule performance index (SPI = EV / PV)", f["spi"]),
            ("Committed (awaiting approval)", f["committed"]), ("Remaining", f["remaining"])]
    for i, (label, value) in enumerate(data, start=5):
        ev.cell(row=i, column=1, value=label)
        c = ev.cell(row=i, column=2, value=float(value) if value is not None else "n/a")
        if "index" not in label and value is not None:
            c.number_format = MONEY
    ev.cell(row=14, column=1, value="CPI or SPI below 1.00 means over cost or behind schedule.").font = Font(italic=True, color="64748B")
    _widths(ev, [48, 18])
    return _save(wb)


def expense_register(expenses, org, base_rates: dict, generated_by: str, title_suffix: str = "") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Expenses"
    start = _title(ws, "Expense register" + title_suffix, [
        ("Organization", org.name), ("Base currency", org.currency),
        ("Generated", f"{datetime.now():%d %b %Y %H:%M} by {generated_by}"),
    ])
    headers = ["Reference", "Date", "Project", "Budget line", "Description", "Payee", "Invoice", "Amount",
               "Currency", "Rate", "Amount (project currency)", "Project currency", "Status", "Requested by",
               "Decided by", "Paid on", "Payment"]
    _header(ws, start, headers)
    r = start + 1
    for e in expenses:
        values = [e.reference, e.spent_on, e.project.code, f"{e.budget_line.code} {e.budget_line.description}",
                  e.description, e.payee, e.invoice_ref, float(e.amount), e.currency, float(e.exchange_rate),
                  float(e.amount_project), e.project.currency, e.status_label,
                  e.requested_by.full_name if e.requested_by else "", e.decided_by.full_name if e.decided_by else "",
                  e.paid_at, e.payment_label]
        for i, v in enumerate(values, start=1):
            c = ws.cell(row=r, column=i, value=safe(v))
            if isinstance(v, date):
                c.number_format = "dd mmm yyyy"
        ws.cell(row=r, column=8).number_format = MONEY
        ws.cell(row=r, column=11).number_format = MONEY
        ws.cell(row=r, column=10).number_format = "0.000000"
        r += 1
    _widths(ws, [16, 12, 15, 30, 36, 22, 14, 14, 9, 11, 18, 10, 18, 20, 20, 12, 14])
    ws.freeze_panes = ws.cell(row=start + 1, column=1)
    ws.auto_filter.ref = f"A{start}:Q{max(r - 1, start)}"
    return _save(wb)


# ---------------------------------------------------------------- Stage 9: M&E
STATUS_FILL = {"achieved": "DCFCE7", "on_track": "DBEAFE", "at_risk": "FEF3C7", "off_track": "FDE8E8"}


def indicator_tracking(projects_summaries: list[tuple], org, generated_by: str, visits=(), title_suffix="") -> bytes:
    """Indicator performance tracking table (ITT): one row per indicator, every reported value, and visits."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Indicators"
    start = _title(ws, "Indicator tracking table" + title_suffix, [
        ("Organization", org.name), ("Generated", f"{datetime.now():%d %b %Y %H:%M} by {generated_by}"),
        ("Achievement", "Share of the way from baseline to target, verified results only"),
    ])
    headers = ["Project", "Result", "Code", "Indicator", "Unit", "Measure", "Direction", "Frequency", "Baseline",
               "Baseline date", "Target", "Target date", "Latest actual", "As of", "Endline", "Achieved %",
               "Time elapsed %", "Status", "Next data due", "Responsible", "Means of verification", "Active"]
    _header(ws, start, headers)
    r = start + 1
    for project, summary in projects_summaries:
        for ind, p in summary["rows"]:
            values = [f"{project.code} · {project.name}", ind.result.code if ind.result else "", ind.code, ind.name,
                      ind.unit, "Percentage" if ind.is_percent else "Number",
                      "Higher is better" if ind.direction == "increase" else "Lower is better", ind.frequency_label,
                      float(ind.baseline_value), ind.baseline_date, float(ind.target_value), ind.target_date,
                      float(p.actual) if p.actual is not None else None, p.actual_date,
                      float(ind.endline_value) if ind.endline_value is not None else None,
                      p.achievement / 100 if p.achievement is not None else None, p.expected / 100,
                      p.status_label, p.next_due, ind.responsible.full_name if ind.responsible else "",
                      ind.data_source, "Yes" if ind.is_active else "Retired"]
            for i, v in enumerate(values, start=1):
                c = ws.cell(row=r, column=i, value=safe(v))
                c.border = Border(bottom=THIN)
                if isinstance(v, date):
                    c.number_format = "dd mmm yyyy"
            ws.cell(row=r, column=16).number_format = "0.0%"
            ws.cell(row=r, column=17).number_format = "0%"
            if p.status in STATUS_FILL:
                ws.cell(row=r, column=18).fill = PatternFill("solid", fgColor=STATUS_FILL[p.status])
            r += 1
    _widths(ws, [30, 8, 9, 44, 14, 12, 15, 14, 11, 13, 11, 13, 12, 13, 11, 11, 12, 13, 14, 20, 34, 8])
    ws.freeze_panes = ws.cell(row=start + 1, column=5)
    ws.auto_filter.ref = f"A{start}:{get_column_letter(len(headers))}{max(r - 1, start)}"

    vs = wb.create_sheet("Reported values")
    vstart = _title(vs, "Reported values", [("Organization", org.name)])
    _header(vs, vstart, ["Project", "Indicator", "Period ending", "Value", "Status", "Source", "Notes",
                         "Reported by", "Verified by", "Verified on", "Review note"])
    r = vstart + 1
    for project, summary in projects_summaries:
        for ind, _ in summary["rows"]:
            for v in sorted(ind.values, key=lambda x: x.period_end):
                row = [project.code, f"{ind.code} · {ind.name}", v.period_end, float(v.value), v.status_label, v.source,
                       v.notes, v.reported_by.full_name if v.reported_by else "",
                       v.verified_by.full_name if v.verified_by else "",
                       v.verified_at.replace(tzinfo=None) if v.verified_at else None, v.review_note]
                for i, val in enumerate(row, start=1):
                    c = vs.cell(row=r, column=i, value=safe(val))
                    if isinstance(val, (date, datetime)):
                        c.number_format = "dd mmm yyyy"
                r += 1
    _widths(vs, [14, 44, 13, 12, 22, 24, 40, 20, 20, 13, 30])
    vs.freeze_panes = vs.cell(row=vstart + 1, column=1)

    if visits:
        mv = wb.create_sheet("Monitoring visits")
        mstart = _title(mv, "Monitoring visits", [("Organization", org.name)])
        _header(mv, mstart, ["Reference", "Project", "Date", "Location", "Latitude", "Longitude", "Assessment",
                             "Status", "Visited by", "Findings", "Recommendations"])
        r = mstart + 1
        for v in visits:
            row = [v.reference, v.project.code, v.visit_date, v.location,
                   float(v.latitude) if v.latitude is not None else None,
                   float(v.longitude) if v.longitude is not None else None, v.rating_label, v.status_label,
                   v.visited_by.full_name if v.visited_by else "", v.findings, v.recommendations]
            for i, val in enumerate(row, start=1):
                c = mv.cell(row=r, column=i, value=safe(val))
                if isinstance(val, date):
                    c.number_format = "dd mmm yyyy"
                c.alignment = Alignment(vertical="top", wrap_text=i in (10, 11))
            r += 1
        _widths(mv, [15, 14, 13, 26, 11, 11, 16, 12, 20, 60, 50])
    return _save(wb)


# ---------------------------------------------------------------- Stage 10: risk & procurement
LEVEL_FILL = {"critical": "FDE8E8", "high": "FFEDD5", "medium": "FEF3C7", "low": "DCFCE7"}


def _sheet_rows(ws, start, rows, date_cols=(), money_cols=(), fills=None):
    r = start
    for i, row in enumerate(rows):
        for c, v in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=safe(v))
            cell.border = Border(bottom=THIN)
            cell.alignment = Alignment(vertical="top", wrap_text=isinstance(v, str) and len(v) > 40)
            if c in date_cols and v is not None:
                cell.number_format = "dd mmm yyyy"
            if c in money_cols:
                cell.number_format = MONEY
        if fills and fills[i]:
            for c in fills[i][1]:
                ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor=fills[i][0])
        r += 1
    return r


def risk_register(risks, issues, org, generated_by: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Risk register"
    start = _title(ws, "Risk register", [("Organization", org.name),
                                         ("Generated", f"{datetime.now():%d %b %Y %H:%M} by {generated_by}"),
                                         ("Score", "Likelihood × impact (1–5 each): 1–4 low, 5–9 medium, "
                                                   "10–15 high, 16–25 critical")])
    headers = ["Project", "Code", "Risk", "Cause and consequence", "Category", "Likelihood", "Impact", "Score",
               "Level", "Response", "Mitigation", "Contingency", "Owner", "Next review", "Status", "Last updated"]
    _header(ws, start, headers)
    rows = [[r.project.code, r.code, r.title, r.description, r.category_label, r.likelihood, r.impact, r.score,
             r.level_label, r.response_label, r.mitigation, r.contingency, r.owner.full_name if r.owner else "",
             r.review_date, r.status_label, r.updated_at.replace(tzinfo=None) if r.updated_at else None] for r in risks]
    _sheet_rows(ws, start + 1, rows, date_cols=(14, 16), fills=[(LEVEL_FILL[r.level], (8, 9)) for r in risks])
    _widths(ws, [14, 9, 36, 40, 16, 10, 9, 8, 10, 12, 40, 34, 18, 13, 18, 13])
    ws.freeze_panes = ws.cell(row=start + 1, column=4)
    ws.auto_filter.ref = f"A{start}:{get_column_letter(len(headers))}{max(start + len(rows), start)}"

    il = wb.create_sheet("Issue log")
    s2 = _title(il, "Issue log", [("Organization", org.name)])
    _header(il, s2, ["Project", "Code", "Issue", "Details", "Priority", "Status", "Assigned to", "Raised by",
                     "Resolve by", "From risk", "Resolution", "Resolved"])
    rows = [[i.project.code, i.code, i.title, i.description, i.priority_label, i.status_label,
             i.assignee.full_name if i.assignee else "", i.raised_by.full_name if i.raised_by else "", i.due_date,
             i.risk.code if i.risk else "", i.resolution, i.resolved_at.replace(tzinfo=None) if i.resolved_at else None]
            for i in issues]
    _sheet_rows(il, s2 + 1, rows, date_cols=(9, 12))
    _widths(il, [14, 9, 36, 40, 10, 13, 18, 18, 12, 10, 40, 12])
    il.freeze_panes = il.cell(row=s2 + 1, column=4)
    return _save(wb)


def procurement_register(requests, org, generated_by: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Procurement"
    start = _title(ws, "Procurement register", [("Organization", org.name),
                                                ("Generated", f"{datetime.now():%d %b %Y %H:%M} by {generated_by}")])
    headers = ["Reference", "Project", "Title", "Budget line", "Method", "Status", "Estimated (project currency)",
               "Currency", "Requested by", "Submitted", "Approved / rejected by", "Decided", "Quotations", "Vendor",
               "PO number", "Awarded (project currency)", "Saving", "Received", "Vendor rating", "Expense"]
    _header(ws, start, headers)
    rows = []
    for p in requests:
        q = p.awarded_quotation
        rows.append([p.reference, p.project.code, p.title, p.budget_line.code if p.budget_line else "", p.method_label,
                     p.status_label, float(p.estimated_total), p.project.currency,
                     p.requested_by.full_name if p.requested_by else "",
                     p.submitted_at.replace(tzinfo=None) if p.submitted_at else None,
                     p.decided_by.full_name if p.decided_by else "",
                     p.decided_at.replace(tzinfo=None) if p.decided_at else None, len(p.quotations),
                     q.vendor.name if q else "", p.po_number,
                     float(p.awarded_amount_project) if p.awarded_amount_project is not None else None,
                     float(p.estimated_total - p.awarded_amount_project) if p.awarded_amount_project is not None else None,
                     p.received_on, p.vendor_rating, p.expense.reference if p.expense else ""])
    _sheet_rows(ws, start + 1, rows, date_cols=(10, 12, 18), money_cols=(7, 16, 17))
    _widths(ws, [15, 14, 34, 10, 20, 16, 16, 9, 18, 12, 20, 12, 10, 24, 14, 16, 12, 12, 9, 15])
    ws.freeze_panes = ws.cell(row=start + 1, column=3)
    ws.auto_filter.ref = f"A{start}:{get_column_letter(len(headers))}{max(start + len(rows), start)}"
    return _save(wb)
