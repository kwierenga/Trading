"""
Pyramid on confirmed winners (RECOMMENDATIONS_top10 #5).

The strategy doc explicitly says "deploy cash reserve for adding to winners"
but the code path never existed — dry powder just sat through working trades.
This module fixes that: for any open position that's confirmed working, propose
an "add" of 8-12% equity at the next AM cycle.

Eligibility (ALL must hold):
  - held >= 5 trading days  (no day-1 chasers — wait for thesis confirmation)
  - unrealized return >= +5%
  - current price > SMA20
  - SMA50 slope not negative (trend intact)
  - has not already been pyramided 2x for this position
  - no earnings in next 7 calendar days (reuse earnings_calendar filter)

Output: trade dicts in the same schema as Claude's plan, flagged
`is_pyramid=True` and `tranche=N`. `morning_routine.run_plan_phase` appends
these to `latest_strategy.json`. `execute_strategy.py` relaxes the
concentration cap from 25% -> 30% when `is_pyramid` is set on the trade.
Max 2 adds per symbol — beyond that, just trail.

Pairs with position_manager.py (rec #1): trail to lock ground gained,
pyramid only on what's already working = asymmetric compounder.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from alpaca_client import AlpacaClient
from earnings_calendar import passes_earnings_filter
from market_data import get_technicals
from trade_journal import TradeJournal


# Eligibility thresholds
MIN_DAYS_HELD = 5
MIN_UNREALIZED_PCT = 0.05
MAX_ADDS_PER_SYMBOL = 2

# Sizing — shared with execute_strategy.py's tranche cap so the two can't drift
from position_sizer import MAX_PYRAMID_TRANCHE_PCT as PYRAMID_ALLOCATION_PCT  # ~10% of equity per add (8-12% band)
PYRAMID_LIMIT_NUDGE_PCT = 0.005 # limit price = current * 1.005 (small upward nudge)


def _days_held(entry_iso: str) -> int:
    if not entry_iso:
        return 0
    try:
        dt = datetime.fromisoformat(entry_iso.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return (datetime.now(timezone.utc) - dt).days


def _pyramid_count_for_symbol(symbol: str) -> int:
    """How many pyramid adds have already been logged for this symbol's current open run?"""
    trades = TradeJournal.load_trades()
    return sum(
        1 for t in trades
        if (t.get("symbol") or "").upper() == symbol.upper()
        and t.get("status") == "open"
        and t.get("is_pyramid")
    )


def _earliest_open_entry(symbol: str) -> Optional[Dict]:
    """
    Oldest open journal entry for this symbol. Pyramid eligibility uses the
    age of the cohort — not the most recent add. A position that's been held
    10 days but got a pyramid tranche yesterday is still a 10-day-old position.
    """
    trades = TradeJournal.load_trades()
    matches = [t for t in trades if t.get("status") == "open"
               and (t.get("symbol") or "").upper() == symbol.upper()]
    if not matches:
        return None
    return min(matches, key=lambda t: t.get("entry_time", ""))


def _check_eligibility(symbol: str, alpaca_position: Dict, journal_entry: Optional[Dict],
                       tech) -> tuple[bool, str]:
    """Return (eligible, reason). reason is set on both pass and fail paths."""
    if journal_entry is None:
        return False, "no matching open journal entry"

    days = _days_held(journal_entry.get("entry_time", ""))
    if days < MIN_DAYS_HELD:
        return False, f"held only {days}d (need >= {MIN_DAYS_HELD})"

    try:
        unrealized_pct = float(alpaca_position.get("unrealized_plpc") or 0)
    except (TypeError, ValueError):
        unrealized_pct = 0.0
    if unrealized_pct < MIN_UNREALIZED_PCT:
        return False, f"unrealized {unrealized_pct:+.1%} below +{MIN_UNREALIZED_PCT:.0%} threshold"

    if tech is None:
        return False, "no technicals available"
    if tech.sma20 is None or tech.price <= tech.sma20:
        return False, f"price ${tech.price:.2f} not above SMA20 ${(tech.sma20 or 0):.2f}"
    if tech.sma50_slope_pct is not None and tech.sma50_slope_pct < 0:
        return False, f"SMA50 slope {tech.sma50_slope_pct:+.1f}% — trend rolling over"

    n_adds = _pyramid_count_for_symbol(symbol)
    if n_adds >= MAX_ADDS_PER_SYMBOL:
        return False, f"already pyramided {n_adds}x (max {MAX_ADDS_PER_SYMBOL})"

    earn = passes_earnings_filter(symbol)
    if not earn.get("passes"):
        return False, earn.get("reason") or "earnings within window"

    return True, f"held {days}d, unrealized {unrealized_pct:+.1%}, above SMA20, SMA50 slope OK"


