"""Staff, attendance and salaries."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select

from app.api.deps import DB, require, require_any
from app.core.clock import local_today
from app.modules.access.service import BusinessAccess
from app.modules.payroll import service
from app.modules.payroll.models import AttendanceStatus, Employee, PayType, RunStatus, SalaryRun

router = APIRouter(prefix="/businesses/{business_id}/payroll", tags=["payroll"])
Manage = Annotated[BusinessAccess, Depends(require("payroll.manage"))]
Mark = Annotated[BusinessAccess, Depends(require_any("attendance.mark", "payroll.manage"))]
Money = Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=4)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]


class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=30)]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    branch_id: uuid.UUID | None = None
    designation: Text | None = None
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)] | None = None
    joining_date: date
    leaving_date: date | None = None
    pay_type: PayType = PayType.MONTHLY
    monthly_salary: Money = Decimal(0)
    daily_wage: Money = Decimal(0)
    commission_percent: Decimal = Field(Decimal(0), ge=0, le=100, max_digits=8, decimal_places=4)
    user_id: uuid.UUID | None = None
    pan: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$")] | None = None
    bank_account: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{6,34}$")] | None = None
    ifsc: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Z]{4}0[A-Z0-9]{6}$")] | None = None
    is_active: bool = True
    notes: str | None = Field(None, max_length=2000)
    pf_enabled: bool = False
    uan: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{12}$")] | None = None
    basic_percent: Decimal = Field(Decimal(100), gt=0, le=100, decimal_places=2, description="Share of salary that is basic + DA (PF wage)")
    pf_wage_cap: bool = True
    esi_enabled: bool = False
    esi_number: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{10,17}$")] | None = None
    pt_state: Annotated[str, StringConstraints(pattern=r"^[0-9]{2}$")] | None = None
    tds_enabled: bool = False


class EmployeeOut(EmployeeIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    updated_at: datetime


class MarkIn(BaseModel):
    day: date
    entries: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    employee_id: uuid.UUID
    employee_name: str
    pay_type: PayType
    days_in_month: int
    payable_days: Decimal
    paid_days: Decimal
    base: Decimal
    sales: Decimal
    commission: Decimal
    bonus: Decimal
    deductions: Decimal
    pf_employee: Decimal = Decimal(0)
    pf_employer: Decimal = Decimal(0)
    esi_employee: Decimal = Decimal(0)
    esi_employer: Decimal = Decimal(0)
    pt: Decimal = Decimal(0)
    tds: Decimal = Decimal(0)
    net: Decimal
    note: str | None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    month: date
    status: RunStatus
    gross: Decimal
    deductions: Decimal
    net: Decimal
    posted_at: datetime | None
    paid_at: datetime | None
    lines: list[LineOut]


class LineUpdateIn(BaseModel):
    bonus: Money | None = None
    deductions: Money | None = None
    pt: Money | None = Field(None, description="Override the calculated professional tax")
    tds: Money | None = Field(None, description="Override the calculated TDS (e.g. after investment proofs)")
    note: Annotated[str, StringConstraints(max_length=300)] | None = None


class PayIn(BaseModel):
    paid_from_account_id: uuid.UUID
    paid_on: date | None = None


@router.get("/employees", response_model=list[EmployeeOut])
def list_employees(access: Mark, db: DB) -> list[Any]:
    rows = list(db.scalars(select(Employee).where(Employee.business_id == access.business.id)
                           .order_by(Employee.is_active.desc(), Employee.name)))
    if not access.can("payroll.manage"):  # attendance markers see names, not pay
        return [EmployeeOut.model_validate(e).model_copy(update={"monthly_salary": Decimal(0), "daily_wage": Decimal(0),
                "commission_percent": Decimal(0), "pan": None, "bank_account": None, "ifsc": None}) for e in rows]
    return rows


@router.post("/employees", response_model=EmployeeOut, status_code=status.HTTP_201_CREATED)
def create_employee(body: EmployeeIn, access: Manage, db: DB) -> Employee:
    e = service.save_employee(db, access, body.model_dump())
    db.commit()
    return e


@router.put("/employees/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: uuid.UUID, body: EmployeeIn, access: Manage, db: DB) -> Employee:
    e = service.save_employee(db, access, body.model_dump(), service.get_employee(db, access, employee_id))
    db.commit()
    return e


@router.get("/attendance")
def attendance(access: Mark, db: DB, month: date | None = None) -> dict[str, Any]:
    """Marks for a month: {employee_id: {date: status}}; unmarked days count as present."""
    return service.month_attendance(db, access, month or local_today())


@router.put("/attendance")
def mark_attendance(body: MarkIn, access: Mark, db: DB) -> dict[str, int]:
    for e in body.entries:
        if e.get("status") is not None and e["status"] not in {s.value for s in AttendanceStatus}:
            from app.core.errors import ValidationFailed

            raise ValidationFailed(f"Unknown attendance status {e['status']}")
        e["employee_id"] = uuid.UUID(str(e["employee_id"]))
    n = service.mark(db, access, body.day, body.entries)
    db.commit()
    return {"marked": n}


@router.get("/runs", response_model=list[RunOut])
def list_runs(access: Manage, db: DB) -> list[SalaryRun]:
    return list(db.scalars(select(SalaryRun).where(SalaryRun.business_id == access.business.id)
                           .order_by(SalaryRun.month.desc())))


@router.post("/runs", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def create_run(body: dict[str, date], access: Manage, db: DB) -> SalaryRun:
    run = service.create_run(db, access, body["month"])
    db.commit()
    return run


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, access: Manage, db: DB) -> SalaryRun:
    return service.get_run(db, access, run_id)


@router.patch("/runs/{run_id}/lines/{employee_id}", response_model=RunOut)
def update_line(run_id: uuid.UUID, employee_id: uuid.UUID, body: LineUpdateIn, access: Manage, db: DB) -> SalaryRun:
    run = service.update_line(db, access, service.get_run(db, access, run_id), employee_id, body.model_dump(exclude_unset=True))
    db.commit()
    return run


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(run_id: uuid.UUID, access: Manage, db: DB) -> None:
    service.delete_run(db, access, service.get_run(db, access, run_id))
    db.commit()


@router.post("/runs/{run_id}/post", response_model=RunOut)
def post_run(run_id: uuid.UUID, access: Manage, db: DB) -> SalaryRun:
    run = service.post_run(db, access, service.get_run(db, access, run_id))
    db.commit()
    return run


@router.post("/runs/{run_id}/pay", response_model=RunOut)
def pay_run(run_id: uuid.UUID, body: PayIn, access: Manage, db: DB) -> SalaryRun:
    run = service.pay_run(db, access, service.get_run(db, access, run_id), paid_from_account_id=body.paid_from_account_id,
                          on=body.paid_on or local_today())
    db.commit()
    return run


@router.get("/pay-from")
def pay_from(access: Manage, db: DB) -> list[dict[str, Any]]:
    """Cash and bank accounts salaries can be paid from."""
    from app.modules.accounting.models import Account, AccountSubtype

    rows = db.scalars(select(Account).where(Account.business_id == access.business.id, Account.is_active.is_(True),
                                            Account.subtype.in_([AccountSubtype.CASH, AccountSubtype.BANK])).order_by(Account.code))
    return [{"id": a.id, "code": a.code, "name": a.name} for a in rows]


@router.get("/runs/{run_id}/statutory")
def run_statutory(run_id: uuid.UUID, access: Manage, db: DB) -> dict[str, Any]:
    """PF, ESI, professional tax and TDS for the month, with due dates."""
    return service.statutory(db, access, service.get_run(db, access, run_id))


@router.get("/runs/{run_id}/ecr.txt")
def run_ecr(run_id: uuid.UUID, access: Manage, db: DB) -> Response:
    run = service.get_run(db, access, run_id)
    return Response(service.ecr_text(db, access, run), media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="ECR-{run.month:%Y-%m}.txt"'})


@router.get("/runs/{run_id}/payslips/{employee_id}", response_class=HTMLResponse)
def payslip(run_id: uuid.UUID, employee_id: uuid.UUID, access: Manage, db: DB) -> HTMLResponse:
    return HTMLResponse(service.payslip_html(db, access, service.get_run(db, access, run_id), employee_id))
