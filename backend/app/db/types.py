"""Portable column types.

Every type here behaves identically on SQLite and PostgreSQL so business logic never
depends on the backing database.

* ``UTCDateTime`` – always timezone-aware UTC in Python. SQLite has no timezone support,
  so values are stored as naive UTC there and re-tagged on load.
* ``ScaledDecimal`` / ``Money`` / ``Quantity`` – exact decimals. SQLite's NUMERIC affinity
  silently converts to binary floating point, which is unacceptable for money, so on SQLite
  the value is stored as a scaled 64-bit integer (e.g. ``12.3456`` → ``123456`` with scale 4).
  Integer SUM() stays exact. On PostgreSQL the native ``NUMERIC(p, s)`` type is used.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import BigInteger, DateTime, Enum, Numeric
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not allowed; use timezone-aware UTC values")
        value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class ScaledDecimal(TypeDecorator[Decimal]):
    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = 18, scale: int = 4) -> None:
        super().__init__()
        self.precision = precision
        self.scale = scale
        self._quantum = Decimal(1).scaleb(-scale)
        self._factor = Decimal(10) ** scale

    def __repr__(self) -> str:
        # Alembic renders column types with repr(); keep precision/scale in migrations.
        return f"ScaledDecimal(precision={self.precision}, scale={self.scale})"

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(BigInteger())
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def _quantize(self, value: Decimal | int | str) -> Decimal:
        if isinstance(value, float):
            raise TypeError("float is not allowed for decimal columns; use Decimal or str")
        d = value if isinstance(value, Decimal) else Decimal(value)
        q = d.quantize(self._quantum, rounding=ROUND_HALF_UP)
        if q != d:
            raise ValueError(f"{d} has more than {self.scale} decimal places")
        if len(q.as_tuple().digits) > self.precision:
            raise ValueError(f"{d} exceeds precision {self.precision}")
        return q

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        q = self._quantize(value)
        if dialect.name == "sqlite":
            return int(q * self._factor)
        return q

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        if dialect.name == "sqlite":
            return (Decimal(int(value)) / self._factor).quantize(self._quantum)
        return Decimal(value).quantize(self._quantum)


def Money() -> ScaledDecimal:  # noqa: N802 - reads like a type
    """Monetary amount: 18 digits, 4 decimal places (supports rates and paise-level rounding)."""
    return ScaledDecimal(18, 4)


def Quantity() -> ScaledDecimal:  # noqa: N802
    """Stock quantity: 18 digits, 3 decimal places (grams, millilitres, etc.)."""
    return ScaledDecimal(18, 3)


def str_enum(enum_cls: type[enum.Enum], length: int = 20) -> Enum:
    """Portable enum column: stored as VARCHAR holding the enum *value* (not the member name),
    with a CHECK constraint. Avoids PostgreSQL native ENUM types, which are painful to migrate."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
        create_constraint=True,
        name=f"{enum_cls.__name__.lower()}",
    )
