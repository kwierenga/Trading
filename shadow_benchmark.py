"""
Shadow benchmark portfolio (RECOMMENDATIONS_top10 #8).

Maintain a parallel "passive baseline" portfolio that simulates putting
$100k into 70% SPY + 30% USMV on day-1 and never trading. Compare the
strategy's live equity against this benchmark to answer the question the
Phase-2 graduation criteria in `trading_rollout_plan.md` are defined on:
"Net-of-tax CAGR > SPY by >= 1%".

State: `shadow_portfolio.json` (initial shares + history of daily values).
Updates: daily at EOD via `eod_routine.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import yfinance as yf


STATE_FILE = Path("shadow_portfolio.json")

INITIAL_EQUITY = 100_000.0
ALLOCATIONS = {"SPY": 0.70, "USMV": 0.30}

# Day 1 of paper trading per the journal — first [AM] entry is 2026-05-04
# in JOURNAL.md, first Alpaca buy fill is 2026-05-08. Use the earlier of the
# two as the benchmark anchor so we don't get an unfair head-start vs the
# strategy.
DEFAULT_START_DATE = "2026-05-04"


@dataclass
class ShadowState:
    start_date: str
    initial_equity: float
    shares: Dict[str, float]          # symbol -> share count (fractional ok)
    start_prices: Dict[str, float]    # symbol -> price on start_date
    history: List[Dict]               # rolling daily snapshots


def _close_on_or_after(symbol: str, anchor: date) -> Optional[tuple[date, float]]:
    """First available close on or after `anchor`. Returns (date, price) or None."""
    try:
        hist = yf.Ticker(symbol).history(start=anchor.isoformat(),
                                          end=(anchor.replace(day=min(anchor.day + 14, 28))).isoformat(),
                                          auto_adjust=True)
    except Exception:
        return None
    if hist is None or hist.empty:
        return None
    first_idx = hist.index[0]
    return (first_idx.date(), float(hist["Close"].iloc[0]))


def _latest_close(symbol: str) -> Optional[float]:
    try:
        hist = yf.Ticker(symbol).history(period="5d", auto_adjust=True)
    except Exception:
        return None
    if hist is None or hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


def _load_state() -> Optional[ShadowState]:
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return ShadowState(
        start_date=data["start_date"],
        initial_equity=float(data["initial_equity"]),
        shares=dict(data["shares"]),
        start_prices=dict(data["start_prices"]),
        history=list(data.get("history", [])),
    )


def _save_state(state: ShadowState) -> None:
    STATE_FILE.write_text(json.dumps(asdict(state), indent=2, default=str), encoding="utf-8")


def _initialize(start_date_str: str) -> Optional[ShadowState]:
    """Allocate $100k 70/30 SPY/USMV at the close on start_date_str."""
    anchor = date.fromisoformat(start_date_str)
    shares: Dict[str, float] = {}
    start_prices: Dict[str, float] = {}
    actual_start: Optional[date] = None
    for sym, alloc in ALLOCATIONS.items():
        hit = _close_on_or_after(sym, anchor)
        if not hit:
            print(f"  Shadow benchmark: could not fetch start price for {sym} on or after {anchor}")
            return None
        d, price = hit
        actual_start = d if actual_start is None else min(actual_start, d)
        start_prices[sym] = price
        shares[sym] = (INITIAL_EQUITY * alloc) / price

    return ShadowState(
        start_date=(actual_start or anchor).isoformat(),
        initial_equity=INITIAL_EQUITY,
        shares=shares,
        start_prices=start_prices,
        history=[],
    )


def update_shadow(strategy_equity: Optional[float] = None,
                  start_date: str = DEFAULT_START_DATE) -> Dict:
    """
    Update the shadow portfolio for today and return a report dict suitable
    for the EOD email.

    Args:
      strategy_equity: live account equity (from Alpaca). When provided, the
                       report includes the spread vs the shadow.
      start_date: ISO date string for the day-1 allocation (only used on first run).
    """
    state = _load_state()
    if state is None:
        state = _initialize(start_date)
        if state is None:
            return {"available": False, "reason": "could not initialize shadow benchmark"}

    today_iso = datetime.now(timezone.utc).date().isoformat()

    # Skip if already updated today (idempotent within a day)
    if state.history and state.history[-1].get("date") == today_iso:
        snap = state.history[-1]
    else:
        prices: Dict[str, float] = {}
        for sym in state.shares:
            p = _latest_close(sym)
            if p is None:
                return {"available": False, "reason": f"could not price {sym}"}
            prices[sym] = p
        value = sum(state.shares[sym] * prices[sym] for sym in state.shares)
        snap = {
            "date": today_iso,
            "value": round(value, 2),
            "prices": {sym: round(prices[sym], 4) for sym in prices},
            "return_pct": (value / state.initial_equity - 1),
        }
        state.history.append(snap)
        # Keep only last 400 entries to avoid unbounded growth
        state.history = state.history[-400:]
        _save_state(state)

    report = {
        "available": True,
        "shadow_value": snap["value"],
        "shadow_return_pct": snap["return_pct"],
        "start_date": state.start_date,
        "initial_equity": state.initial_equity,
    }

    if strategy_equity is not None and state.initial_equity > 0:
        strategy_return = strategy_equity / state.initial_equity - 1
        report["strategy_equity"] = strategy_equity
        report["strategy_return_pct"] = strategy_return
        report["spread_pct"] = strategy_return - snap["return_pct"]

    return report


def report_block(report: Dict) -> str:
    """Compact lines for the EOD email."""
    if not report.get("available"):
        reason = report.get("reason", "unknown")
        return f"SHADOW BENCHMARK: not available ({reason})"

    lines = [
        f"SHADOW BENCHMARK (SPY 70% + USMV 30% since {report['start_date']}):",
        f"  Shadow value: ${report['shadow_value']:,.0f} "
        f"({report['shadow_return_pct']:+.2%} since start)",
    ]
    if "strategy_return_pct" in report:
        lines.append(
            f"  Strategy:     ${report['strategy_equity']:,.0f} "
            f"({report['strategy_return_pct']:+.2%}); spread vs shadow {report['spread_pct']:+.2%}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    eq = None
    if "--equity" in sys.argv:
        i = sys.argv.index("--equity")
        if i + 1 < len(sys.argv):
            eq = float(sys.argv[i + 1])
    r = update_shadow(strategy_equity=eq)
    print(report_block(r))
    if r.get("available"):
        print(f"\n  Raw: {r}")
