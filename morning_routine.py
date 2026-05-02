"""
Morning Routine
Runs at 6am EST via Windows Task Scheduler (weekdays only).
- Forces a fresh S&P 500 screen via ai_strategy_enhanced
- Appends an [AM] entry to JOURNAL.md (open questions + today's plan)
- Emails a brief summary to klaaswierenga@gmail.com
- Saves the strategy to latest_strategy.json so execute_strategy.py can use it

Time budget: a few minutes for Klaas to read.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytz

from alpaca_client import AlpacaClient
from ai_strategy_enhanced import get_enhanced_strategy
from email_notifier import send_email


EST = pytz.timezone("America/New_York")
JOURNAL_PATH = Path("JOURNAL.md")
STRATEGY_PATH = Path("latest_strategy.json")


def is_us_trading_day() -> bool:
    """Skip weekends. (Holidays not implemented; rare and we'll just send empty plans.)"""
    return datetime.now(EST).weekday() < 5  # Mon-Fri = 0-4


def append_to_journal(entry_text: str) -> None:
    """Insert a new entry just below the journal header (newest-first ordering)."""
    if not JOURNAL_PATH.exists():
        print(f"  JOURNAL.md not found at {JOURNAL_PATH.resolve()} — skipping append")
        return

    text = JOURNAL_PATH.read_text(encoding="utf-8")
    marker = "---\n\n"
    parts = text.split(marker, 2)
    if len(parts) < 2:
        # Fallback: just append at end
        JOURNAL_PATH.write_text(text + "\n\n" + entry_text, encoding="utf-8")
        return

    # parts[0] = preamble (header + "Sections (in order)" notes) up to first ---
    # parts[1] = body starting with first dated entry
    new_text = parts[0] + marker + entry_text + "\n\n" + marker + parts[1]
    JOURNAL_PATH.write_text(new_text, encoding="utf-8")


def build_journal_entry(strategy: dict, open_positions: list, today_label: str) -> str:
    trades = strategy.get("trades", [])

    lines = [f"## [AM] {today_label}\n"]

    # Open questions
    lines.append("**Open questions.** ")
    if not trades:
        lines.append(
            "Screen produced no high-conviction setups today — is the market broadly extended, or are filters too tight? "
            "What would have to change for a setup to emerge tomorrow? "
            f"With {len(open_positions)} open position(s), are any approaching their stops or targets? "
        )
    else:
        symbols = ", ".join(t["symbol"] for t in trades)
        lines.append(
            f"Will the {len(trades)} proposed entries ({symbols}) fill at limit, or run away pre-market? "
            f"What's the one thing that could derail the {strategy.get('confidence_target_pct', 0)}% confidence target? "
            "Any overnight news on these names worth checking before placing orders? "
        )
    lines.append("\n")

    # Today's plan
    lines.append("**Today's plan.** ")
    if trades:
        lines.append(
            f"Run `python execute_strategy.py --dry-run` to preview sizing, then `python execute_strategy.py` to place bracket orders. "
        )
    else:
        lines.append("Hold cash today — no high-conviction setups passed the screen. Patience is a position. ")

    if open_positions:
        lines.append(
            f"Monitor {len(open_positions)} open position(s) for thesis-break, stop hits, or LTCG-approaching flags. "
        )
    else:
        lines.append("No open positions to monitor. ")

    return "".join(lines)


def build_email(strategy: dict, open_positions: list, today_label: str) -> tuple:
    trades = strategy.get("trades", [])

    lines = [f"Morning plan — {today_label} EST\n"]
    lines.append(f"Confidence: {strategy.get('confidence_target_pct', 0)}%")
    lines.append("")

    if not trades:
        lines.append("PLAN: hold cash — no high-conviction setups today.")
    else:
        lines.append(f"PLAN — {len(trades)} trade(s) to consider:")
        for i, t in enumerate(trades, 1):
            entry = t.get("entry_price", 0)
            stop = t.get("stop_loss", 0)
            target = t.get("target", 0)
            stop_pct = (entry - stop) / entry if entry else 0
            tgt_pct = (target - entry) / entry if entry else 0
            lines.append(
                f"  {i}. {t['symbol']:<6} BUY @ ${entry:.2f}   "
                f"stop ${stop:.2f} ({stop_pct:.1%})   "
                f"target ${target:.2f} ({tgt_pct:+.1%})   "
                f"[{t.get('holding_period', '?')}, {t.get('conviction', 0)}% conv, R/R {t.get('risk_reward_ratio', 0):.1f}]"
            )

    lines.append("")
    if open_positions:
        lines.append(f"OPEN POSITIONS ({len(open_positions)}):")
        for p in open_positions:
            sym = p.get("symbol", "?")
            value = float(p.get("market_value", 0))
            unrealized_pct = float(p.get("unrealized_plpc", 0))
            lines.append(f"  {sym:<6} ${value:>9,.0f}   {unrealized_pct:+.1%} unrealized")
    else:
        lines.append("OPEN POSITIONS: none (fully in cash)")

    if trades:
        lines.append("")
        lines.append("To execute: `python execute_strategy.py --dry-run` then `python execute_strategy.py`")

    body = "\n".join(lines)
    subject = f"[Trading AM] {today_label} — {'cash' if not trades else f'{len(trades)} setup(s)'}"
    return subject, body


def main() -> int:
    # Windows console / Task Scheduler default to cp1252 — Claude's analysis text
    # may contain → ✓ ★ etc. that crash print() unless stdout is UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    today = datetime.now(EST)
    today_label = today.strftime("%Y-%m-%d %A")
    print(f"Morning routine starting at {today.isoformat()}")

    if not is_us_trading_day():
        print(f"  {today_label} is a weekend — skipping.")
        return 0

    try:
        client = AlpacaClient()
        open_positions = client.get_positions()
    except Exception as e:
        print(f"  Could not fetch positions: {e}")
        open_positions = []

    print("  Generating strategy (forcing fresh screen)...")
    strategy = get_enhanced_strategy(force_refresh_candidates=True)

    if not strategy:
        send_email(
            f"[Trading AM] {today_label} — FAILED",
            f"Strategy generation failed at {today.isoformat()}.\nCheck logs and yfinance/Anthropic connectivity.",
        )
        return 1

    # Persist for execute_strategy.py
    with open(STRATEGY_PATH, "w") as f:
        json.dump(strategy, f, indent=2)

    # Journal
    entry = build_journal_entry(strategy, open_positions, today_label)
    append_to_journal(entry)

    # Email
    subject, body = build_email(strategy, open_positions, today_label)
    send_email(subject, body)

    print("Morning routine complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
