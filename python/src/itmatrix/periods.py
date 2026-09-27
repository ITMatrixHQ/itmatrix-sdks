"""Client-side calendar windows for the bars API.

These are New York *calendar* dates, not exchange sessions. The backend expands
date-only ``to`` through the end of that date, so both endpoints are inclusive.
An exchange holiday or weekend can legitimately return no bars.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

BarPeriod = Literal["today", "week_to_date", "month_to_date", "year_to_date", "calendar_month"]
BAR_TIMEFRAMES = frozenset({"1s", "1m", "5m", "1h", "1d"})
_MAX_DAYS = {"1s": 7, "1m": 30, "5m": 90, "1h": 365, "1d": 1825}
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class BarWindow:
    start: date
    end: date
    tz: str = "America/New_York"

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def resolve_bar_window(
    period: BarPeriod, *, now: datetime | None = None, month: str | None = None
) -> BarWindow:
    """Resolve a named period without making an API call.

    ``now`` must be timezone-aware when supplied. ``month`` is ``YYYY-MM`` and
    applies only to ``calendar_month``. A current calendar month includes its
    future dates; use ``month_to_date`` for a period ending today.
    """
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("now must be timezone-aware")
    today = (now or datetime.now(_NEW_YORK)).astimezone(_NEW_YORK).date()
    if period != "calendar_month" and month is not None:
        raise ValueError("month applies only to calendar_month")
    if period == "today":
        start, end = today, today
    elif period == "week_to_date":
        start, end = today - timedelta(days=today.weekday()), today
    elif period == "month_to_date":
        start, end = today.replace(day=1), today
    elif period == "year_to_date":
        start, end = today.replace(month=1, day=1), today
    elif period == "calendar_month":
        if month is None:
            first = today.replace(day=1)
        else:
            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
                raise ValueError("month must be YYYY-MM")
            first = date.fromisoformat(f"{month}-01")
        start = first
        end = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    else:
        raise ValueError(f"unknown bar period {period!r}")
    return BarWindow(start, end)


def validate_bar_window(window: BarWindow, timeframe: str) -> None:
    """Reject a period the backend would silently truncate to its span cap."""
    if timeframe not in BAR_TIMEFRAMES:
        raise ValueError(f"unsupported bars timeframe {timeframe!r}")
    # Reject equality conservatively: a DST fall-back can make N New York
    # calendar dates one hour longer than N * 24 hours.
    if window.days >= _MAX_DAYS[timeframe]:
        raise ValueError(
            f"{timeframe} cannot cover {window.days} calendar days in one request; "
            "choose a coarser timeframe or a shorter period"
        )
