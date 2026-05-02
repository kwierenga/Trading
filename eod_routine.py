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
from trade_journal import TradeJournal
from performance_tracker import get_turnover_stats


EST = pytz.timezone("America/New_York")
JOURNAL_PATH = Path("JOURNAL.md")


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


def build_email(account, positions, today_closed, open_with_status, turnover, today_label) -> tuple:
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

    today_closed = trades_closed_today()
    open_with_status = TradeJournal.get_open_trades_with_holding_status()
    turnover = get_turnover_stats(period_days=30)

    # Journal entry
    entry = build_journal_entry(equity, cash, len(positions), today_closed, open_with_status, today_label)
    append_to_journal(entry)

    # Email
    subject, body = build_email(account, positions, today_closed, open_with_status, turnover, today_label)
    send_email(subject, body)

    print("EOD routine complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
