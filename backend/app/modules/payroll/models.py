"""Staff, attendance and monthly salary runs."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import Money, UTCDateTime, str_enum
from app.modules.catalog.models import Rate


class PayType(enum.StrEnum):
    MONTHLY = "monthly"  # fixed salary, prorated by paid days
    DAILY = "daily"  # wage × paid days


class Employee(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("business_id", "code"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("branches.id", ondelete="RESTRICT"))
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    designation: Mapped[str | None] = mapped_column(String(80))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    joining_date: Mapped[date] = mapped_column(Date, nullable=False)
    leaving_date: Mapped[date | None] = mapped_column(Date)
    pay_type: Mapped[PayType] = mapped_column(str_enum(PayType, 10), default=PayType.MONTHLY, nullable=False)
    monthly_salary: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    daily_wage: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    commission_percent: Mapped[Decimal] = mapped_column(Rate(), default=Decimal(0), nullable=False)  # of own sales
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))  # salesperson link
    pan: Mapped[str | None] = mapped_column(String(10))
    bank_account: Mapped[str | None] = mapped_column(String(34))
    ifsc: Mapped[str | None] = mapped_column(String(11))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class AttendanceStatus(enum.StrEnum):
    PRESENT = "present"
    ABSENT = "absent"  # unpaid
    HALF_DAY = "half_day"  # half paid
    LEAVE = "leave"  # paid leave
    HOLIDAY = "holiday"  # paid


class Attendance(UUIDPrimaryKey, Base):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("employee_id", "day"), Index("ix_attendance_business_day", "business_id", "day"))

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[AttendanceStatus] = mapped_column(str_enum(AttendanceStatus, 10), nullable=False)
    marked_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))


class RunStatus(enum.StrEnum):
    DRAFT = "draft"
    POSTED = "posted"  # in the books as salaries payable
    PAID = "paid"


class SalaryRun(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "salary_runs"
    __table_args__ = (UniqueConstraint("business_id", "month"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)  # first day of the month
    status: Mapped[RunStatus] = mapped_column(str_enum(RunStatus, 10), default=RunStatus.DRAFT, nullable=False)
    gross: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    deductions: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    net: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    paid_from_account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    lines: Mapped[list[SalaryLine]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="SalaryLine.employee_name")


class SalaryLine(UUIDPrimaryKey, Base):
    __tablename__ = "salary_lines"
    __table_args__ = (UniqueConstraint("run_id", "employee_id"),)

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("salary_runs.id", ondelete="CASCADE"), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    employee_name: Mapped[str] = mapped_column(String(120), nullable=False)
    pay_type: Mapped[PayType] = mapped_column(str_enum(PayType, 10), nullable=False)
    days_in_month: Mapped[int] = mapped_column(nullable=False)
    payable_days: Mapped[Decimal] = mapped_column(Rate(), nullable=False)  # days employed this month
    paid_days: Mapped[Decimal] = mapped_column(Rate(), nullable=False)
    base: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    sales: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    bonus: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)
    deductions: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0), nullable=False)  # advance recovery etc.
    net: Mapped[Decimal] = mapped_column(Money(), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