def _build_pyramid_trade(symbol: str, alpaca_position: Dict, journal_entry: Dict,
                         tech, equity: float, tranche: int) -> Optional[Dict]:
    price = tech.price
    # Limit ~0.5% above current to give a small head-start; if it doesn't fill, no harm.
    limit_price = round(price * (1 + PYRAMID_LIMIT_NUDGE_PCT), 2)

    # Stop = break-even on the *combined* cost basis. We're adding ~10% equity
    # worth at limit_price; combine with existing avg_entry weighted by qty/value.
    existing_qty = int(float(alpaca_position.get("qty") or 0))
    existing_value = float(alpaca_position.get("cost_basis") or
                            (existing_qty * float(alpaca_position.get("avg_entry_price") or 0)))
    add_dollars = equity * PYRAMID_ALLOCATION_PCT
    add_shares = max(1, int(add_dollars / limit_price))
    add_value = add_shares * limit_price
    combined_qty = existing_qty + add_shares
    combined_value = existing_value + add_value
    breakeven_combined = combined_value / combined_qty if combined_qty > 0 else limit_price
    stop = round(breakeven_combined * 0.995, 2)  # tiny cushion below break-even

    if stop >= limit_price:
        # Math says the combined break-even is already above today's price — would
        # immediately stop out. Bail.
        return None

    # Loose target — the position manager will trail; the explicit target_price
    # is mostly informational and used by the bracket. Set it well above so it
    # doesn't bind before trailing takes over at +3R.
    target = round(limit_price * 1.20, 2)

    return {
        "symbol": symbol,
        "direction": "BUY",
        "entry_price": limit_price,
        "stop_loss": stop,
        "target": target,
        "conviction": 75,
        "holding_period": "swing",
        "risk_reward_ratio": (target - limit_price) / max(0.01, limit_price - stop),
        "rationale": (
            f"PYRAMID tranche {tranche}: original lot held "
            f"{_days_held(journal_entry.get('entry_time', ''))}d at +"
            f"{float(alpaca_position.get('unrealized_plpc') or 0):.1%}; "
            f"price ${tech.price:.2f} > SMA20 ${tech.sma20:.2f}; "
            f"adding ${add_value:,.0f} ({add_shares} sh) with combined break-even "
            f"stop at ${stop:.2f}."
        ),
        "entry_strategy": f"Limit @ ${limit_price:.2f} (current +0.5%)",
        "exit_plan": "Bracket; trail-managed after +1R by position_manager.",
        "is_pyramid": True,
        "tranche": tranche,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def find_pyramid_candidates(verbose: bool = True) -> List[Dict]:
    """
    Scan open Alpaca positions and return a list of trade dicts suitable for
    appending to `latest_strategy.json`.
    """
    client = AlpacaClient()
    try:
        positions = client.get_positions()
        account = client.get_account()
        equity = float(account.get("equity") or 0)
    except Exception as e:
        if verbose:
            print(f"  pyramid: could not fetch account/positions: {e}")
        return []

    proposals: List[Dict] = []
    for p in positions:
        sym = (p.get("symbol") or "").upper()
        if not sym:
            continue
        journal = _earliest_open_entry(sym)
        tech = get_technicals(sym)
        ok, reason = _check_eligibility(sym, p, journal, tech)
        if not ok:
            if verbose:
                print(f"  {sym}: skip — {reason}")
            continue
        tranche = _pyramid_count_for_symbol(sym) + 2  # original is tranche 1
        trade = _build_pyramid_trade(sym, p, journal, tech, equity, tranche)
        if trade:
            proposals.append(trade)
            if verbose:
                print(f"  {sym}: PROPOSE pyramid tranche {tranche} — {reason}")
        elif verbose:
            print(f"  {sym}: skip — combined break-even stop above current price (math precludes)")

    return proposals


if __name__ == "__main__":
    candidates = find_pyramid_candidates(verbose=True)
    print()
    if not candidates:
        print("No pyramid candidates today.")
    else:
        print(f"{len(candidates)} pyramid candidate(s):")
        for c in candidates:
            print(f"  {c['symbol']} tranche {c['tranche']}: "
                  f"entry ${c['entry_price']:.2f}, stop ${c['stop_loss']:.2f}, "
                  f"target ${c['target']:.2f}")
            print(f"    {c['rationale']}")
