"""
Weekly Review
Runs Sunday afternoon via GitHub Actions (.github/workflows/weekly_review.yml).
- Captures the week's closed trades, open positions, AM/EOD journal entries
- Asks Claude to synthesize: what worked, what didn't, what's puzzling, what to watch
- Emails the review to Klaas
- Appends a [WEEK] entry to JOURNAL.md

Time budget: ~15 min for Klaas to read and add 2-3 sentences of reflection.

Designed to support the active learning loop (see memory/trading_learning_loop.md):
the value isn't the auto-generated summary, it's the prompt for Klaas to actually
think about the week's trades before they fade into the aggregate P&L.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pytz
import anthropic

from alpaca_client import AlpacaClient
from email_notifier import send_email
from trade_journal import TradeJournal


EST = pytz.timezone("America/New_York")
JOURNAL_PATH = Path("JOURNAL.md")
WEEKLY_SUMMARY_FILE = Path("weekly_summary.json")


def journal_entries_in_window(start: datetime, end: datetime) -> str:
    """Pull JOURNAL.md entries from [start, end] (UTC dates) as a single string."""
    if not JOURNAL_PATH.exists():
        return "(no JOURNAL.md found)"
    text = JOURNAL_PATH.read_text(encoding="utf-8")
    # Crude but good enough: keep only entries whose date string falls in window
    lines = text.splitlines()
    keep, current_keep = [], False
    for line in lines:
        if line.startswith("## "):
            # Try to parse a date from the header
            current_keep = False
            for token in line.split():
                try:
                    d = datetime.strptime(token, "%Y-%m-%d").date()
                    if start.date() <= d <= end.date():
                        current_keep = True
                    break
                except ValueError:
                    continue
        if current_keep:
            keep.append(line)
    return "\n".join(keep) if keep else "(no entries in window)"


def closed_trades_in_window(start: datetime, end: datetime) -> List[dict]:
    trades = TradeJournal.load_trades()
    out = []
    for t in trades:
        if t.get("status") != "closed":
            continue
        exit_time = t.get("exit_time")
        if not exit_time:
            continue
        try:
            ts = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        if start <= ts <= end:
            out.append(t)
    return out


def build_summary_prompt(
    week_start: datetime,
    week_end: datetime,
    equity: float,
    cash: float,
    positions: list,
    closed_trades: List[dict],
    journal_text: str,
) -> str:
    """Build a prompt asking Claude to synthesize the week's events."""
    # Compact closed-trade lines
    closed_lines = []
    for t in closed_trades:
        pnl_pct = (t.get("pnl_pct") or 0) * 100
        pnl_d = t.get("pnl") or 0
        days = (t.get("duration_minutes") or 0) / (60 * 24)
        closed_lines.append(
            f"  {t['symbol']:<6} {pnl_pct:+6.1f}%  (${pnl_d:+,.0f})  "
            f"held {days:.1f}d  exit via {t.get('reason_exit', '?')}"
        )
    closed_block = "\n".join(closed_lines) if closed_lines else "  (no trades closed this week)"

    # Compact open-position lines
    open_lines = []
    for p in positions:
        sym = p.get("symbol", "?")
        unrealized = float(p.get("unrealized_plpc", 0))
        value = float(p.get("market_value", 0))
        open_lines.append(f"  {sym:<6} ${value:>9,.0f}  {unrealized:+.1%} unrealized")
    open_block = "\n".join(open_lines) if open_lines else "  (no open positions)"

    return f"""
You are reviewing a week of paper trading for Klaas, a thoughtful investor running
a concentrated quality+momentum strategy. Your job: synthesize what happened, surface
patterns, ask the reflective questions that turn this week into learning.

DO NOT recommend trades or rationalize losses. The point is honest reflection.
Be direct, specific, and brief. Three short paragraphs maximum.

WEEK: {week_start.strftime('%Y-%m-%d')} through {week_end.strftime('%Y-%m-%d')}

PORTFOLIO STATE (end of week):
  Equity: ${equity:,.2f}
  Cash:   ${cash:,.2f}
  Open positions: {len(positions)}

CLOSED THIS WEEK ({len(closed_trades)}):
{closed_block}

OPEN POSITIONS:
{open_block}

JOURNAL ENTRIES THIS WEEK:
{journal_text[:6000]}

Produce three short paragraphs:
1. **What worked / what didn't** — be specific about which trades, which rules, which patterns. Don't generalize from N=1.
2. **What's puzzling or worth watching** — anomalies, repeated patterns, things the daily emails don't capture.
3. **Reflective prompts for Klaas** — 2 or 3 specific questions tied to this week's events (not generic "how do you feel"). End each with a `?`.

Tone: a careful colleague debriefing, not a coach. No emoji, no fluff.
"""


def append_to_journal(week_label: str, summary: str) -> None:
    if not JOURNAL_PATH.exists():
        return
    text = JOURNAL_PATH.read_text(encoding="utf-8")
    marker = "---\n\n"
    parts = text.split(marker, 2)
    entry = f"## [WEEK] {week_label}\n\n{summary}\n"
    if len(parts) < 2:
        JOURNAL_PATH.write_text(text + "\n\n" + entry, encoding="utf-8")
        return
    new_text = parts[0] + marker + entry + "\n\n" + marker + parts[1]
    JOURNAL_PATH.write_text(new_text, encoding="utf-8")


