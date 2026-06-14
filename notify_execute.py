"""
Email notifications for execute.yml workflow.

Three modes, all called from execute.yml as separate steps:

  queue-delay   Compare github.event.created_at vs now. If gap > threshold
                (default 20 min), email "no trade today" and exit 1 so the
                workflow halts before submitting on a stale plan.
  failure       Email on any step failure (called from `if: failure()`).
                Subject indicates which step, body links to the run.
  success       Email after orders submitted; summarizes today's new entries
                from trade_journal.json.

Exit codes:
  queue-delay:  0 = no delay (proceed),  1 = delay detected (email sent, halt)
  failure:      0 always (email failure shouldn't double-fault the run)
  success:      0 always
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from email_notifier import send_email
import execution_ledger


# The executed plan (post-open re-evaluation) carries the full AI rationale per
# trade; fall back to the AM plan if the post-open file is absent.
EXECUTED_PLAN_FILES = ("latest_strategy_postopen.json", "latest_strategy.json")


def _load_executed_plan() -> dict:
    """Load the plan that was actually executed, for per-trade rationale.
    Returns {} on any failure (rationale block is then simply omitted)."""
    for name in EXECUTED_PLAN_FILES:
        p = Path(name)
        if not p.exists():
            continue
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def rationale_block(placed_symbols: list) -> str:
    """Build the 'why we bought it' section for today's placed trades, pulling
    the AI's rationale/conviction/exit-plan from the executed plan. Empty string
    if nothing is available — never raises."""
    if not placed_symbols:
        return ""
    plan = _load_executed_plan()
    trades = {t.get("symbol"): t for t in plan.get("trades", []) if t.get("symbol")}
    if not trades:
        return ""

    lines = ["WHY THESE TRADES (AI rationale):"]
    for sym in placed_symbols:
        t = trades.get(sym)
        if not t:
            continue
        conv = t.get("conviction", 0)
        hold = t.get("holding_period", "?")
        lines.append(f"\n  {sym}  [conviction {conv}%, horizon {hold}]")
        rationale = (t.get("rationale") or "").strip()
        if rationale:
            lines.append(f"    {rationale}")
        exit_plan = (t.get("exit_plan") or "").strip()
        if exit_plan:
            lines.append(f"    Exit plan: {exit_plan}")

    # The top-level analysis is the market-regime / "why now" context behind the
    # whole plan — include it once as a footer for the full picture.
    analysis = (plan.get("analysis") or "").strip()
    if analysis:
        lines.append("\nMARKET CONTEXT (from the morning plan):")
        lines.append(f"  {analysis}")

    return "\n".join(lines) if len(lines) > 1 else ""


def cmd_queue_delay(created_at: str, threshold_min: int, run_url: str) -> int:
    if not created_at:
        print("  No --created-at; skipping queue-delay check.")
        return 0
    try:
        dispatched = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        print(f"  Invalid --created-at: {created_at!r}; skipping queue-delay check.")
        return 0

    now = datetime.now(timezone.utc)
    delay_min = (now - dispatched).total_seconds() / 60.0
    print(f"  Queue delay: {delay_min:.1f} min (threshold {threshold_min} min)")

    if delay_min <= threshold_min:
        return 0

    subject = f"[Trading EXEC] Queue delay {delay_min:.0f}m — no trade today"
    body = (
        f"You tapped 'Run Workflow' at {dispatched.isoformat()},\n"
        f"but a GitHub Actions runner didn't pick up the job until\n"
        f"{now.isoformat()} ({delay_min:.0f} minutes later).\n\n"
        f"To avoid trading on a plan that's now stale, this run is exiting\n"
        f"without submitting any orders. Today is a no-trade day.\n\n"
        f"Cause: GitHub-hosted runner queue congestion (out of our control).\n"
        f"If this happens repeatedly we'll move execute.yml off free runners.\n\n"
        f"Run URL: {run_url}\n"
    )
    send_email(subject, body)
    return 1


def cmd_failure(run_url: str, failed_step: str) -> int:
    label = failed_step or "(see run URL)"
    subject = f"[Trading EXEC] Failed at: {label}"
    body = (
        f"The Execute strategy workflow failed.\n\n"
        f"Failing step: {label}\n"
        f"Run URL:      {run_url}\n\n"
        f"Some, all, or none of today's orders may have been submitted.\n"
        f"Check the run log via the URL, then verify positions in Alpaca.\n"
    )
    send_email(subject, body)
    return 0


def cmd_success(run_url: str, journal_path: str = "trade_journal.json") -> int:
    """
    Report the day's execution from the per-candidate ledger (enhancements
    #1-#3): every PLACED order with its real Alpaca fill status, and every
    SKIPPED candidate with the exact gate + reason. This replaces the old
    "0 orders submitted — likely cash or re-evaluation skipped" guess, which
    couldn't tell Klaas *why* a trade didn't happen.

    Falls back to the legacy trade_journal summary only if the ledger is
    absent (e.g. a run from before this module existed).
    """
    today = datetime.now(timezone.utc).date().isoformat()

    # reconcile_and_persist queries Alpaca so "submitted" becomes the true
    # state ("filled" / "working (unfilled)" / "partial"). Best-effort: on any
    # failure it returns the unreconciled rows so the email still goes out.
    rows = execution_ledger.reconcile_and_persist(today)

    if rows:
        counts = execution_ledger.summary_counts(rows)
        placed = [r for r in rows if r.get("decision") == "placed"]
        total = sum(float(r.get("entry_price", 0)) * int(r.get("shares", 0)) for r in placed)

        subject = (
            f"[Trading EXEC] {counts['placed']} placed "
            f"({counts['filled']} filled), {counts['skipped']} skipped"
        )
        why = rationale_block([r.get("symbol") for r in placed])
        body = (
            f"Execution summary for {today}:\n\n"
            + execution_ledger.email_block(rows)
            + (f"\n\n  Placed notional: ${total:,.2f}" if placed else "")
            + (f"\n\n{why}" if why else "")
            + "\n\nNote: entries are GTC limit orders. 'working (unfilled)' means the "
            + "order is resting and has not yet filled at the limit price; the EOD "
            + "sweep reports/cancels stale resting entries.\n"
            + f"\nRun URL: {run_url}\n"
        )
        send_email(subject, body)
        return 0

    # --- Legacy fallback: no ledger present ---
    new_entries = []
    if Path(journal_path).exists():
        try:
            with open(journal_path, "r") as f:
                trades = json.load(f)
            for t in trades:
                if t.get("entry_time", "").startswith(today):
                    new_entries.append(t)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  Could not read {journal_path}: {e}")

    if not new_entries:
        subject = "[Trading EXEC] Run complete — 0 orders submitted"
        body = (
            f"Workflow ran cleanly but no orders went in, and no execution ledger "
            f"was found.\nLikely cause: re-evaluation skipped all trades, or strategy "
            f"proposed cash.\n\nRun URL: {run_url}\n"
        )
    else:
        lines = []
        total = 0.0
        for e in new_entries:
            shares = int(e.get("shares", 0))
            entry = float(e.get("entry_price", 0))
            stop = float(e.get("stop_loss", 0))
            target = float(e.get("target_price", 0))
            total += shares * entry
            lines.append(
                f"  {e.get('symbol', '?'):6s}  {shares:>5d} sh @ ${entry:>8.2f}  "
                f"stop ${stop:>8.2f}  target ${target:>8.2f}  "
                f"= ${shares * entry:>10,.2f}"
            )
        subject = f"[Trading EXEC] {len(new_entries)} order(s) submitted"
        body = (
            f"Orders placed today ({today}):\n\n"
            + "\n".join(lines)
            + f"\n\nTotal notional: ${total:,.2f}\n"
            + f"\nRun URL: {run_url}\n"
        )
    send_email(subject, body)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", required=True,
                   choices=["queue-delay", "failure", "success"])
    p.add_argument("--created-at", default="",
                   help="github.event.created_at ISO timestamp (queue-delay mode)")
    p.add_argument("--threshold-min", type=int, default=20,
                   help="Queue delay threshold in minutes (default 20)")
    p.add_argument("--failed-step", default="",
                   help="Step name that failed (failure mode)")
    p.add_argument("--run-url", default="",
                   help="GitHub Actions run URL for clickthrough")
    p.add_argument("--journal", default="trade_journal.json",
                   help="Path to trade journal JSON (success mode)")
    args = p.parse_args()

    if args.mode == "queue-delay":
        return cmd_queue_delay(args.created_at, args.threshold_min, args.run_url)
    if args.mode == "failure":
        return cmd_failure(args.run_url, args.failed_step)
    if args.mode == "success":
        return cmd_success(args.run_url, args.journal)
    return 0


if __name__ == "__main__":
    sys.exit(main())
