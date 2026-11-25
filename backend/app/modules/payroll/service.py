"""Staff, attendance and salaries.

Attendance: mark the exceptions. A day with no mark counts as present (most small businesses
only note absences); ``absent`` is unpaid, ``half_day`` half paid, ``leave`` and ``holiday`` paid.

A salary run for a month has one line per employee employed that month:

* monthly pay: salary × paid days ÷ days in the month (days before joining or after leaving
  are not paid);
* daily pay: wage × paid days;
* commission: the employee's % of their own sales (sales invoices where they are the
  salesperson, before GST, net of credit notes);
* plus bonus, minus deductions (advance recovery) typed by the owner.

Posting puts it in the books (Dr Salaries & Wages gross, Cr Advances for recoveries, Cr Salaries
Payable net); paying clears Salaries Payable from cash or bank.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.payroll.models import Attendance, AttendanceStatus, Employee, PayType, RunStatus, SalaryLine, SalaryRun

ZERO = Decimal(0)
PAID = {AttendanceStatus.PRESENT: Decimal(1), AttendanceStatus.LEAVE: Decimal(1), AttendanceStatus.HOLIDAY: Decimal(1),
        AttendanceStatus.HALF_DAY: Decimal("0.5"), AttendanceStatus.ABSENT: ZERO}
EMPLOYEE_FIELDS = ("branch_id", "code", "name", "designation", "phone", "email", "joining_date", "leaving_date", "pay_type",
                   "monthly_salary", "daily_wage", "commission_percent", "user_id", "pan", "bank_account", "ifsc",
                   "is_active", "notes", "pf_enabled", "uan", "basic_percent", "pf_wage_cap", "esi_enabled", "esi_number", "pt_state", "tds_enabled",
)


def _q2(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def month_bounds(month: date) -> tuple[date, date]:
    first = month.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


# ---------------------------------------------------------------------------- employees


def save_employee(db: Session, access: BusinessAccess, data: dict[str, Any], e: Employee | None = None) -> Employee:
    access.require("payroll.manage")
    code = data.get("code", e.code if e else None)
    dup = select(Employee.id).where(Employee.business_id == access.business.id, Employee.code == code)
    if e is not None:
        dup = dup.where(Employee.id != e.id)
    if db.scalar(dup):
        raise ValidationFailed(f"Employee code {code} is already used")
    if e is None:
        e = Employee(business_id=access.business.id)
        db.add(e)
    for k, v in data.items():
        if k in EMPLOYEE_FIELDS:
            setattr(e, k, v)
    if e.leaving_date and e.leaving_date < e.joining_date:
        raise ValidationFailed("Leaving date is before the joining date")
    if e.pay_type == PayType.MONTHLY and not e.monthly_salary:
        raise ValidationFailed("Give the monthly salary")
    if e.pay_type == PayType.DAILY and not e.daily_wage:
        raise ValidationFailed("Give the daily wage")
    if e.user_id is not None:
        from app.modules.access.models import Membership

        if not db.scalar(select(Membership.id).where(Membership.business_id == access.business.id,
                                                     Membership.user_id == e.user_id)):
            raise ValidationFailed("That user is not a member of this business")
    db.flush()
    audit.record(db, action="employee.saved", entity_type="employee", entity_id=e.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"code": e.code, "name": e.name})
    return e


def get_employee(db: Session, access: BusinessAccess, eid: uuid.UUID) -> Employee:
    e = db.get(Employee, eid)
    if e is None or e.business_id != access.business.id:
        raise NotFound("Employee not found")
    return e


# ---------------------------------------------------------------------------- attendance


def mark(db: Session, access: BusinessAccess, day: date, entries: list[dict[str, Any]]) -> int:
    if not (access.can("attendance.mark") or access.can("payroll.manage")):
        access.require("attendance.mark")
    n = 0
    for it in entries:
        e = get_employee(db, access, it["employee_id"])
        if day < e.joining_date or (e.leaving_date and day > e.leaving_date):
            raise ValidationFailed(f"{e.name} was not employed on {day:%d %b %Y}")
        row = db.scalar(select(Attendance).where(Attendance.employee_id == e.id, Attendance.day == day))
        status = it.get("status")
        if status is None:  # clear the mark
            if row is not None:
                db.delete(row)
            continue
        if row is None:
            row = Attendance(business_id=access.business.id, employee_id=e.id, day=day)
            db.add(row)
        row.status, row.marked_by_id = AttendanceStatus(status), access.user.id
        n += 1
    db.flush()
    return n


def month_attendance(db: Session, access: BusinessAccess, month: date) -> dict[str, Any]:
    first, last = month_bounds(month)
    rows = db.scalars(select(Attendance).where(Attendance.business_id == access.business.id,
                                               Attendance.day >= first, Attendance.day <= last))
    marks: dict[str, dict[str, str]] = {}
    for r in rows:
        marks.setdefault(str(r.employee_id), {})[r.day.isoformat()] = r.status.value
    return {"month": first.isoformat(), "days": (last - first).days + 1, "marks": marks}


# ---------------------------------------------------------------------------- salary


def _employed_days(e: Employee, first: date, last: date) -> tuple[date, date] | None:
    start, end = max(first, e.joining_date), min(last, e.leaving_date or last)
    return (start, end) if start <= end else None


def _paid_days(db: Session, e: Employee, start: date, end: date) -> Decimal:
    marks = {r.day: r.status for r in db.scalars(select(Attendance).where(
        Attendance.employee_id == e.id, Attendance.day >= start, Attendance.day <= end))}
    days = (end - start).days + 1
    return sum((PAID[marks[start + timedelta(days=i)]] if start + timedelta(days=i) in marks else Decimal(1)
                for i in range(days)), ZERO)


def _sales(db: Session, business_id: uuid.UUID, user_id: uuid.UUID | None, first: date, last: date) -> Decimal:
    if user_id is None:
        return ZERO
    from app.modules.billing.doctypes import DocType
    from app.modules.billing.models import DocStatus, Document

    def total(t: DocType) -> Decimal:
        return Decimal(db.scalar(select(func.coalesce(func.sum(Document.taxable_total), 0)).where(
            Document.business_id == business_id, Document.salesperson_id == user_id, Document.doc_type == t,
            Document.status == DocStatus.FINAL, Document.doc_date >= first, Document.doc_date <= last)) or 0)
    return total(DocType.SALES_INVOICE) - total(DocType.CREDIT_NOTE)


def _statutory(line: SalaryLine, e: Employee | None, month: date) -> None:
    """PF on the salary (not bonus or commission), ESI / PT / TDS on the month's gross."""
    from app.modules.payroll import statutory as st

    gross = line.base + line.commission + line.bonus
    zero = {"pf_wage": ZERO, "pf_employee": ZERO, "pf_employer": ZERO, "eps_employer": ZERO,
            "esi_employee": ZERO, "esi_employer": ZERO}
    vals: dict[str, Decimal] = dict(zero)
    if e is not None and e.pf_enabled:
        vals.update(st.pf(line.base, e.basic_percent, e.pf_wage_cap))
    if e is not None and e.esi_enabled:
        rate = e.monthly_salary if e.pay_type == PayType.MONTHLY else e.daily_wage * 26
        vals.update(st.esi(gross, rate))
    for k, v in vals.items():
        setattr(line, k, v)
    line.pt = st.professional_tax(e.pt_state, gross, month) if e is not None else ZERO
    line.tds = st.monthly_tds(gross) if e is not None and e.tds_enabled else ZERO