def append_weekly_summary(summary: dict) -> None:
    try:
        if WEEKLY_SUMMARY_FILE.exists():
            with open(WEEKLY_SUMMARY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        else:
            history = []
    except (OSError, json.JSONDecodeError):
        history = []

    history.append(summary)
    with open(WEEKLY_SUMMARY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2, default=str)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    now = datetime.now(EST)
    # Cover the trading week that just ended: Mon 00:00 EST through Sun 23:59 EST.
    days_since_monday = (now.weekday() - 0) % 7  # 0 if Mon, 6 if Sun
    week_end = now.replace(hour=23, minute=59, second=59)
    week_start = (week_end - timedelta(days=days_since_monday + 6)).replace(hour=0, minute=0, second=0)
    # Convert window to UTC for trade-journal comparison (entries are stored UTC)
    week_start_utc = week_start.astimezone(timezone.utc)
    week_end_utc = week_end.astimezone(timezone.utc)
    week_label = f"{week_start.strftime('%Y-%m-%d')} → {week_end.strftime('%Y-%m-%d')}"

    print(f"Weekly review for {week_label}")

    try:
        client = AlpacaClient()
        account = client.get_account()
        positions = client.get_positions()
        equity = float(account["equity"])
        cash = float(account["cash"])
    except Exception as e:
        print(f"  Could not fetch account: {e}")
        equity, cash, positions = 0.0, 0.0, []

    closed = closed_trades_in_window(week_start_utc, week_end_utc)
    journal_text = journal_entries_in_window(week_start, week_end)

    print(f"  Closed trades this week: {len(closed)}")
    print(f"  Open positions: {len(positions)}")
    print(f"  Journal text length: {len(journal_text)} chars")

    # Ask Claude to synthesize
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("  ANTHROPIC_API_KEY missing — skipping synthesis, sending mechanical summary only.")
        synthesis = "(synthesis unavailable — set ANTHROPIC_API_KEY to enable)"
    else:
        try:
            prompt = build_summary_prompt(
                week_start, week_end, equity, cash, positions, closed, journal_text
            )
            anth = anthropic.Anthropic()
            r = anth.messages.create(
                model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            synthesis = next((b.text for b in r.content if b.type == "text"), "")
        except (anthropic.APIError, ValueError) as e:
            print(f"  Claude synthesis failed: {e}")
            synthesis = f"(synthesis failed: {e})"

    # Build email body
    pnl_total = sum(t.get("pnl") or 0 for t in closed)
    wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
    losses = sum(1 for t in closed if (t.get("pnl") or 0) < 0)

    email_lines = [
        f"Weekly review — {week_label}",
        "",
        f"Equity: ${equity:,.2f}   Cash: ${cash:,.0f}   Positions: {len(positions)}",
        f"Closed this week: {len(closed)}  ({wins}W / {losses}L, total P&L ${pnl_total:+,.2f})",
        "",
    ]

    if closed:
        email_lines.append("Closed trades:")
        for t in closed:
            pnl_pct = (t.get("pnl_pct") or 0) * 100
            pnl_d = t.get("pnl") or 0
            days = (t.get("duration_minutes") or 0) / (60 * 24)
            email_lines.append(
                f"  {t['symbol']:<6} {pnl_pct:+6.1f}%  (${pnl_d:+,.0f})  "
                f"held {days:.1f}d  exit via {t.get('reason_exit', '?')}"
            )
        email_lines.append("")

    if positions:
        email_lines.append("Open positions:")
        for p in positions:
            sym = p.get("symbol", "?")
            value = float(p.get("market_value", 0))
            unrealized = float(p.get("unrealized_plpc", 0))
            email_lines.append(f"  {sym:<6} ${value:>9,.0f}   {unrealized:+.1%} unrealized")
        email_lines.append("")

    email_lines.extend([
        "=" * 60,
        "Claude's read on the week:",
        "=" * 60,
        synthesis,
        "",
        "→ Add 2-3 sentences to JOURNAL.md under [WEEK] entry to capture your reflection.",
    ])

    body = "\n".join(email_lines)
    subject = f"[Trading WEEK] {week_label} — {len(closed)} closed, {len(positions)} open"
    send_email(subject, body)

    # Journal entry: just the synthesis (Klaas adds reflection by hand)
    append_to_journal(week_label, synthesis)
    try:
        summary_record = {
            'week_label': week_label,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'equity': equity,
            'cash': cash,
            'open_positions': len(positions),
            'closed_trades': [
                {
                    'symbol': t['symbol'],
                    'pnl': t.get('pnl', 0),
                    'pnl_pct': t.get('pnl_pct', 0),
                    'reason_exit': t.get('reason_exit', ''),
                    'duration_minutes': t.get('duration_minutes', 0),
                }
                for t in closed
            ],
            'synthesis': synthesis,
        }
        append_weekly_summary(summary_record)
    except (KeyError, ValueError, TypeError, OSError) as e:
        print(f"  Failed to persist weekly summary: {e}")

    print("Weekly review complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
