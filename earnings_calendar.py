"""
Earnings calendar filter (RECOMMENDATIONS_top10 #3).

A bracket entered the day before earnings is a coin-flip with asymmetric
downside: a -15% gap blows past the stop and Alpaca fills below it. The
filter excludes any candidate with earnings in the next ~5 trading days
(7 calendar days for weekend buffer).

Data source: yfinance Ticker.calendar (a small dict containing the next
earnings date, dividend date, and analyst estimates — does NOT require
lxml, unlike get_earnings_dates). Cached to disk with a 24h TTL.

Note: the post-earnings 2-day block in the original doc is not enforced
here. yfinance's calendar API only exposes the *next* earnings date, not
historical ones. The screen's daily cadence partly compensates — a name
that reported yesterday will have its next date push out ~90 days and
naturally clear the ahead-window — but post-earnings-drift entries are
not filtered separately. If this becomes an issue, add a historical
cache populated as dates fall off the "next" rotation.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Optional

import yfinance as yf


EARNINGS_CACHE_FILE = "earnings_dates_cache.json"
EARNINGS_CACHE_TTL_SECONDS = 24 * 3600

# Filter window (calendar days, slightly wider than the doc's "5 trading days"
# to leave a safety margin for weekends/holidays).
DAYS_AHEAD_BLOCKED = 7      # block if earnings within next 7 calendar days


@dataclass
class EarningsInfo:
    symbol: str
    next_date: Optional[str]      # ISO date string, or None
    days_until: Optional[int]     # signed: + = future, - = past
    in_window: bool               # True if within DAYS_AHEAD_BLOCKED ahead or DAYS_BEHIND_BLOCKED behind
    reason: Optional[str]         # explanation for log if in_window


# ---------------------------------------------------------------------------
# Cache (per-symbol, JSON on disk)
# ---------------------------------------------------------------------------

def _load_cache() -> dict:
    if not os.path.exists(EARNINGS_CACHE_FILE):
        return {}
    try:
        with open(EARNINGS_CACHE_FILE, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(EARNINGS_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except OSError as e:
        print(f"  Warning: could not write {EARNINGS_CACHE_FILE}: {e}")


# ---------------------------------------------------------------------------
# Earnings-date lookup
# ---------------------------------------------------------------------------

def _fetch_next_earnings_date(symbol: str) -> Optional[str]:
    """
    Return the next future earnings date for `symbol` as an ISO date string,
    or None if not available.

    Uses yfinance Ticker.calendar (a dict with 'Earnings Date' = list of dates,
    usually 1 entry, sometimes 2 when the exact day isn't announced — we take
    the *earliest* future date as a conservative lower bound).
    """
    try:
        cal = yf.Ticker(symbol).calendar
    except Exception:
        return None

    if not cal or not isinstance(cal, dict):
        return None

    raw_dates = cal.get("Earnings Date") or []
    if not isinstance(raw_dates, list):
        raw_dates = [raw_dates]

    today = date.today()
    future_dates: list[date] = []
    for d in raw_dates:
        # yfinance returns datetime.date instances directly; be defensive about
        # datetime or string forms in case yfinance changes the shape.
        if hasattr(d, "date") and not isinstance(d, date):
            d = d.date()
        if isinstance(d, date) and d >= today:
            future_dates.append(d)

    if not future_dates:
        return None
    return min(future_dates).isoformat()


def get_earnings_info(symbol: str, cache: Optional[dict] = None) -> EarningsInfo:
    """
    Return EarningsInfo for `symbol`. Uses the on-disk cache (24h TTL).

    Pass `cache` to batch-screen many symbols without repeatedly loading the
    cache from disk; the caller is responsible for saving via save_earnings_cache().
    """
    own_cache = cache is None
    if own_cache:
        cache = _load_cache()

    entry = cache.get(symbol)
    fetched_at = (entry or {}).get("fetched_at", 0)
    age = time.time() - fetched_at

    if entry and age < EARNINGS_CACHE_TTL_SECONDS:
        next_iso = entry.get("next_date")
    else:
        next_iso = _fetch_next_earnings_date(symbol)
        cache[symbol] = {"next_date": next_iso, "fetched_at": time.time()}

    if own_cache:
        _save_cache(cache)

    if not next_iso:
        return EarningsInfo(symbol=symbol, next_date=None, days_until=None,
                            in_window=False, reason=None)

    try:
        next_d = date.fromisoformat(next_iso)
    except ValueError:
        return EarningsInfo(symbol=symbol, next_date=None, days_until=None,
                            in_window=False, reason=None)

    days_until = (next_d - date.today()).days

    if 0 <= days_until <= DAYS_AHEAD_BLOCKED:
        reason = f"earnings in {days_until}d ({next_iso}) — within {DAYS_AHEAD_BLOCKED}d block"
        return EarningsInfo(symbol=symbol, next_date=next_iso, days_until=days_until,
                            in_window=True, reason=reason)

    return EarningsInfo(symbol=symbol, next_date=next_iso, days_until=days_until,
                        in_window=False, reason=None)


def save_earnings_cache(cache: dict) -> None:
    """Persist a shared cache after batch use."""
    _save_cache(cache)


# ---------------------------------------------------------------------------
# Filter helper
# ---------------------------------------------------------------------------

def passes_earnings_filter(symbol: str, cache: Optional[dict] = None) -> dict:
    """
    Returns {'passes': bool, 'reason': Optional[str], 'next_date': Optional[str],
             'days_until': Optional[int]}.

    Pass `cache` to batch over the universe; otherwise this opens/closes the
    on-disk cache per call.
    """
    info = get_earnings_info(symbol, cache=cache)
    return {
        "passes": not info.in_window,
        "reason": info.reason,
        "next_date": info.next_date,
        "days_until": info.days_until,
    }


if __name__ == "__main__":
    import sys
    syms = sys.argv[1:] or ["AAPL", "MSFT", "NVDA"]
    cache = _load_cache()
    for s in syms:
        info = get_earnings_info(s, cache=cache)
        if info.in_window:
            print(f"{s}: BLOCKED — {info.reason}")
        elif info.next_date:
            print(f"{s}: OK — next earnings {info.next_date} ({info.days_until}d out)")
        else:
            print(f"{s}: OK — no earnings date available")
    save_earnings_cache(cache)