def _fill(line: SalaryLine) -> None:
    line.net = _q2(line.base + line.commission + line.bonus - line.deductions
                   - line.pf_employee - line.esi_employee - line.pt - line.tds)
    if line.net < 0:
        raise ValidationFailed(f"{line.employee_name}: deductions are more than the pay")


def _totals(run: SalaryRun) -> None:
    run.gross = sum((ln.base + ln.commission + ln.bonus for ln in run.lines), ZERO)
    run.deductions = sum((ln.deductions + ln.pf_employee + ln.esi_employee + ln.pt + ln.tds for ln in run.lines), ZERO)
    run.net = sum((ln.net for ln in run.lines), ZERO)


def create_run(db: Session, access: BusinessAccess, month: date) -> SalaryRun:
    access.require("payroll.manage")
    first, last = month_bounds(month)
    if db.scalar(select(SalaryRun.id).where(SalaryRun.business_id == access.business.id, SalaryRun.month == first)):
        raise Conflict(f"Salaries for {first:%B %Y} already exist; open that run")
    run = SalaryRun(business_id=access.business.id, month=first, status=RunStatus.DRAFT, created_by_id=access.user.id)
    days_in_month = (last - first).days + 1
    for e in db.scalars(select(Employee).where(Employee.business_id == access.business.id).order_by(Employee.name)):
        span = _employed_days(e, first, last)
        if span is None or (not e.is_active and not e.leaving_date):
            continue
        payable = Decimal((span[1] - span[0]).days + 1)
        paid = _paid_days(db, e, *span)
        base = (e.monthly_salary * paid / days_in_month) if e.pay_type == PayType.MONTHLY else e.daily_wage * paid
        sales = _sales(db, access.business.id, e.user_id, first, last)
        ln = SalaryLine(employee_id=e.id, employee_name=e.name, pay_type=e.pay_type, days_in_month=days_in_month,
                        payable_days=payable, paid_days=paid, base=_q2(base), sales=sales,
                        commission=_q2(sales * e.commission_percent / 100), bonus=ZERO, deductions=ZERO)
        _statutory(ln, e, first)
        _fill(ln)
        run.lines.append(ln)
    if not run.lines:
        raise ValidationFailed("No employees were employed in that month")
    _totals(run)
    db.add(run)
    db.flush()
    audit.record(db, action="salary_run.created", entity_type="salary_run", entity_id=run.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"month": first, "net": run.net})
    return run


