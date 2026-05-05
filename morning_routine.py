"""
Morning Routine
Runs at 06:00 ET via .github/workflows/morning.yml on weekdays.

Two-phase invocation (controlled by the PHASE env var):

  PHASE=plan   (default) — run the S&P 500 screen, ask Claude for trades, write
                            latest_strategy.json, append an [AM] JOURNAL entry.
                            Does NOT send email. Used by morning.yml step 1.

  PHASE=email             — load the just-written latest_strategy.json, fetch
                            current open positions, and send the morning email
                            with the APPROVE_URL (the GitHub Actions URL of the
                            waiting Execute strategy run). Used by morning.yml
                            step 2, after dispatching execute.yml.

Splitting these phases lets the workflow dispatch execute.yml *between* them and
include the resulting waiting-run URL in the email — so the email contains the
exact tap-to-approve link instead of a generic actions-tab URL.
"""

import json
import os
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
ACTIONS_FALLBACK_URL = "https://github.com/kwierenga/Trading/actions/workflows/execute.yml"


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

    # Today's plan — describes the cloud-side flow Klaas actually uses now
    lines.append("**Today's plan.** ")
    if trades:
        lines.append(
            "Tap **Approve** on the GitHub email (subject `[Trading AM]`) before 09:25 ET. "
            "Approval triggers the post-open re-evaluation; if Claude still likes the setups, "
            "bracket orders go in at 09:35 ET. No tap = day skipped, no harm. "
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


def build_email(strategy: dict, open_positions: list, today_label: str,
                approve_url: str = "") -> tuple:
    trades = strategy.get("trades", [])

    lines = [f"Morning plan — {today_label} ET\n"]
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
        url = approve_url or ACTIONS_FALLBACK_URL
        lines.append("")
        lines.append("=" * 60)
        lines.append("ACTION REQUIRED — to place these orders today:")
        lines.append("")
        lines.append(f"  TAP TO APPROVE:  {url}")
        lines.append("")
        lines.append("  Approve before 09:25 ET. After you tap:")
        lines.append("    - Claude re-evaluates each ticker against the actual open")
        lines.append("    - Orders that still look good are submitted at 09:35 ET")
        lines.append("    - Orders that gapped up or broke down are skipped automatically")
        lines.append("  No tap = day skipped, no harm.")
        lines.append("=" * 60)

    body = "\n".join(lines)
    action_hint = "approval needed by 09:25 ET" if trades else "cash"
    subject = f"[Trading AM] {today_label} — {len(trades)} setup(s) — {action_hint}" if trades \
        else f"[Trading AM] {today_label} — cash"
    return subject, body


def run_plan_phase(today_label: str) -> int:
    """Generate strategy + journal, write latest_strategy.json. No email."""
    print("  Generating strategy (forcing fresh screen)...")
    strategy = get_enhanced_strategy(force_refresh_candidates=True)

    if not strategy:
        # Email a failure notice immediately — don't wait for the email phase.
        send_email(
            f"[Trading AM] {today_label} — FAILED",
            f"Strategy generation failed.\nCheck logs and yfinance/Anthropic connectivity.",
        )
        return 1

    try:
        client = AlpacaClient()
        open_positions = client.get_positions()
    except (OSError, ValueError, KeyError) as e:
        print(f"  Could not fetch positions: {e}")
        open_positions = []

    with open(STRATEGY_PATH, "w") as f:
        json.dump(strategy, f, indent=2)

    entry = build_journal_entry(strategy, open_positions, today_label)
    append_to_journal(entry)

    print(f"Plan phase complete: {len(strategy.get('trades', []))} trade(s) written to {STRATEGY_PATH}.")
    return 0


def run_email_phase(today_label: str) -> int:
    """Load latest_strategy.json + send email with APPROVE_URL."""
    if not STRATEGY_PATH.exists():
        print(f"  {STRATEGY_PATH} missing — plan phase didn't run? Skipping email.")
        return 1

    strategy = json.loads(STRATEGY_PATH.read_text(encoding="utf-8"))

    try:
        client = AlpacaClient()
        open_positions = client.get_positions()
    except (OSError, ValueError, KeyError) as e:
        print(f"  Could not fetch positions: {e}")
        open_positions = []

    approve_url = os.getenv("APPROVE_URL", "").strip()
    if not approve_url:
        print(f"  APPROVE_URL not provided — falling back to {ACTIONS_FALLBACK_URL}")

    subject, body = build_email(strategy, open_positions, today_label, approve_url)
    send_email(subject, body)
    print("Email phase complete.")
    return 0


def main() -> int:
    # Windows console / Task Scheduler default to cp1252 — Claude's analysis text
    # may contain → ✓ ★ etc. that crash print() unless stdout is UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    today = datetime.now(EST)
    today_label = today.strftime("%Y-%m-%d %A")
    phase = os.getenv("PHASE", "plan").lower()
    print(f"Morning routine ({phase}) starting at {today.isoformat()}")

    force = os.getenv("FORCE_RUN", "false").lower() == "true"
    if not is_us_trading_day() and not force:
        print(f"  {today_label} is a weekend — skipping. (Set FORCE_RUN=true to override.)")
        return 0
    if force and not is_us_trading_day():
        print(f"  {today_label} is a weekend — running anyway because FORCE_RUN=true.")

    if phase == "email":
        return run_email_phase(today_label)
    return run_plan_phase(today_label)


if __name__ == "__main__":
    sys.exit(main())
