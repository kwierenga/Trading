"""
Execution ledger (enhancements #1-#3, 2026-06-06).

Records ONE row per trade candidate every time execute_strategy.py runs:
why each candidate was PLACED or SKIPPED, and — for placed orders — the Alpaca
order id so fill status can be reconciled later.

Why this exists: before it, a candidate that didn't trade left no durable trace
except a line in the GitHub Actions log Klaas never reads. trade_journal.json
only ever recorded *placed* trades (and after EOD reconciliation, only fills),
so the daily question "why didn't X trade today?" had no answer. This ledger
is that answer, and `notify_execute.py` surfaces it in the daily email.

It also closes the "submitted == executed" conflation: an entry is a GTC limit
order that may never fill. `reconcile_and_persist()` queries Alpaca for the
real order status so the email can say "working (unfilled)" instead of
implying a fill happened.

File format: a flat JSON list of rows (`execution_ledger.json`), newest runs
appended. Idempotent per UTC date — re-running execute on the same date
replaces that date's rows rather than duplicating them, so the cron-job.org
backup trigger can't double-log.

Row schema:
  {
    "date": "2026-06-06",      # UTC date of the run
    "timestamp": "...Z",       # UTC timestamp the row was written
    "symbol": "MSFT",
    "decision": "placed" | "skipped",
    "gate": "submitted" | "gross" | "conviction" | "concentration" | ...,
    "detail": "human-readable reason",
    "shares": 24,              # placed only
    "entry_price": 429.12,     # placed only
    "order_id": "abc-123",     # placed only; None if the submit call failed
    "fill_status": "unknown"   # enriched by reconcile_and_persist()
  }
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

FILE = "execution_ledger.json"


# --- Alpaca order-status -> short human label ------------------------------
# Statuses that mean "still resting, not yet (fully) filled" all collapse to
# "working (unfilled)" — that is the honest state of a below-market GTC limit
# seconds after submit, which is when notify_execute first reports.
_WORKING = {
    "new", "accepted", "pending_new", "held", "accepted_for_bidding",
    "calculated", "suspended", "pending_replace", "replaced", "done_for_day",
}


def _status_label(order: Dict) -> str:
    status = (order.get("status") or "").lower()
    if status == "filled":
        return "filled"
    if status == "partially_filled":
        fq = order.get("filled_qty") or "?"
        q = order.get("qty") or "?"
        return f"partial ({fq}/{q})"
    if status in ("canceled", "expired", "rejected", "pending_cancel", "stopped"):
        return status
    if status in _WORKING:
        return "working (unfilled)"
    return status or "unknown"


def load(path: str = FILE) -> List[Dict]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _write(rows: List[Dict], path: str = FILE) -> None:
    Path(path).write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")


def rows_for_date(date_utc: str, path: str = FILE) -> List[Dict]:
    return [r for r in load(path) if r.get("date") == date_utc]


class RunLedger:
    """
    Accumulates rows for a single execute_strategy run, then writes them in one
    idempotent commit. Instantiate at the top of the run, call placed()/skipped()
    as decisions are made, and commit() at the end.
    """

    def __init__(self, date_utc: Optional[str] = None, path: str = FILE):
        self.date = date_utc or datetime.now(timezone.utc).date().isoformat()
        self.path = path
        self.rows: List[Dict] = []

    def _row(self, symbol: str, decision: str, gate: str, detail: str, **extra) -> Dict:
        row = {
            "date": self.date,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "decision": decision,
            "gate": gate,
            "detail": detail,
        }
        row.update(extra)
        self.rows.append(row)
        return row

    def skipped(self, symbol: str, gate: str, detail: str) -> Dict:
        return self._row(symbol, "skipped", gate, detail)

    def placed(self, symbol: str, shares: int, entry_price: float,
               order_id: Optional[str], detail: str = "") -> Dict:
        return self._row(
            symbol, "placed", "submitted", detail,
            shares=shares, entry_price=entry_price,
            order_id=order_id, fill_status="unknown",
        )

    def commit(self) -> None:
        """
        Merge this run's rows into the on-disk ledger.

        Other dates are untouched. For this date, prior `placed` rows are
        PRESERVED — they record orders that really went out to Alpaca, and a
        later same-day run must not erase that audit trail (on 2026-06-11 a
        manual re-dispatch overwrote three placed rows with `skipped`, losing
        the order ids the EOD reconcile needed). Prior `skipped` rows are
        replaced by this run's verdicts, which keeps the original
        no-double-logging intent for re-runs.
        """
        existing = load(self.path)
        others = [r for r in existing if r.get("date") != self.date]
        kept_placed = [
            r for r in existing
            if r.get("date") == self.date and r.get("decision") == "placed"
        ]
        _write(others + kept_placed + self.rows, self.path)


def reconcile_and_persist(date_utc: str, path: str = FILE, client=None) -> List[Dict]:
    """
    Update fill_status on the given date's PLACED rows from live Alpaca order
    state, write the result back, and return that date's rows.

    Best-effort: if the Alpaca call fails, rows are returned unchanged (and not
    rewritten). AlpacaClient is imported lazily so this module stays importable
    without credentials (e.g. to inspect the ledger locally).
    """
    all_rows = load(path)
    if client is None:
        try:
            from alpaca_client import AlpacaClient
            client = AlpacaClient()
        except Exception:
            return [r for r in all_rows if r.get("date") == date_utc]

    try:
        orders = client.get_orders(status="all")
    except Exception:
        return [r for r in all_rows if r.get("date") == date_utc]

    by_id = {o.get("id"): o for o in orders}
    changed = False
    for r in all_rows:
        if r.get("date") != date_utc or r.get("decision") != "placed":
            continue
        order = by_id.get(r.get("order_id"))
        if order:
            new_label = _status_label(order)
            if r.get("fill_status") != new_label:
                r["fill_status"] = new_label
                changed = True

    if changed:
        _write(all_rows, path)
    return [r for r in all_rows if r.get("date") == date_utc]


def summary_counts(rows: List[Dict]) -> Dict[str, int]:
    placed = [r for r in rows if r.get("decision") == "placed"]
    skipped = [r for r in rows if r.get("decision") == "skipped"]
    filled = sum(1 for r in placed if r.get("fill_status") == "filled")
    return {
        "placed": len(placed),
        "filled": filled,
        "working": len(placed) - filled,
        "skipped": len(skipped),
    }


def email_block(rows: List[Dict]) -> str:
    """Render the ledger for the daily execute email — the 'why' for every name."""
    if not rows:
        return "EXECUTION LEDGER: no candidates evaluated (empty plan)."

    placed = [r for r in rows if r.get("decision") == "placed"]
    skipped = [r for r in rows if r.get("decision") == "skipped"]

    lines = [f"EXECUTION LEDGER ({rows[0].get('date', '?')}):"]

    lines.append(f"  PLACED ({len(placed)}):")
    if placed:
        for r in placed:
            oid = (r.get("order_id") or "")[:8]
            lines.append(
                f"    {r.get('symbol', '?'):<6} {int(r.get('shares', 0)):>4d} sh "
                f"@ ${float(r.get('entry_price', 0)):>8.2f}  -> "
                f"{r.get('fill_status', 'unknown')}"
                + (f"  [id {oid}]" if oid else "")
            )
    else:
        lines.append("    (none)")

    lines.append(f"  SKIPPED ({len(skipped)}):")
    if skipped:
        for r in skipped:
            lines.append(
                f"    {r.get('symbol', '?'):<6} {r.get('gate', '?')} — {r.get('detail', '')}"
            )
    else:
        lines.append("    (none)")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now(timezone.utc).date().isoformat()
    print(email_block(rows_for_date(date)))