def get_run(db: Session, access: BusinessAccess, rid: uuid.UUID) -> SalaryRun:
    access.require("payroll.manage")
    run = db.get(SalaryRun, rid)
    if run is None or run.business_id != access.business.id:
        raise NotFound("Salary run not found")
    return run


def update_line(db: Session, access: BusinessAccess, run: SalaryRun, employee_id: uuid.UUID, data: dict[str, Any]) -> SalaryRun:
    if run.status != RunStatus.DRAFT:
        raise Conflict("Posted salaries cannot be changed")
    ln = next((x for x in run.lines if x.employee_id == employee_id), None)
    if ln is None:
        raise NotFound("That employee is not in this run")
    for k in ("bonus", "deductions", "note"):
        if k in data:
            setattr(ln, k, data[k] if k == "note" else Decimal(data[k] or 0))
    _statutory(ln, db.get(Employee, ln.employee_id), run.month)
    for k in ("pt", "tds"):  # a manual figure overrides the calculated one
        if data.get(k) is not None:
            setattr(ln, k, Decimal(data[k]))
    _fill(ln)
    _totals(run)
    db.flush()
    return run


def delete_run(db: Session, access: BusinessAccess, run: SalaryRun) -> None:
    if run.status != RunStatus.DRAFT:
        raise Conflict("Posted salaries cannot be deleted")
    db.delete(run)
    db.flush()


def post_run(db: Session, access: BusinessAccess, run: SalaryRun) -> SalaryRun:
    from app.modules.accounting.chart import Accounts
    from app.modules.accounting.ledger import Line, post
    from app.modules.accounting.models import JournalSource

    if run.status != RunStatus.DRAFT:
        raise Conflict("These salaries are already posted")
    _, last = month_bounds(run.month)
    acc = Accounts(db, access.business.id)
    total = lambda k: sum((getattr(ln, k) for ln in run.lines), ZERO)  # noqa: E731
    employer = total("pf_employer") + total("esi_employer")
    lines = [Line(acc["salaries"], debit=run.gross, description=f"Salaries {run.month:%B %Y}")]
    if employer:
        lines.append(Line(acc["employer_contributions"], debit=employer, description="Employer PF and ESI"))
    if total("deductions"):
        lines.append(Line(acc["advances"], credit=total("deductions"), description="Advances recovered"))
    for key, amount in (("pf_payable", total("pf_employee") + total("pf_employer")),
                        ("esi_payable", total("esi_employee") + total("esi_employer")),
                        ("pt_payable", total("pt")), ("tds_payable", total("tds"))):
        if amount:
            lines.append(Line(acc[key], credit=amount))
    lines.append(Line(acc["salaries_payable"], credit=run.net))
    post(db, access.business.id, entry_date=last, source_type=JournalSource.PAYROLL, source_id=run.id,
         source_ref=f"SAL/{run.month:%Y-%m}", lines=lines, created_by=access.user.id,
         narration=f"Salaries for {run.month:%B %Y}")
    run.status, run.posted_at = RunStatus.POSTED, datetime.now(UTC)
    db.flush()
    audit.record(db, action="salary_run.posted", entity_type="salary_run", entity_id=run.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"gross": run.gross, "net": run.net})
    return run


