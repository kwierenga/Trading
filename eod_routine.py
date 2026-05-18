"""
EOD Routine
Runs at 4:15pm EST via Windows Task Scheduler (weekdays only).
- Captures today's portfolio state (P&L, closed trades, open positions)
- Appends an [EOD] entry to JOURNAL.md (what happened + what we learned)
- Emails a 15-min review to klaaswierenga@gmail.com

Time budget: ~15 min for Klaas to read + add a manual reflection sentence.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytz

from alpaca_client import AlpacaClient
from email_notifier import send_email
from journal_reconcile import reconcile as reconcile_journal, report_block as reconcile_report_block
from position_manager import run_eod_pass as run_position_manager, report_block as position_report_block
from shadow_benchmark import update_shadow, report_block as shadow_report_block
from trade_journal import TradeJournal
from performance_tracker import get_turnover_stats, save_snapshot


# Position manager safety: starts in dry-run so we can observe the planned
# actions in the EOD email for a few days before going live. Flip to False
# (or set POSITION_MANAGER_LIVE=true in env) once dry-run output looks right
# on at least one full state transition (+1R or +2R event).
POSITION_MANAGER_DRY_RUN = os.getenv("POSITION_MANAGER_LIVE", "false").lower() != "true"


EST = pytz.timezone("America/New_York")
JOURNAL_PATH = Path("JOURNAL.md")
DAILY_REPORT_FILE = Path("daily_report.json")


def is_us_trading_day() -> bool:
    return datetime.now(EST).weekday() < 5


def append_to_journal(entry_text: str) -> None:
    if not JOURNAL_PATH.exists():
        print(f"  JOURNAL.md not found at {JOURNAL_PATH.resolve()} — skipping append")
        return

    text = JOURNAL_PATH.read_text(encoding="utf-8")
    marker = "---\n\n"
    parts = text.split(marker, 2)
    if len(parts) < 2:
        JOURNAL_PATH.write_text(text + "\n\n" + entry_text, encoding="utf-8")
        return

    new_text = parts[0] + marker + entry_text + "\n\n" + marker + parts[1]
    JOURNAL_PATH.write_text(new_text, encoding="utf-8")


def append_daily_report(report: dict) -> None:
    try:
        if DAILY_REPORT_FILE.exists():
            with open(DAILY_REPORT_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        else:
            history = []
    except (OSError, json.JSONDecodeError):
        history = []

    history.append(report)
    with open(DAILY_REPORT_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2, default=str)


def trades_closed_today() -> list:
    """Closed trades whose exit_time is within the current EST trading day."""
    all_trades = TradeJournal.load_trades()
    closed = [t for t in all_trades if t.get("status") == "closed" and t.get("exit_time")]

    today_start_est = datetime.now(EST).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_est.astimezone(timezone.utc)

    out = []
    for t in closed:
        try:
            exit_dt = datetime.fromisoformat(t["exit_time"])
            if exit_dt.tzinfo is None:
                exit_dt = exit_dt.replace(tzinfo=timezone.utc)
            if exit_dt >= today_start_utc:
                out.append(t)
        except (ValueError, KeyError):
            continue
    return out


def build_journal_entry(equity, cash, n_positions, today_closed, open_with_status, today_label) -> str:
    lines = [f"## [EOD] {today_label}\n"]

    # What happened
    lines.append("**What happened.** ")
    if today_closed:
        symbols = ", ".join(f"{t['symbol']} ({(t.get('pnl_pct') or 0)*100:+.1f}%)" for t in today_closed)
        lines.append(f"Closed: {symbols}. ")
    else:
        lines.append("No trades closed today. ")
    lines.append(
        f"End equity ${equity:,.0f}, cash ${cash:,.0f} ({cash / equity * 100:.0f}% of equity), {n_positions} open position(s). "
    )
    flagged = [t for t in open_with_status if t.get("holding_status", {}).get("status") == "approaching_LTCG"]
    if flagged:
        names = ", ".join(t["symbol"] for t in flagged)
        lines.append(f"LTCG approaching: {names}. ")
    lines.append("\n")

    # What we learned (manual prompt — Klaas fills this in during 15-min review)
    lines.append("**What we learned.** ")
    lines.append(
        "[Add 1-2 sentences during your 15-min review: what surprised you today, "
        "what hypothesis got confirmed or refuted, or what you noticed about the market.] "
    )

    return "".join(lines)


def build_email(account, positions, today_closed, open_with_status, turnover, today_label,
                reconcile_report=None, shadow_report=None, position_report=None) -> tuple:
    equity = float(account.get("equity", 0))
    cash = float(account.get("cash", 0))

    lines = [f"EOD review — {today_label} EST\n"]
    lines.append(f"Equity: ${equity:,.2f}   Cash: ${cash:,.0f} ({cash / equity * 100:.0f}%)   Positions: {len(positions)}")
    lines.append("")

    if today_closed:
        lines.append(f"CLOSED TODAY ({len(today_closed)}):")
        for t in today_closed:
            pnl_pct = (t.get("pnl_pct") or 0) * 100
            pnl_dollars = t.get("pnl") or 0
            duration_h = (t.get("duration_minutes") or 0) / 60
            lines.append(
                f"  {t['symbol']:<6} {pnl_pct:+.1f}%  (${pnl_dollars:+,.0f})  "
                f"in {duration_h:.0f}h  via {t.get('reason_exit', '?')}"
            )
        lines.append("")

    if positions:
        lines.append(f"OPEN POSITIONS ({len(positions)}):")
        for p in positions:
            sym = p.get("symbol", "?")
            unrealized_pct = float(p.get("unrealized_plpc", 0))
            value = float(p.get("market_value", 0))

            # Holding status from journal (if logged via our flow)
            tag = ""
            for ot in open_with_status:
                if ot["symbol"] == sym:
                    hs = ot.get("holding_status", {})
                    if hs.get("status") == "LTCG":
                        tag = "  [LTCG eligible]"
                    elif hs.get("status") == "approaching_LTCG":
                        tag = f"  [LTCG in {hs.get('days_to_LTCG')}d]"
                    else:
                        tag = f"  [held {hs.get('days_held', 0)}d]"
                    break

            lines.append(f"  {sym:<6} ${value:>9,.0f}   {unrealized_pct:+.1%} unrealized{tag}")
        lines.append("")

    if turnover["trades"] > 0:
        lines.append(
            f"TURNOVER (last 30d): {turnover['trades']} trade(s), avg holding {turnover['avg_holding_days']:.0f}d"
        )
        if turnover["short_term_gains"] > 0:
            lines.append(
                f"  Tax drag (short-term gains vs all-LTCG counterfactual): ${turnover['tax_drag']:,.0f}"
            )
        lines.append("")

    lines.append("→ Open JOURNAL.md and add 1-2 sentences under 'What we learned' if anything surprised you today.")

    if shadow_report:
        lines.append("")
        lines.append(shadow_report_block(shadow_report))

    if position_report:
        lines.append("")
        lines.append(position_report_block(position_report))

    if reconcile_report:
        lines.append("")
        lines.append(reconcile_report_block(reconcile_report))

    body = "\n".join(lines)
    subject = (
        f"[Trading EOD] {today_label} — "
        + (f"{len(today_closed)} closed, " if today_closed else "")
        + f"{len(positions)} open"
    )
    return subject, body


def main() -> int:
    # Match morning_routine: force UTF-8 stdout so → and other non-ASCII
    # characters don't crash print() under Task Scheduler / cp1252 console.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    today = datetime.now(EST)
    today_label = today.strftime("%Y-%m-%d %A")
    print(f"EOD routine starting at {today.isoformat()}")

    force = os.getenv("FORCE_RUN", "false").lower() == "true"
    if not is_us_trading_day() and not force:
        print(f"  {today_label} is a weekend — skipping. (Set FORCE_RUN=true to override.)")
        return 0
    if force and not is_us_trading_day():
        print(f"  {today_label} is a weekend — running anyway because FORCE_RUN=true.")

    try:
        client = AlpacaClient()
        account = client.get_account()
        positions = client.get_positions()
    except Exception as e:
        print(f"  Could not fetch account/positions: {e}")
        send_email(
            f"[Trading EOD] {today_label} — FAILED",
            f"Could not fetch Alpaca account state at {today.isoformat()}.\nError: {e}",
        )
        return 1

    equity = float(account["equity"])
    cash = float(account["cash"])

    # Reconcile journal against Alpaca's filled-orders history. Must run before
    # `trades_closed_today` / `get_open_trades_with_holding_status` so they read
    # the corrected state. Defensive: failure here is logged but not fatal —
    # the rest of EOD still runs.
    try:
        reconcile_report = reconcile_journal(write=True, verbose=False)
    except Exception as e:
        print(f"  Journal reconciliation failed: {e}")
        reconcile_report = {}

    today_closed = trades_closed_today()
    open_with_status = TradeJournal.get_open_trades_with_holding_status()
    turnover = get_turnover_stats(period_days=30)

    # Shadow benchmark (SPY 70% + USMV 30%) — measurement, not edge.
    try:
        shadow_report = update_shadow(strategy_equity=equity)
    except Exception as e:
        print(f"  Shadow benchmark update failed: {e}")
        shadow_report = {"available": False, "reason": str(e)}

    # Position manager (trail stops + scale-out at +1R/+2R). Dry-run by default.
    try:
        position_report = run_position_manager(dry_run=POSITION_MANAGER_DRY_RUN, verbose=False)
    except Exception as e:
        print(f"  Position manager pass failed: {e}")
        position_report = {"available": False, "reason": str(e)}

    # Journal entry
    entry = build_journal_entry(equity, cash, len(positions), today_closed, open_with_status, today_label)
    append_to_journal(entry)

    # Email
    subject, body = build_email(account, positions, today_closed, open_with_status, turnover, today_label,
                                reconcile_report=reconcile_report, shadow_report=shadow_report,
                                position_report=position_report)
    send_email(subject, body)

    # Persist daily report and portfolio snapshot for tracking. Reuse the
    # account/positions already fetched above rather than re-querying Alpaca
    # via get_portfolio_snapshot() (which spins up a second AlpacaClient).
    try:
        report = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'date': today.strftime('%Y-%m-%d'),
            'equity': equity,
            'cash': cash,
            'positions': len(positions),
            'closed_trades': [
                {
                    'symbol': t['symbol'],
                    'pnl': t.get('pnl', 0),
                    'pnl_pct': t.get('pnl_pct', 0),
                    'reason_exit': t.get('reason_exit', ''),
                    'duration_minutes': t.get('duration_minutes', 0),
                }
                for t in today_closed
            ],
            'turnover': turnover,
            'shadow_report': shadow_report,
            'position_manager': position_report,
        }
        snapshot = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'account': {
                'equity': equity,
                'buying_power': float(account.get('buying_power', 0)),
                'cash': cash,
                'status': account.get('status', ''),
            },
            'positions': [
                {
                    'symbol': p['symbol'],
                    'qty': float(p['qty']),
                    'avg_entry_price': float(p['avg_entry_price']),
                    'current_price': float(p.get('current_price', 0)),
                    'unrealized_pl': float(p['unrealized_pl']),
                }
                for p in positions
            ],
        }
        append_daily_report(report)
        save_snapshot(snapshot)
    except (KeyError, ValueError, TypeError, OSError) as e:
        print(f"  Failed to persist daily report/snapshot: {e}")

    print("EOD routine complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
