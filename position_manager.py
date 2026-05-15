"""
Position manager — trailing stops + scale-out (RECOMMENDATIONS_top10 #1).

The biggest single expected-return delta on the rec list. The current bracket
caps every winner at the static take-profit. A basing setup that runs +30-60%
over 3 months gets exited at +14-19%. This module:

  +1R unrealized: sell 25%, raise stop to break-even
  +2R unrealized: sell another 25%, raise stop to entry + 1R
  +3R+ daily:     trail the stop by 1.5 * ATR(14)  (only goes up, never down)

Where R = (entry_price - initial_stop). Persisted on the journal entry at
trade-submit time by `TradeJournal.log_entry`, so it survives Alpaca
order-state changes.

Mechanics on Alpaca:
  - The original bracket has TP + SL OCO legs. On first scale-out we cancel
    BOTH legs and replace with a standalone GTC stop for the remaining
    shares. The TP is *not* re-submitted — the trail manages the upside from
    here, so the winner has no cap.
  - Subsequent state changes only need to cancel + replace the SL.

Safety:
  - Defaults to dry_run=True. The EOD hook starts in dry-run for a few days
    so we can observe the planned actions before going live.
  - Trades without `R` set in the journal (e.g. reconciled pre-existing
    lots) are skipped with a note — we don't try to manage positions we
    don't have the entry context for.
  - Stops only go UP. Any computed new_stop <= current_stop is ignored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yfinance as yf

from alpaca_client import AlpacaClient
from trade_journal import TradeJournal


SCALE_OUT_FRACTION = 0.25       # sell 25% at +1R and another 25% at +2R
ATR_TRAIL_MULTIPLE = 1.5        # trail by 1.5 * ATR(14) at +3R and beyond
BREAKEVEN_EPSILON_PCT = 0.001   # nudge break-even stop a hair above entry to cover commission


@dataclass
class ManagedPosition:
    """Joined view of an Alpaca position and its matching journal entry."""
    symbol: str
    alpaca_qty: int
    alpaca_avg_entry: float
    current_price: float
    journal_id: int
    entry_price: float
    initial_stop: float
    R: float
    current_stop: float
    scaled_25_at_1r: bool
    scaled_25_at_2r: bool
    original_shares: int


@dataclass
class PlannedAction:
    symbol: str
    action: str           # "scale_out_1r" | "scale_out_2r" | "trail" | "noop" | "skip_no_R"
    qty: int = 0
    new_stop: Optional[float] = None
    rationale: str = ""


# ---------------------------------------------------------------------------
# Journal helpers
# ---------------------------------------------------------------------------

def _latest_open_entry_for_symbol(symbol: str) -> Optional[Dict]:
    """Most recent open journal entry for `symbol`, or None."""
    trades = TradeJournal.load_trades()
    matches = [t for t in trades if t.get("status") == "open"
               and (t.get("symbol") or "").upper() == symbol.upper()]
    if not matches:
        return None
    return max(matches, key=lambda t: t.get("entry_time", ""))


def _persist_journal_change(trade_id: int, updates: Dict) -> None:
    trades = TradeJournal.load_trades()
    for t in trades:
        if t.get("id") == trade_id:
            t.update(updates)
            TradeJournal.save_trades(trades)
            return


# ---------------------------------------------------------------------------
# Price + volatility
# ---------------------------------------------------------------------------

def _current_price(client: AlpacaClient, symbol: str) -> Optional[float]:
    """Real-time quote midpoint via Alpaca, falling back to yfinance close."""
    q = client.get_latest_quote(symbol)
    if q:
        try:
            bid = float(q.get("bp") or 0)
            ask = float(q.get("ap") or 0)
            if bid > 0 and ask > 0 and ask >= bid:
                return (bid + ask) / 2
        except (TypeError, ValueError):
            pass
    try:
        hist = yf.Ticker(symbol).history(period="1d", auto_adjust=True)
        if hist is not None and not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


def _atr14(symbol: str) -> Optional[float]:
    """ATR(14) in price units. Used for the +3R trail step size."""
    try:
        hist = yf.Ticker(symbol).history(period="60d", auto_adjust=True)
    except Exception:
        return None
    if hist is None or len(hist) < 15:
        return None
    highs = hist["High"].tolist()
    lows = hist["Low"].tolist()
    closes = hist["Close"].tolist()
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < 14:
        return None
    return sum(trs[-14:]) / 14


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def _build_managed_position(client: AlpacaClient, alpaca_position: Dict) -> Optional[ManagedPosition]:
    sym = (alpaca_position.get("symbol") or "").upper()
    if not sym:
        return None

    journal = _latest_open_entry_for_symbol(sym)
    if not journal:
        return None
    if not journal.get("R") or journal.get("R") <= 0:
        return ManagedPosition(  # caller will see R=0 and skip
            symbol=sym,
            alpaca_qty=int(float(alpaca_position.get("qty") or 0)),
            alpaca_avg_entry=float(alpaca_position.get("avg_entry_price") or 0),
            current_price=0.0,
            journal_id=int(journal.get("id") or 0),
            entry_price=float(journal.get("entry_price") or 0),
            initial_stop=float(journal.get("initial_stop") or journal.get("stop_loss") or 0),
            R=0.0,
            current_stop=float(journal.get("current_stop") or journal.get("stop_loss") or 0),
            scaled_25_at_1r=bool(journal.get("scaled_25_at_1r")),
            scaled_25_at_2r=bool(journal.get("scaled_25_at_2r")),
            original_shares=int(journal.get("original_shares") or journal.get("shares") or 0),
        )

    cur = _current_price(client, sym)
    if cur is None:
        return None

    return ManagedPosition(
        symbol=sym,
        alpaca_qty=int(float(alpaca_position.get("qty") or 0)),
        alpaca_avg_entry=float(alpaca_position.get("avg_entry_price") or 0),
        current_price=cur,
        journal_id=int(journal.get("id") or 0),
        entry_price=float(journal.get("entry_price") or 0),
        initial_stop=float(journal.get("initial_stop") or journal.get("stop_loss") or 0),
        R=float(journal.get("R")),
        current_stop=float(journal.get("current_stop") or journal.get("stop_loss") or 0),
        scaled_25_at_1r=bool(journal.get("scaled_25_at_1r")),
        scaled_25_at_2r=bool(journal.get("scaled_25_at_2r")),
        original_shares=int(journal.get("original_shares") or journal.get("shares") or 0),
    )


def plan_action(pos: ManagedPosition) -> PlannedAction:
    """Decide what (if anything) to do for this position. Pure function."""
    if pos.R <= 0:
        return PlannedAction(symbol=pos.symbol, action="skip_no_R",
                             rationale="position predates R tracking (likely reconciled lot) — managed manually")

    gain_in_R = (pos.current_price - pos.entry_price) / pos.R

    # +1R: scale 25%, raise stop to break-even
    if not pos.scaled_25_at_1r and gain_in_R >= 1.0:
        qty = max(1, int(round(pos.original_shares * SCALE_OUT_FRACTION)))
        qty = min(qty, pos.alpaca_qty - 1)  # leave at least 1 share to manage
        if qty <= 0:
            return PlannedAction(symbol=pos.symbol, action="noop",
                                 rationale="position too small to scale out (would leave 0 shares)")
        new_stop = round(pos.entry_price * (1 + BREAKEVEN_EPSILON_PCT), 2)
        return PlannedAction(
            symbol=pos.symbol, action="scale_out_1r", qty=qty, new_stop=new_stop,
            rationale=f"+{gain_in_R:.2f}R unrealized (${pos.current_price:.2f} vs entry ${pos.entry_price:.2f}, R=${pos.R:.2f}) — "
                      f"sell {qty}/{pos.alpaca_qty}, raise stop to break-even ${new_stop:.2f}",
        )

    # +2R: scale another 25%, raise stop to entry + 1R
    if pos.scaled_25_at_1r and not pos.scaled_25_at_2r and gain_in_R >= 2.0:
        qty = max(1, int(round(pos.original_shares * SCALE_OUT_FRACTION)))
        qty = min(qty, pos.alpaca_qty - 1)
        if qty <= 0:
            return PlannedAction(symbol=pos.symbol, action="noop",
                                 rationale="position too small for second scale-out")
        new_stop = round(pos.entry_price + pos.R, 2)
        return PlannedAction(
            symbol=pos.symbol, action="scale_out_2r", qty=qty, new_stop=new_stop,
            rationale=f"+{gain_in_R:.2f}R unrealized — sell another {qty}, raise stop to entry+1R ${new_stop:.2f}",
        )

    # +3R+: trail by 1.5 * ATR(14), stops only go up
    if pos.scaled_25_at_2r and gain_in_R >= 3.0:
        atr = _atr14(pos.symbol)
        if atr is None or atr <= 0:
            return PlannedAction(symbol=pos.symbol, action="noop",
                                 rationale=f"+{gain_in_R:.2f}R, but ATR unavailable — no trail update")
        candidate = round(pos.current_price - ATR_TRAIL_MULTIPLE * atr, 2)
        if candidate <= pos.current_stop:
            return PlannedAction(symbol=pos.symbol, action="noop",
                                 rationale=f"+{gain_in_R:.2f}R, trail candidate ${candidate:.2f} not above current stop ${pos.current_stop:.2f}")
        return PlannedAction(
            symbol=pos.symbol, action="trail", new_stop=candidate,
            rationale=f"+{gain_in_R:.2f}R — trail stop ${pos.current_stop:.2f} -> ${candidate:.2f} "
                      f"(price ${pos.current_price:.2f} - {ATR_TRAIL_MULTIPLE}*ATR ${atr:.2f})",
        )

    return PlannedAction(
        symbol=pos.symbol, action="noop",
        rationale=f"{gain_in_R:+.2f}R unrealized — no level reached",
    )


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _cancel_open_legs(client: AlpacaClient, symbol: str, dry_run: bool) -> List[str]:
    """Cancel all open orders for `symbol`. Returns list of cancelled order ids."""
    legs = client.get_open_orders_for_symbol(symbol)
    cancelled = []
    for o in legs:
        oid = o.get("id")
        if not oid:
            continue
        if dry_run:
            print(f"    [dry-run] would cancel order {oid} ({o.get('side')} {o.get('order_type')} {o.get('qty')})")
        else:
            try:
                client.cancel_order(oid)
                print(f"    cancelled order {oid} ({o.get('side')} {o.get('order_type')})")
                cancelled.append(oid)
            except Exception as e:
                print(f"    cancel error for {oid}: {e}")
    return cancelled


def _submit_market_sell(client: AlpacaClient, symbol: str, qty: int, dry_run: bool) -> Optional[Dict]:
    if dry_run:
        print(f"    [dry-run] would submit market SELL {qty} {symbol}")
        return None
    try:
        order = client.submit_order(symbol=symbol, qty=qty, side="sell",
                                    order_type="market", time_in_force="day")
        print(f"    submitted market SELL {qty} {symbol}: id={order.get('id')}")
        return order
    except Exception as e:
        print(f"    market sell error: {e}")
        return None


def _submit_stop(client: AlpacaClient, symbol: str, qty: int, stop_price: float, dry_run: bool) -> Optional[Dict]:
    if dry_run:
        print(f"    [dry-run] would submit GTC STOP {qty} {symbol} @ ${stop_price:.2f}")
        return None
    try:
        order = client.submit_order(symbol=symbol, qty=qty, side="sell",
                                    order_type="stop", time_in_force="gtc",
                                    stop_price=round(stop_price, 2))
        print(f"    submitted GTC STOP {qty} {symbol} @ ${stop_price:.2f}: id={order.get('id')}")
        return order
    except Exception as e:
        print(f"    stop submit error: {e}")
        return None


def _execute(client: AlpacaClient, pos: ManagedPosition, plan: PlannedAction, dry_run: bool) -> bool:
    """Execute a plan and update the journal. Returns True if state changed."""
    if plan.action in ("noop", "skip_no_R"):
        return False

    print(f"  {pos.symbol}: {plan.rationale}")

    if plan.action == "trail":
        # Only need to replace the existing SL with a higher one.
        _cancel_open_legs(client, pos.symbol, dry_run)
        _submit_stop(client, pos.symbol, pos.alpaca_qty, plan.new_stop, dry_run)
        if not dry_run:
            _persist_journal_change(pos.journal_id, {"current_stop": plan.new_stop})
        return True

    if plan.action in ("scale_out_1r", "scale_out_2r"):
        # 1) Cancel all open OCO legs for this symbol
        # 2) Sell `qty` at market
        # 3) Submit a new GTC stop for (alpaca_qty - qty) at new_stop
        # 4) Update journal flags + current_stop
        _cancel_open_legs(client, pos.symbol, dry_run)
        _submit_market_sell(client, pos.symbol, plan.qty, dry_run)
        remaining = pos.alpaca_qty - plan.qty
        if remaining > 0 and plan.new_stop:
            _submit_stop(client, pos.symbol, remaining, plan.new_stop, dry_run)
        if not dry_run:
            flag = "scaled_25_at_1r" if plan.action == "scale_out_1r" else "scaled_25_at_2r"
            _persist_journal_change(pos.journal_id, {
                flag: True,
                "current_stop": plan.new_stop,
                "shares": remaining,
            })
        return True

    return False


# ---------------------------------------------------------------------------
# EOD entry point
# ---------------------------------------------------------------------------

def run_eod_pass(dry_run: bool = True, verbose: bool = True) -> Dict:
    """
    Walk every open Alpaca position, build a plan, and (unless dry_run) execute it.

    Returns a report dict with counts of actions taken / planned and per-symbol
    rationale lines. Suitable for inclusion in the EOD email.
    """
    client = AlpacaClient()
    try:
        positions = client.get_positions()
    except Exception as e:
        return {"available": False, "reason": f"could not fetch positions: {e}"}

    actions: List[PlannedAction] = []
    skips: List[str] = []
    for ap in positions:
        managed = _build_managed_position(client, ap)
        if managed is None:
            skips.append(f"{(ap.get('symbol') or '?')}: no matching journal entry")
            continue
        plan = plan_action(managed)
        if verbose and plan.action != "noop":
            mode = "DRY-RUN" if dry_run else "LIVE"
            print(f"[{mode}] position_manager — {managed.symbol}: {plan.action}")
        _execute(client, managed, plan, dry_run)
        actions.append(plan)

    n_scale = sum(1 for a in actions if a.action.startswith("scale_out"))
    n_trail = sum(1 for a in actions if a.action == "trail")
    n_noop = sum(1 for a in actions if a.action == "noop")
    n_skip_no_r = sum(1 for a in actions if a.action == "skip_no_R")

    return {
        "available": True,
        "dry_run": dry_run,
        "actions": [vars(a) for a in actions],
        "skips": skips,
        "scale_outs": n_scale,
        "trails": n_trail,
        "noops": n_noop,
        "skipped_no_R": n_skip_no_r,
    }


def report_block(report: Dict) -> str:
    if not report.get("available"):
        return f"POSITION MANAGER: not available ({report.get('reason', 'unknown')})"
    mode = "DRY-RUN" if report.get("dry_run") else "LIVE"
    lines = [f"POSITION MANAGER ({mode}):"]
    lines.append(f"  Scale-outs: {report['scale_outs']}  Trail moves: {report['trails']}  "
                 f"No-ops: {report['noops']}  Skipped (no R): {report['skipped_no_R']}")
    interesting = [a for a in report["actions"] if a["action"] not in ("noop", "skip_no_R")]
    for a in interesting:
        lines.append(f"  {a['symbol']}: {a['action']} — {a['rationale']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    live = "--live" in sys.argv
    r = run_eod_pass(dry_run=not live, verbose=True)
    print()
    print(report_block(r))