def pay_run(db: Session, access: BusinessAccess, run: SalaryRun, *, paid_from_account_id: uuid.UUID, on: date) -> SalaryRun:
    from app.modules.accounting.accounts import get_account
    from app.modules.accounting.chart import Accounts
    from app.modules.accounting.ledger import Line, post
    from app.modules.accounting.models import AccountSubtype, JournalSource

    if run.status != RunStatus.POSTED:
        raise Conflict("Post the salaries before paying them" if run.status == RunStatus.DRAFT else "Already paid")
    src = get_account(db, access, paid_from_account_id)
    if src.subtype not in (AccountSubtype.CASH, AccountSubtype.BANK):
        raise ValidationFailed("Pay from a cash or bank account")
    acc = Accounts(db, access.business.id)
    post(db, access.business.id, entry_date=on, source_type=JournalSource.PAYROLL, source_id=run.id, source_event="paid",
         source_ref=f"SAL/{run.month:%Y-%m}", lines=[Line(acc["salaries_payable"], debit=run.net), Line(src, credit=run.net)],
         created_by=access.user.id, narration=f"Salaries paid for {run.month:%B %Y}")
    run.status, run.paid_at, run.paid_from_account_id = RunStatus.PAID, datetime.now(UTC), src.id
    db.flush()
    audit.record(db, action="salary_run.paid", entity_type="salary_run", entity_id=run.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"net": run.net, "from": src.code})
    return run


# ---------------------------------------------------------------------------- statutory returns and payslips


def statutory(db: Session, access: BusinessAccess, run: SalaryRun) -> dict[str, Any]:
    """What to deposit for the month: PF (with the ECR file), ESI, professional tax and TDS on salary.
    PF and ESI are due by the 15th of the next month."""
    emps = {e.id: e for e in db.scalars(select(Employee).where(Employee.id.in_([ln.employee_id for ln in run.lines])))}
    pf_rows, esi_rows, pt_rows, tds_rows = [], [], [], []
    for ln in run.lines:
        e = emps.get(ln.employee_id)
        gross = ln.base + ln.commission + ln.bonus
        ncp = max(ZERO, ln.payable_days - ln.paid_days)
        if ln.pf_employee:
            eps_wage = min(ln.pf_wage, Decimal(15000))
            pf_rows.append({"uan": e.uan if e else None, "name": ln.employee_name, "gross": _q2(gross), "epf_wage": ln.pf_wage,
                            "eps_wage": eps_wage, "edli_wage": eps_wage, "employee": ln.pf_employee, "eps": ln.eps_employer,
                            "epf_employer": ln.pf_employer - ln.eps_employer, "ncp_days": ncp})
        if ln.esi_employee or ln.esi_employer:
            esi_rows.append({"ip_number": e.esi_number if e else None, "name": ln.employee_name, "days": ln.paid_days,
                             "wages": _q2(gross), "employee": ln.esi_employee, "employer": ln.esi_employer})
        if ln.pt:
            pt_rows.append({"name": ln.employee_name, "state": e.pt_state if e else None, "gross": _q2(gross), "pt": ln.pt})
        if ln.tds:
            tds_rows.append({"name": ln.employee_name, "pan": e.pan if e else None, "gross": _q2(gross), "tds": ln.tds})
    _, last = month_bounds(run.month)
    due = date(last.year + (last.month == 12), last.month % 12 + 1, 15)
    s = lambda rows, k: sum((r[k] for r in rows), ZERO)  # noqa: E731
    return {"month": run.month, "pf": {"rows": pf_rows, "employee": s(pf_rows, "employee"), "eps": s(pf_rows, "eps"),
                                       "epf_employer": s(pf_rows, "epf_employer"), "due_date": due},
            "esi": {"rows": esi_rows, "employee": s(esi_rows, "employee"), "employer": s(esi_rows, "employer"), "due_date": due},
            "pt": {"rows": pt_rows, "total": s(pt_rows, "pt")},
            "tds": {"rows": tds_rows, "total": s(tds_rows, "tds"), "due_date": date(due.year, due.month, 7) if run.month.month != 3 else date(last.year, 4, 30)}}


