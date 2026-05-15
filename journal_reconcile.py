"""
Journal reconciliation against Alpaca (RECOMMENDATIONS_top10 #7).

The `trade_journal.json` had drifted out of sync with reality:
  - All entries marked status=open even when positions were stopped out
  - Duplicate entries (same symbol+date repeated 4x in May 2 batch)
  - Real fills not in the journal at all (UBER 2026-05-12)

The fix is to treat Alpaca's filled-orders history as the source of truth,
rebuild the journal from it, and preserve `rationale` text from the existing
journal where we can match entries by (symbol, date, price).

Result is consumed by `TradeJournal.get_statistics()` which feeds the
HISTORICAL PERFORMANCE block in Claude's daily prompt — so accurate stats
here are what makes every other strategy improvement compound.

Idempotent: running twice produces the same output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

from alpaca_client import AlpacaClient
from trade_journal import TradeJournal


JOURNAL_FILE = Path(TradeJournal.FILE)


@dataclass
class Lot:
    """An open buy lot waiting to be closed by a future sell fill."""
    symbol: str
    qty: int
    entry_price: float
    entry_time: str            # ISO8601 UTC
    order_id: str
    rationale: str = ""
    source: str = "alpaca_reconciled"


def _filled_orders_sorted(client: AlpacaClient) -> List[Dict]:
    """All filled Alpaca orders sorted by filled_at ascending."""
    orders = client.get_orders(status="all")
    filled = [o for o in orders if o.get("status") == "filled"]
    return sorted(filled, key=lambda o: o.get("filled_at") or "")


def _match_rationale(existing: List[Dict], symbol: str, entry_price: float,
                     entry_iso: str, qty: int) -> tuple[str, str]:
    """
    Try to find an existing journal entry that matches this fill by (symbol,
    date within 1d, price within 2%). Return (rationale, source).
    """
    try:
        target_dt = datetime.fromisoformat(entry_iso.replace("Z", "+00:00"))
    except ValueError:
        target_dt = None

    for e in existing:
        if (e.get("symbol") or "").upper() != symbol.upper():
            continue
        try:
            e_dt = datetime.fromisoformat(e.get("entry_time", ""))
        except (ValueError, TypeError):
            continue
        if target_dt and abs((e_dt - target_dt).total_seconds()) > 86400 * 2:
            continue
        e_price = float(e.get("entry_price") or 0)
        if e_price > 0 and abs(e_price - entry_price) / entry_price > 0.03:
            continue
        return (e.get("rationale") or "", e.get("source") or "claude")

    return ("", "alpaca_reconciled")


def _build_clean_journal(fills: List[Dict], existing: List[Dict]) -> tuple[List[Dict], Dict]:
    """
    Walk fills chronologically; build closed round-trips (FIFO) and open lots.
    Returns (rebuilt_entries, report).
    """
    open_lots_by_symbol: Dict[str, List[Lot]] = {}
    rebuilt: List[Dict] = []
    next_id = 1
    orphan_sells: List[Dict] = []
    n_buys = 0
    n_sells_matched = 0

    for o in fills:
        sym = (o.get("symbol") or "").upper()
        side = (o.get("side") or "").lower()
        try:
            qty = int(float(o.get("filled_qty") or 0))
            price = float(o.get("filled_avg_price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price <= 0 or not sym:
            continue
        ts = o.get("filled_at") or ""
        order_type = o.get("order_type") or ""

        if side == "buy":
            n_buys += 1
            rationale, source = _match_rationale(existing, sym, price, ts, qty)
            lot = Lot(
                symbol=sym,
                qty=qty,
                entry_price=price,
                entry_time=ts,
                order_id=o.get("id", ""),
                rationale=rationale,
                source=source if source != "alpaca_reconciled" else "alpaca_reconciled",
            )
            open_lots_by_symbol.setdefault(sym, []).append(lot)

        elif side == "sell":
            remaining = qty
            lots = open_lots_by_symbol.get(sym, [])
            if not lots:
                orphan_sells.append({"symbol": sym, "qty": qty, "price": price, "time": ts})
                continue
            # FIFO close
            while remaining > 0 and lots:
                head = lots[0]
                close_qty = min(remaining, head.qty)
                n_sells_matched += 1

                reason = "stop_hit" if "stop" in order_type else (
                    "target_hit" if order_type == "limit" else "manual"
                )
                pnl_per_share = price - head.entry_price
                pnl = pnl_per_share * close_qty
                pnl_pct = pnl_per_share / head.entry_price if head.entry_price > 0 else 0.0

                duration_min = None
                try:
                    e_dt = datetime.fromisoformat(head.entry_time.replace("Z", "+00:00"))
                    x_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    duration_min = (x_dt - e_dt).total_seconds() / 60
                except (ValueError, TypeError):
                    pass

                rebuilt.append({
                    "id": next_id,
                    "symbol": sym,
                    "shares": close_qty,
                    "entry_price": head.entry_price,
                    "entry_time": head.entry_time,
                    "stop_loss": None,
                    "target_price": None,
                    "rationale": head.rationale,
                    "source": head.source,
                    "status": "closed",
                    "exit_price": price,
                    "exit_time": ts,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 6),
                    "duration_minutes": duration_min,
                    "reason_exit": reason,
                })
                next_id += 1

                head.qty -= close_qty
                remaining -= close_qty
                if head.qty <= 0:
                    lots.pop(0)

            if remaining > 0:
                orphan_sells.append({"symbol": sym, "qty": remaining, "price": price, "time": ts})

    # Whatever's left in open_lots_by_symbol becomes the current open journal entries
    for sym, lots in open_lots_by_symbol.items():
        for lot in lots:
            rebuilt.append({
                "id": next_id,
                "symbol": lot.symbol,
                "shares": lot.qty,
                "entry_price": lot.entry_price,
                "entry_time": lot.entry_time,
                "stop_loss": None,
                "target_price": None,
                "rationale": lot.rationale,
                "source": lot.source,
                "status": "open",
                "exit_price": None,
                "exit_time": None,
                "pnl": None,
                "pnl_pct": None,
                "duration_minutes": None,
                "reason_exit": None,
            })
            next_id += 1

    report = {
        "buys_processed": n_buys,
        "sells_matched": n_sells_matched,
        "orphan_sells": orphan_sells,
        "closed_trips": sum(1 for e in rebuilt if e["status"] == "closed"),
        "open_lots": sum(1 for e in rebuilt if e["status"] == "open"),
    }
    return rebuilt, report


def reconcile(write: bool = True, verbose: bool = True) -> Dict:
    """
    Rebuild the trade journal from Alpaca's filled-orders history.

    Returns a report dict (see _build_clean_journal). When `write=True`, writes
    the new state to trade_journal.json after backing up the previous file to
    trade_journal.json.bak (single rolling backup).
    """
    client = AlpacaClient()
    fills = _filled_orders_sorted(client)

    existing = []
    if JOURNAL_FILE.exists():
        try:
            existing = json.loads(JOURNAL_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = []

    rebuilt, report = _build_clean_journal(fills, existing)

    if verbose:
        print("=" * 60)
        print("JOURNAL RECONCILIATION")
        print("=" * 60)
        print(f"  Alpaca filled orders processed: {report['buys_processed']} buys + "
              f"{report['sells_matched']} sells matched")
        print(f"  Existing journal entries: {len(existing)} (rebuilt to {len(rebuilt)})")
        print(f"  Closed round-trips: {report['closed_trips']}")
        print(f"  Open lots: {report['open_lots']}")
        if report["orphan_sells"]:
            print(f"  Orphan sells (no matching open lot): {len(report['orphan_sells'])}")
            for s in report["orphan_sells"]:
                print(f"    {s['time'][:10]} sell {s['qty']} {s['symbol']} @ ${s['price']:.2f} "
                      f"(no prior buy in Alpaca history — pre-existing position?)")

        closed = [e for e in rebuilt if e["status"] == "closed"]
        if closed:
            print("\n  Closed round-trips:")
            for e in closed:
                print(f"    {e['symbol']:<6} {e['shares']} @ ${e['entry_price']:.2f} -> "
                      f"${e['exit_price']:.2f} = {e['pnl_pct']:+.2%} ({e['reason_exit']})")

        open_ = [e for e in rebuilt if e["status"] == "open"]
        if open_:
            print("\n  Open lots:")
            for e in open_:
                print(f"    {e['symbol']:<6} {e['shares']} @ ${e['entry_price']:.2f} "
                      f"({e['entry_time'][:10]}, source={e['source']})")
        print("=" * 60)

    if write:
        if JOURNAL_FILE.exists():
            backup = JOURNAL_FILE.with_suffix(".json.bak")
            backup.write_text(JOURNAL_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        JOURNAL_FILE.write_text(json.dumps(rebuilt, indent=2, default=str), encoding="utf-8")
        if verbose:
            print(f"\n  Wrote {len(rebuilt)} reconciled entries to {JOURNAL_FILE}")
            print(f"  Backup of pre-reconciliation state at {JOURNAL_FILE.with_suffix('.json.bak')}")

    return report


def report_block(report: Dict) -> str:
    """Render a compact reconciliation block for the EOD email."""
    lines = ["JOURNAL RECONCILIATION:"]
    lines.append(f"  Closed round-trips: {report.get('closed_trips', 0)}")
    lines.append(f"  Open lots: {report.get('open_lots', 0)}")
    if report.get("orphan_sells"):
        lines.append(f"  Orphan sells: {len(report['orphan_sells'])} (see logs)")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    write = "--dry-run" not in sys.argv
    if not write:
        print("DRY RUN — will not write trade_journal.json")
    reconcile(write=write, verbose=True)
