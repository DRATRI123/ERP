"""The business calendar date. Servers usually run in UTC, but an invoice raised at 00:30 in
India belongs to *that* day, not to yesterday's UTC date. Every "today" default in the
application comes from here (``ERP_TIMEZONE``, default Asia/Kolkata)."""

from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.core.config import get_settings


@lru_cache(maxsize=4)
def _zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def local_today() -> date:
    return datetime.now(_zone(get_settings().timezone)).date()


def local_date(moment: datetime) -> date:
    """The business-calendar date of an aware moment (e.g. a UTC timestamp)."""
    return moment.astimezone(_zone(get_settings().timezone)).date()
