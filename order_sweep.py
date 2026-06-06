"""
Stale-entry-order sweep (enhancement #4, 2026-06-06).

The execute path submits entries as GTC limit orders priced at the AM limit.
Because the strategy deliberately buys basing / below-market names, those
limits frequently never fill — and nothing expired them. They rested
indefinitely, phantom-committing buying power and quietly accumulating. The
old code's only mitigation was a comment telling Klaas to cancel them by hand.

This module, run at EOD, finds open UNFILLED entry (buy) orders older than a
threshold and cancels them, and reports every resting entry so they are
visible in the EOD email.

Safety: defaults to DRY-RUN (report only), mirroring position_manager's
observe-before-mutate convention. Set ORDER_SWEEP_LIVE=true in the eod
environment to actually cancel. Repricing (cancel + resubmit at a fresh limit)
is intentionally NOT done here — that's a re-entry decision that belongs to the
next morning's screen, not an EOD janitor.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

# Orders in these states are still resting / not (fully) filled.
_OPEN_STATES = {
    "new", "accepted", "pending_new", "held", "accepted_for_bidding",
    "partially_filled", "calculated", "suspended",
}


def _order_age_hours(order: Dict, now: Optional[datetime] = None) -> Optional[float]:
    now = now or datetime.now(timezone.utc)
    ts = order.get("submitted_at") or order.get("created_at") or ""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 3600.0


def _is_unfilled_entry(order: Dict) -> bool:
    """A resting BUY order that has not (fully) filled — i.e. an entry leg."""
    if (order.get("side") or "").lower() != "buy":
        return False
    if (order.get("status") or "").lower() not in _OPEN_STATES:
        return False
    try:
        filled = float(order.get("filled_qty") or 0)
        qty = float(order.get("qty") or 0)
    except (TypeError, ValueError):
        filled, qty = 0.0, 0.0
    return qty <= 0 or filled < qty


def sweep(dry_run: bool = True, max_age_hours: float = 18.0,
          verbose: bool = False, client=None) -> Dict:
    """
    Cancel resting unfilled entry (buy) orders older than max_age_hours.

    Returns a report dict:
      available, dry_run, max_age_hours,
      working: [{symbol, qty, limit_price, age_hours, order_id, status}],
      canceled: [...same shape...],
      errors: [{order_id, error}]
    """
    if client is None:
        from alpaca_client import AlpacaClient
        client = AlpacaClient()

    try:
        orders = client.get_orders(status="open")
    except Exception as e:
        return {"available": False, "reason": str(e), "dry_run": dry_run}

    now = datetime.now(timezone.utc)
    working: List[Dict] = []
    canceled: List[Dict] = []
    errors: List[Dict] = []

    for o in orders:
        if not _is_unfilled_entry(o):
            continue
        age = _order_age_hours(o, now)
        info = {
            "symbol": (o.get("symbol") or "?").upper(),
            "qty": o.get("qty"),
            "limit_price": o.get("limit_price"),
            "age_hours": round(age, 1) if age is not None else None,
            "order_id": o.get("id"),
            "status": o.get("status"),
        }
        working.append(info)

        if age is not None and age >= max_age_hours:
            if dry_run:
                if verbose:
                    print(f"  [DRY RUN] would cancel stale entry {info['symbol']} "
                          f"(age {info['age_hours']}h) id={info['order_id']}")
                canceled.append({**info, "action": "would_cancel"})
            else:
                try:
                    client.cancel_order(o.get("id"))
                    canceled.append({**info, "action": "canceled"})
                    if verbose:
                        print(f"  Canceled stale entry {info['symbol']} "
                              f"(age {info['age_hours']}h) id={info['order_id']}")
                except Exception as e:
                    errors.append({"order_id": o.get("id"), "error": str(e)})
                    if verbose:
                        print(f"  Cancel FAILED for {info['symbol']} id={info['order_id']}: {e}")

    return {
        "available": True,
        "dry_run": dry_run,
        "max_age_hours": max_age_hours,
        "working": working,
        "canceled": canceled,
        "errors": errors,
    }


def report_block(report: Dict) -> str:
    """Compact EOD-email block for the entry-order sweep."""
    if not report or not report.get("available"):
        reason = (report or {}).get("reason", "not run")
        return f"ENTRY-ORDER SWEEP: unavailable ({reason})"

    working = report.get("working", [])
    canceled = report.get("canceled", [])
    mode = "DRY-RUN (report only)" if report.get("dry_run") else "LIVE"

    lines = [f"ENTRY-ORDER SWEEP [{mode}, stale > {report.get('max_age_hours')}h]:"]
    if not working:
        lines.append("  No resting entry orders.")
        return "\n".join(lines)

    lines.append(f"  Resting entry orders ({len(working)}):")
    for w in working:
        lp = w.get("limit_price")
        lp_s = f"${float(lp):.2f}" if lp not in (None, "") else "?"
        age = w.get("age_hours")
        age_s = f"{age:.0f}h" if age is not None else "?"
        lines.append(f"    {w['symbol']:<6} {w.get('qty', '?')} @ {lp_s}  resting {age_s}")

    if canceled:
        verb = "Would cancel" if report.get("dry_run") else "Canceled"
        names = ", ".join(c["symbol"] for c in canceled)
        lines.append(f"  {verb} (stale): {names}")
    if report.get("errors"):
        lines.append(f"  Cancel errors: {len(report['errors'])} (see logs)")
    if report.get("dry_run") and canceled:
        lines.append("  (set ORDER_SWEEP_LIVE=true to actually cancel)")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    live = "--live" in sys.argv
    rep = sweep(dry_run=not live, verbose=True)
    print(report_block(rep))
