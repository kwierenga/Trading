"""
Re-evaluate at market open

The AM plan in latest_strategy.json was generated pre-market with prior-close
prices. By the time bracket orders go out (09:35 ET), the open may have moved
the picture. This step re-calls Claude with the AM plan + current prices and
asks for a per-ticker SUBMIT / ADJUST / SKIP verdict.

Falls back to a mechanical filter if the LLM call fails:
  - price > entry * 1.03  -> skip (gapped up; opportunity passed)
  - price < stop          -> skip (signal already broken)
  - otherwise             -> submit at original entry

Reads:  latest_strategy.json
Writes: latest_strategy_postopen.json   (consumed by execute_strategy.py)
"""

import json
import os
import sys
from pathlib import Path

import anthropic

from market_data import latest_price


STRATEGY_IN = Path("latest_strategy.json")
STRATEGY_OUT = Path("latest_strategy_postopen.json")
GAP_UP_SKIP_THRESHOLD = 0.03  # current > entry * (1 + this) -> skip


REEVAL_SCHEMA = {
    "type": "object",
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["symbol", "action", "rationale"],
                "properties": {
                    "symbol": {"type": "string"},
                    "action": {"enum": ["submit", "adjust", "skip"]},
                    "limit_price": {"type": "number"},
                    "rationale": {"type": "string"},
                },
            },
        },
        "overall_note": {"type": "string"},
    },
}


def attach_current_prices(trades: list) -> None:
    for t in trades:
        sym = t["symbol"]
        price = latest_price(sym)
        t["current_price"] = price
        entry = float(t.get("entry_price", 0)) or None
        t["overnight_pct"] = (price - entry) / entry if (price is not None and entry) else None


def mechanical_decision(trade: dict) -> dict:
    sym = trade["symbol"]
    entry = float(trade["entry_price"])
    stop = float(trade["stop_loss"])
    cur = trade.get("current_price")

    if cur is None:
        return {"symbol": sym, "action": "skip",
                "rationale": "no current price (data fetch failed)"}
    if cur < stop:
        return {"symbol": sym, "action": "skip",
                "rationale": f"market ${cur:.2f} already below stop ${stop:.2f} — signal broken"}
    if cur > entry * (1 + GAP_UP_SKIP_THRESHOLD):
        gap = (cur / entry - 1) * 100
        return {"symbol": sym, "action": "skip",
                "rationale": f"gapped +{gap:.1f}% above entry — opportunity passed"}
    return {"symbol": sym, "action": "submit", "limit_price": entry,
            "rationale": f"current ${cur:.2f} within entry range — submit at original limit"}


def build_prompt(strategy: dict) -> str:
    trades = strategy.get("trades", [])
    lines = [
        "You analyzed these S&P 500 stocks pre-market and proposed bracket entries.",
        "The market is now OPEN. Below are the current prices and the move from your",
        "proposed entry. For each ticker, decide ONE of:",
        "  - SUBMIT  : original entry limit is still good",
        "  - ADJUST  : use a different limit price (only for small, justified moves)",
        "  - SKIP    : price action invalidates the setup today",
        "",
        "Apply the CLAUDE.md rules (you are not re-screening fundamentals — you are",
        "deciding whether the timing today still makes sense):",
        "  - Don't chase a gap-up: if current > 3% above entry, prefer SKIP unless",
        "    you have a strong reason the gap is structural (earnings beat, deal news).",
        "  - Don't catch a knife: if current is at or below the stop, signal is broken.",
        "  - When ADJUSTing, the new limit should still respect the original stop:",
        "    new entry must keep stop_distance_pct between 5% and 12%.",
        "",
        f"Original analysis: {strategy.get('analysis', '')}",
        f"Original confidence target: {strategy.get('confidence_target_pct', 0)}%",
        "",
        "Trades to re-evaluate:",
    ]
    for t in trades:
        cur = t.get("current_price")
        cur_s = f"${cur:.2f}" if cur is not None else "UNKNOWN"
        ov = t.get("overnight_pct")
        ov_s = f"{ov:+.1%}" if ov is not None else "n/a"
        lines.append(
            f"  - {t['symbol']}: AM entry ${t['entry_price']:.2f}, stop ${t['stop_loss']:.2f}, "
            f"target ${t['target']:.2f}, conv {t.get('conviction', 0)}%. "
            f"Current: {cur_s} ({ov_s} from entry)."
        )
    lines.append("")
    lines.append("Return a JSON object with `decisions`: one entry per symbol.")
    return "\n".join(lines)


