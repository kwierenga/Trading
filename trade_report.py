"""
Rich per-trade email blocks (facts / motivation / amount / goals).

Shared by the after-market EOD email (eod_routine.py) and the weekly review
(weekly_review.py) so both render trades the same way. Pure formatting over
trade_journal records (+ optional live Alpaca position data); no I/O, no API
calls, never raises on a missing field.

Each block answers the four things Klaas asked for per trade:
  - Facts      : prices, dates, P&L, holding time
  - Amount     : shares and notional
  - Goals      : target / stop (with %), risk-reward
  - Motivation : the AI rationale captured at entry
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

# Keep the rationale readable in a plain-text email without dominating it.
RATIONALE_MAX_CHARS = 320


def _date(iso: Optional[str]) -> str:
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "?"


def _held(duration_minutes: Optional[float]) -> str:
    if not duration_minutes:
        return "?"
    days = duration_minutes / (60 * 24)
    if days >= 1:
        return f"{days:.0f}d"
    return f"{duration_minutes / 60:.0f}h"


def _rationale(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = str(text).strip()
    if len(t) > RATIONALE_MAX_CHARS:
        t = t[:RATIONALE_MAX_CHARS].rstrip() + "…"
    return t


def _goal_line(entry: Optional[float], target: Optional[float], stop: Optional[float]) -> Optional[str]:
    """Target/stop with % moves and risk-reward, computed from prices we store."""
    parts = []
    if target and entry:
        parts.append(f"target ${target:,.2f} ({(target - entry) / entry:+.0%})")
    elif target:
        parts.append(f"target ${target:,.2f}")
    if stop and entry:
        parts.append(f"stop ${stop:,.2f} ({(stop - entry) / entry:+.0%})")
    elif stop:
        parts.append(f"stop ${stop:,.2f}")
    if target and stop and entry and (entry - stop) > 0:
        parts.append(f"R/R {(target - entry) / (entry - stop):.1f}")
    return "Goal: " + ", ".join(parts) if parts else None


def closed_trade_block(t: Dict) -> str:
    """Rich block for a trade that has closed (today or this week)."""
    sym = t.get("symbol", "?")
    shares = t.get("shares") or t.get("original_shares") or 0
    entry = t.get("entry_price")
    exit_price = t.get("exit_price")
    pnl = t.get("pnl") or 0
    pnl_pct = (t.get("pnl_pct") or 0) * 100
    notional = (shares * entry) if (shares and entry) else None

    head = f"{sym:<6} {pnl_pct:+.1f}%  (${pnl:+,.0f})"
    lines = [head]

    fact = f"  Bought {shares} @ ${entry:,.2f} ({_date(t.get('entry_time'))})" if entry else f"  {shares} shares"
    if exit_price:
        fact += f" → Sold @ ${exit_price:,.2f} ({_date(t.get('exit_time'))})"
    fact += f"  · held {_held(t.get('duration_minutes'))}  · exit: {t.get('reason_exit', '?')}"
    lines.append(fact)

    if notional is not None:
        lines.append(f"  Amount: ${notional:,.0f}")

    goal = _goal_line(entry, t.get("target_price"), t.get("initial_stop") or t.get("stop_loss"))
    if goal:
        lines.append(f"  {goal} (as set at entry)")

    why = _rationale(t.get("rationale"))
    if why:
        lines.append(f"  Why entered: {why}")

    return "\n".join(lines)


def open_position_block(live_pos: Dict, journal_trade: Optional[Dict] = None) -> str:
    """Rich block for an open position. `live_pos` is the Alpaca position dict;
    `journal_trade` (matched by symbol) supplies goals/rationale/holding status."""
    sym = live_pos.get("symbol", "?")
    value = float(live_pos.get("market_value", 0) or 0)
    unreal = float(live_pos.get("unrealized_plpc", 0) or 0)
    qty = float(live_pos.get("qty", 0) or 0)
    avg_entry = float(live_pos.get("avg_entry_price", 0) or 0)

    lines = [f"{sym:<6} {unreal:+.1%} unrealized  (${value:,.0f})"]

    fact = f"  Holding {qty:g} @ ${avg_entry:,.2f}"
    jt = journal_trade or {}
    if jt.get("entry_time"):
        fact += f" since {_date(jt.get('entry_time'))}"
    # Holding-status tag (LTCG progress) if the journal computed one.
    hs = jt.get("holding_status", {}) or {}
    if hs.get("status") == "LTCG":
        fact += "  · LTCG eligible"
    elif hs.get("status") == "approaching_LTCG":
        fact += f"  · LTCG in {hs.get('days_to_LTCG')}d"
    elif hs.get("days_held") is not None:
        fact += f"  · held {hs.get('days_held')}d"
    lines.append(fact)

    if qty and avg_entry:
        lines.append(f"  Amount: ${qty * avg_entry:,.0f}")

    entry_for_goal = jt.get("entry_price") or (avg_entry or None)
    goal = _goal_line(entry_for_goal, jt.get("target_price"), jt.get("initial_stop") or jt.get("stop_loss"))
    if goal:
        lines.append(f"  {goal}")

    why = _rationale(jt.get("rationale"))
    if why:
        lines.append(f"  Why: {why}")

    return "\n".join(lines)
