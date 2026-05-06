"""
NYSE trading-day check.

Used by morning_routine.py (skip plan generation on holidays) and
execute.yml (refuse to submit orders into a closed market).

Without this, both workflows would fire on US market holidays — Memorial
Day, July 4, Thanksgiving, etc. — and try to submit orders into a closed
exchange. Alpaca behavior on closed-market submissions is configuration-
dependent (typically queues to next open) and not something we want to
rely on.

Half-day closes (e.g., day after Thanksgiving) are still treated as
trading days here. Bracket orders submitted on a half-day will fill
normally; the only nuance is the early 13:00 ET close, which the RTH
window check in execute.yml already accommodates.

Run as CLI for use from a workflow step:
    python market_calendar.py        # exit 0 if trading day, 1 otherwise
"""

import sys
from datetime import datetime

import pytz

EST = pytz.timezone("America/New_York")

_nyse = None


def _get_nyse():
    """Lazy-load the NYSE calendar — pandas_market_calendars import is slow."""
    global _nyse
    if _nyse is None:
        import pandas_market_calendars as mcal
        _nyse = mcal.get_calendar("NYSE")
    return _nyse


def is_us_trading_day(date=None) -> bool:
    """
    True if `date` (default: today in America/New_York) is a regular NYSE
    trading day — weekends and US market holidays return False.
    """
    if date is None:
        date = datetime.now(EST).date()
    sched = _get_nyse().schedule(start_date=date, end_date=date)
    return not sched.empty


if __name__ == "__main__":
    today_ny = datetime.now(EST).strftime("%Y-%m-%d %A")
    if is_us_trading_day():
        print(f"{today_ny} — NYSE trading day")
        sys.exit(0)
    else:
        print(f"{today_ny} — NOT a NYSE trading day (weekend or holiday)")
        sys.exit(1)