def call_claude(strategy: dict) -> dict | None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("  ANTHROPIC_API_KEY not set — falling back to mechanical")
        return None

    # `or` (not the os.getenv default) so an empty-string env var also falls back.
    # Workflow misconfigs that pipe a non-existent secret can leave CLAUDE_MODEL=""
    # rather than unset, which the SDK rejects with `model: String should have at
    # least 1 character`. Defensive default.
    model = os.getenv("CLAUDE_MODEL") or "claude-sonnet-4-6"
    client = anthropic.Anthropic()
    prompt = build_prompt(strategy)

    text = ""
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": REEVAL_SCHEMA},
            },
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        if not text:
            print("  Claude returned no text — falling back to mechanical")
            return None
        return json.loads(text)
    except anthropic.APIError as e:
        print(f"  Claude API error: {e} — falling back to mechanical")
        return None
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e} — falling back to mechanical")
        return None


def apply_decisions(strategy: dict, decisions: list) -> dict:
    by_symbol = {d["symbol"]: d for d in decisions}
    new_trades = []
    for t in strategy.get("trades", []):
        sym = t["symbol"]
        d = by_symbol.get(sym)
        if not d:
            print(f"  {sym}: no decision returned — skipping")
            continue
        action = d["action"]
        rationale = d.get("rationale", "")
        if action == "skip":
            print(f"  {sym}: SKIP — {rationale}")
            continue
        if action == "adjust":
            new_limit = d.get("limit_price")
            if new_limit is None or new_limit <= 0:
                print(f"  {sym}: ADJUST without valid limit_price — submitting at original")
                new_limit = float(t["entry_price"])
            print(f"  {sym}: ADJUST limit ${new_limit:.2f} (was ${t['entry_price']:.2f}) — {rationale}")
            t = {**t, "entry_price": float(new_limit),
                 "reeval_action": "adjust", "reeval_rationale": rationale}
        else:
            print(f"  {sym}: SUBMIT @ ${t['entry_price']:.2f} — {rationale}")
            t = {**t, "reeval_action": "submit", "reeval_rationale": rationale}
        # Strip transient snapshot fields before persisting
        t.pop("current_price", None)
        t.pop("overnight_pct", None)
        new_trades.append(t)

    return {**strategy, "trades": new_trades, "reeval_at_open": True}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    if not STRATEGY_IN.exists():
        print(f"  No {STRATEGY_IN} — nothing to re-evaluate")
        return 1

    strategy = json.loads(STRATEGY_IN.read_text(encoding="utf-8"))
    trades = strategy.get("trades", [])
    if not trades:
        print("  AM plan had no trades — writing empty post-open strategy")
        STRATEGY_OUT.write_text(json.dumps(strategy, indent=2), encoding="utf-8")
        return 0

    print(f"Re-evaluating {len(trades)} trade(s) at market open...")
    attach_current_prices(trades)
    for t in trades:
        cur = t.get("current_price")
        cur_s = f"${cur:.2f}" if cur is not None else "n/a"
        ov = t.get("overnight_pct")
        ov_s = f"{ov:+.1%}" if ov is not None else "n/a"
        print(f"  {t['symbol']}: AM entry ${t['entry_price']:.2f}, current {cur_s} ({ov_s})")

    print("Asking Claude for per-ticker submit/adjust/skip verdict...")
    result = call_claude(strategy)

    if result is None:
        print("  Mechanical fallback engaged.")
        decisions = [mechanical_decision(t) for t in trades]
    else:
        decisions = result.get("decisions", [])
        if result.get("overall_note"):
            print(f"  Note: {result['overall_note']}")

    print("\nDecisions:")
    out = apply_decisions(strategy, decisions)
    STRATEGY_OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {STRATEGY_OUT} with {len(out['trades'])} trade(s) to execute.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