def ecr_text(db: Session, access: BusinessAccess, run: SalaryRun) -> str:
    """EPFO ECR 2.0 text file (fields separated by #~#) for the PF unified portal."""
    rows = statutory(db, access, run)["pf"]["rows"]
    out = []
    for r in rows:
        out.append("#~#".join(str(x) for x in (
            r["uan"] or "", r["name"].upper(), int(r["gross"]), int(r["epf_wage"]), int(r["eps_wage"]), int(r["edli_wage"]),
            int(r["employee"]), int(r["eps"]), int(r["epf_employer"]), int(r["ncp_days"]), 0)))
    return "\n".join(out) + ("\n" if out else "")


def payslip_html(db: Session, access: BusinessAccess, run: SalaryRun, employee_id: uuid.UUID) -> str:
    import html

    from app.modules.gst.words import rupees_in_words

    ln = next((x for x in run.lines if x.employee_id == employee_id), None)
    if ln is None:
        raise NotFound("That employee is not in this run")
    e = db.get(Employee, employee_id)
    b = access.business
    esc = html.escape
    earn = [("Salary / wages", ln.base), ("Commission", ln.commission), ("Bonus", ln.bonus)]
    ded = [("Provident fund", ln.pf_employee), ("ESI", ln.esi_employee), ("Professional tax", ln.pt), ("Income tax (TDS)", ln.tds),
           ("Advance / other", ln.deductions)]
    gross = ln.base + ln.commission + ln.bonus
    rows = ""
    for i in range(max(len(earn), len(ded))):
        a = earn[i] if i < len(earn) else ("", None)
        d = ded[i] if i < len(ded) else ("", None)
        rows += (f"<tr><td>{esc(a[0])}</td><td class=n>{'' if a[1] is None else f'{a[1]:,.2f}'}</td>"
                 f"<td>{esc(d[0])}</td><td class=n>{'' if d[1] is None else f'{d[1]:,.2f}'}</td></tr>")
    tot_ded = gross - ln.net
    info = [("Employee", ln.employee_name), ("Code", e.code if e else ""), ("Designation", (e.designation or "") if e else ""),
            ("PAN", (e.pan or "") if e else ""), ("UAN", (e.uan or "") if e else ""), ("ESI no.", (e.esi_number or "") if e else ""),
            ("Paid days", f"{ln.paid_days.normalize():f} of {ln.days_in_month}"), ("Bank a/c", (e.bank_account or "") if e else "")]
    grid = "".join(f"<div><span>{esc(k)}</span><b>{esc(str(v))}</b></div>" for k, v in info)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Payslip {esc(ln.employee_name)} {run.month:%b %Y}</title>
<style>body{{font:10pt Arial,"Noto Sans",sans-serif;margin:14mm;color:#111}}h2{{margin:0}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:6px 16px;margin:12px 0}}
.grid span{{display:block;font-size:8pt;color:#666}}table{{width:100%;border-collapse:collapse}}td,th{{border:1px solid #bbb;padding:5px}}th{{background:#f2f2f2;text-align:left}}.n{{text-align:right}}</style></head><body>
<h2>{esc(b.legal_name or b.name)}</h2><p>Payslip for {run.month:%B %Y}</p><div class=grid>{grid}</div>
<table><tr><th>Earnings</th><th class=n>₹</th><th>Deductions</th><th class=n>₹</th></tr>{rows}
<tr><th>Gross</th><th class=n>{gross:,.2f}</th><th>Total deductions</th><th class=n>{tot_ded:,.2f}</th></tr></table>
<p><b>Net pay: ₹{ln.net:,.2f}</b> ({esc(rupees_in_words(ln.net))})</p>
<p style="font-size:8pt;color:#666">Employer contributions (not deducted): PF ₹{ln.pf_employer:,.2f}, ESI ₹{ln.esi_employer:,.2f}. This is a computer-generated payslip.</p>
</body></html>"""
