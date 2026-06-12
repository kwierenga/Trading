"""
Per-trade post-mortems — the per-trade capture of the active learning loop.

Every closed trade gets a block appended to trade_post_mortems.md at EOD:
the mechanical half is auto-filled from the reconciled trade journal, the
reflective half is left as placeholders for Klaas to fill within 24h. The
reflective fields are the actual learning; the mechanical scaffold exists so
the reflection reacts to facts instead of memory.

Idempotent: each block carries an HTML-comment key (symbol|exit_time|entry),
and trades whose key is already in the file are skipped — safe to run at
every EOD including re-runs.

Manual/liquidation closes (reason_exit == "manual") get a slimmer block:
they're book-management events, not signal exits, so deep reflection is
optional there.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

PM_FILE = Path("trade_post_mortems.md")
PLACEHOLDER = "_(fill in within 24h)_"

HEADER = """# Trade post-mortems

One block per closed trade, appended automatically at EOD (newest at bottom).
The **Mechanical** half is auto-filled from the reconciled trade journal; the
**Reflective** half is yours — fill it within 24h of the close, while the
trade is still vivid. The reflective fields are where the learning happens:
the goal is to separate thesis-failure from execution-failure from variance,
because in aggregate P&L all three look identical.

If more than one block in a row is left unfilled, the EOD email will nag.
Confirmed patterns (3+ occurrences) graduate to the memory lessons file and,
eventually, to RULEBOOK.md.
"""


def _key(trade: Dict) -> str:
    return f"{trade.get('symbol')}|{(trade.get('exit_time') or '')[:19]}|{trade.get('entry_price')}"


def _existing_keys(text: str) -> set:
    return set(re.findall(r"<!-- pm-key: (.*?) -->", text))


def _held_days(trade: Dict) -> float:
    dm = trade.get("duration_minutes")
    if dm:
        return dm / 60 / 24
    return 0.0


def _format_block(trade: Dict) -> str:
    sym = trade.get("symbol", "?")
    entry = trade.get("entry_price") or 0
    exit_p = trade.get("exit_price") or 0
    pnl = trade.get("pnl") or 0
    pnl_pct = (trade.get("pnl_pct") or 0) * 100
    reason = trade.get("reason_exit") or "unknown"
    held = _held_days(trade)
    exit_date = (trade.get("exit_time") or "")[:10]
    entry_date = (trade.get("entry_time") or "")[:10]
    tax = "LTCG" if held >= 365 else "STCG"
    rationale = (trade.get("rationale") or "").strip()
    if len(rationale) > 350:
        rationale = rationale[:350] + " …"
    if not rationale:
        rationale = "(no pre-trade rationale recorded)"

    stop = trade.get("stop_loss")
    r_line = ""
    if stop and entry and entry > stop > 0:
        r = (exit_p - entry) / (entry - stop)
        r_line = f"\n- R-multiple: {r:+.2f}R (1R = ${entry - stop:.2f}/share)"

    head = (
        f"## {exit_date} — {sym} closed "
        f"(held {held:.1f}d, {pnl_pct:+.2f}%, ${pnl:+,.2f})\n"
        f"<!-- pm-key: {_key(trade)} -->\n"
    )

    mech = (
        f"\n**Mechanical (auto-filled):**\n"
        f"- Entry: ${entry:.2f} on {entry_date} → Exit: ${exit_p:.2f} on {exit_date} via {reason}\n"
        f"- Tax bucket: {tax}{r_line}\n"
        f"- Pre-trade rationale: {rationale}\n"
    )

    if reason == "manual":
        refl = (
            "\n**Reflective (optional — manual/liquidation close, not a signal exit):**\n"
            f"- Why was this closed manually? {PLACEHOLDER}\n"
        )
    else:
        refl = (
            "\n**Reflective (fill within 24h):**\n"
            f"- What I expected to happen: {PLACEHOLDER}\n"
            f"- What actually happened: {PLACEHOLDER}\n"
            f"- What surprised me: {PLACEHOLDER}\n"
            f"- Entry timing right in retrospect? (Y/N + why): {PLACEHOLDER}\n"
            f"- Exit timing right in retrospect? (Y/N + why): {PLACEHOLDER}\n"
            f"- Did I override the system anywhere? (size/stop/hold/exit + why): {PLACEHOLDER}\n"
            f"- What would I do differently? (or \"nothing — process worked\"): {PLACEHOLDER}\n"
        )

    return head + mech + refl + "\n---\n"


def append_for_closed_trades(closed: List[Dict]) -> List[str]:
    """Append blocks for closed trades not yet in the file. Returns new symbols."""
    text = PM_FILE.read_text(encoding="utf-8") if PM_FILE.exists() else HEADER
    seen = _existing_keys(text)
    new = [t for t in closed if _key(t) not in seen]
    if not new:
        return []
    new.sort(key=lambda t: t.get("exit_time") or "")
    blocks = "".join("\n" + _format_block(t) for t in new)
    PM_FILE.write_text(text + blocks, encoding="utf-8")
    return [t.get("symbol", "?") for t in new]


def pending_reflections() -> List[str]:
    """Headers of signal-exit blocks whose reflective fields are still unfilled."""
    if not PM_FILE.exists():
        return []
    text = PM_FILE.read_text(encoding="utf-8")
    pending = []
    for block in text.split("\n## ")[1:]:
        if "Reflective (fill within 24h)" in block and PLACEHOLDER in block:
            pending.append(block.splitlines()[0].strip())
    return pending


def email_block(new_symbols: List[str]) -> str:
    """Compact EOD-email section: new blocks + outstanding reflections."""
    lines = ["POST-MORTEMS (the training loop):"]
    if new_symbols:
        lines.append(f"  New post-mortem block(s) written: {', '.join(new_symbols)}")
        lines.append("  -> trade_post_mortems.md - fill the reflective fields within 24h.")
    pending = pending_reflections()
    if pending:
        lines.append(f"  Awaiting your reflection ({len(pending)}):")
        for p in pending[:5]:
            lines.append(f"    - {p}")
        if len(pending) > 5:
            lines.append(f"    … and {len(pending) - 5} more")
        if len(pending) >= 2:
            lines.append("  WARNING: more than one unfilled post-mortem - the learning loop is the product; don't let these pile up.")
    elif not new_symbols:
        lines.append("  Nothing new; no reflections outstanding.")
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    trades = json.loads(Path("trade_journal.json").read_text(encoding="utf-8"))
    closed = [t for t in trades if t.get("status") == "closed"]
    added = append_for_closed_trades(closed)
    print(f"Added {len(added)} block(s): {', '.join(added) if added else '—'}")
    print(email_block(added))
