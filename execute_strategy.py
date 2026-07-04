"""
Execute Strategy

Loads latest_strategy.json, computes position sizes per the risk rules,
runs the concentration check, asks for confirmation per trade, and places
bracket orders (limit entry + take-profit + stop-loss) via Alpaca.

Usage:
  python execute_strategy.py                # interactive
  python execute_strategy.py --dry-run      # show plan, place no orders
  python execute_strategy.py --yes          # auto-approve all (DANGEROUS)
  python execute_strategy.py --strategy=path  # use a different strategy file
"""

import argparse
import json
import sys
from pathlib import Path

from alpaca_client import AlpacaClient
from position_sizer import (
    PositionSizer, validate_concentration, validate_sector_concentration,
    validate_gross_deployment, validate_daily_loss_halt,
    MAX_POSITION_PCT, MAX_PYRAMID_PCT, MAX_PYRAMID_TRANCHE_PCT,
    MAX_SECTOR_PCT, MAX_GROSS_PCT,
    MIN_STOP_PCT, MAX_STOP_PCT,
)
from trade_journal import TradeJournal
from market_data import latest_price
from execution_ledger import RunLedger


# ---------------------------------------------------------------------------
# Conviction -> position-size mapping (RECOMMENDATIONS_top10 #2)
# Claude emits a conviction percentage on every proposed trade. Previously
# every approved trade got the full risk-target size regardless of conviction.
# Apply the multiplier so a 70%-conviction trade is sized smaller than a 90%-
# conviction one, and < 60% is skipped entirely.
# ---------------------------------------------------------------------------

CONVICTION_TIERS = [
    (80, 1.00),   # >= 80%: full size
    (70, 0.75),   # 70-79%: 3/4 size
    (60, 0.50),   # 60-69%: half size
]
MIN_CONVICTION_TO_TRADE = 60


def conviction_size_multiplier(conviction: float) -> float:
    """Return the sizing multiplier for a given conviction %. 0.0 means skip."""
    if conviction is None:
        return 0.0
    for threshold, mult in CONVICTION_TIERS:
        if conviction >= threshold:
            return mult
    return 0.0


def load_strategy(path: str) -> dict:
    if not Path(path).exists():
        print(f"Strategy file not found: {path}")
        print("Run `python ai_strategy_enhanced.py` first to generate one.")
        sys.exit(1)
    with open(path, 'r') as f:
        return json.load(f)


def confirm(prompt: str, auto_yes: bool = False) -> bool:
    if auto_yes:
        print(f"{prompt}[y]")
        return True
    answer = input(prompt).strip().lower()
    return answer in ('y', 'yes')


def execute_strategy(strategy_path: str = 'latest_strategy.json',
                     dry_run: bool = False,
                     auto_yes: bool = False) -> None:
    strategy = load_strategy(strategy_path)

    print(f"\n=== STRATEGY EXECUTION ({strategy_path}) ===\n")
    print(f"Analysis: {strategy.get('analysis', '')}")
    print(f"Confidence target: {strategy.get('confidence_target_pct', 0)}%")

    trades = strategy.get('trades', [])
    if not trades:
        print("\nStrategy proposes NO TRADES — recommendation is cash. Done.")
        return

    print(f"\n{len(trades)} trade(s) to evaluate.\n")

    client = AlpacaClient()
    account = client.get_account()
    positions = client.get_positions()
    equity = float(account['equity'])
    print(f"Account equity: ${equity:,.2f}")
    print(f"Open positions: {len(positions)}\n")

    sizer = PositionSizer(account_equity=equity)

    placed = []
    skipped = []
    # Per-run ledger (enhancement #1): one row per candidate recording why it
    # was placed or skipped, surfaced in the daily email by notify_execute.
    ledger = RunLedger()

    # Daily dollar-loss kill switch (live-transition checklist #9): if the
    # account is already down >= DAILY_LOSS_HALT_PCT vs prior close, refuse
    # every new entry for the day. Runs BEFORE the per-trade loop — it's a
    # book-level halt, not a per-name check. Fail-closed on missing data.
    halt = validate_daily_loss_halt(account)
    if not halt['ok']:
        print("=" * 70)
        print(f"KILL SWITCH ENGAGED: {halt['reason']}")
        print("=" * 70)
        for trade in trades:
            symbol = trade.get('symbol', '?')
            skipped.append(symbol)
            ledger.skipped(symbol, "kill_switch", halt['reason'])
        if not dry_run:
            ledger.commit()
        print("\n" + "=" * 70)
        print("EXECUTION SUMMARY")
        print("=" * 70)
        print(f"  Placed:  0")
        print(f"  Skipped: {len(skipped)} {skipped}")
        print("  All candidates skipped by the daily-loss kill switch.")
        print()
        return
    print(f"Kill switch OK: {halt['reason']}\n")

    for i, trade in enumerate(trades, 1):
        symbol = trade['symbol']
        direction = trade['direction']

        is_pyramid = bool(trade.get('is_pyramid'))
        trade_cap_pct = MAX_PYRAMID_PCT if is_pyramid else MAX_POSITION_PCT

        print("=" * 70)
        tag = f" [PYRAMID t{trade.get('tranche', '?')}]" if is_pyramid else ""
        print(f"TRADE {i}/{len(trades)}: {symbol} {direction}  [{trade.get('holding_period', '?')}]{tag}")
        print("=" * 70)
        print(f"  Rationale:      {trade.get('rationale', '')}")
        print(f"  Entry strategy: {trade.get('entry_strategy', '')}")
        print(f"  Exit plan:      {trade.get('exit_plan', '')}")
        print(f"  Conviction:     {trade.get('conviction', 0)}%   R/R: {trade.get('risk_reward_ratio', 0):.2f}")

        if direction != 'BUY':
            print("  [SKIPPING] non-BUY directives are not auto-executed yet (use Alpaca UI for sells/closes).")
            skipped.append(symbol)
            ledger.skipped(symbol, "non_buy", f"{direction} not auto-executed (manual via Alpaca UI)")
            continue

        try:
            entry = float(trade['entry_price'])
            stop = float(trade['stop_loss'])
            target = float(trade['target'])
        except (KeyError, TypeError, ValueError) as e:
            print(f"  ERROR: malformed trade data ({e}) — skipping")
            skipped.append(symbol)
            ledger.skipped(symbol, "malformed", f"malformed trade data ({e})")
            continue

        if entry <= 0 or stop <= 0 or target <= 0 or stop >= entry or target <= entry:
            print(f"  ERROR: invalid prices (entry ${entry}, stop ${stop}, target ${target}) — skipping")
            skipped.append(symbol)
            ledger.skipped(symbol, "invalid_prices",
                           f"invalid prices (entry ${entry}, stop ${stop}, target ${target})")
            continue

        # Sanity check vs current market
        current = latest_price(symbol)
        if current:
            print(f"  Market now: ${current:.2f}  (proposed entry ${entry:.2f}, target ${target:.2f})")
            if current > entry * 1.05:
                print(f"  WARNING: market is {(current/entry - 1):+.1%} above entry — opportunity may have passed")
            elif current < stop:
                print(f"  WARNING: market is already below the proposed stop — likely stale signal")
                skipped.append(symbol)
                ledger.skipped(symbol, "stale_signal",
                               f"market ${current:.2f} already below stop ${stop:.2f} — signal broken")
                continue

        # Stop sanity bounds
        stop_pct = (entry - stop) / entry
        if stop_pct < MIN_STOP_PCT:
            print(f"  WARNING: stop {stop_pct:.1%} narrower than {MIN_STOP_PCT:.0%} floor — likely whipsaw risk")
        if stop_pct > MAX_STOP_PCT:
            print(f"  WARNING: stop {stop_pct:.1%} wider than {MAX_STOP_PCT:.0%} ceiling — single-name risk too high")

        # Conviction gate (sized down below the LLM's stated certainty)
        conviction = float(trade.get('conviction', 0) or 0)
        mult = conviction_size_multiplier(conviction)
        if mult <= 0:
            print(f"  [SKIPPING] conviction {conviction:.0f}% below {MIN_CONVICTION_TO_TRADE}% floor — too uncertain to risk capital")
            skipped.append(symbol)
            ledger.skipped(symbol, "conviction",
                           f"conviction {conviction:.0f}% below {MIN_CONVICTION_TO_TRADE}% floor")
            continue

        # Compute position size (risk-targeted, concentration-capped).
        # Pyramid trades get the relaxed 30% cap on confirmed winners.
        rec = sizer.calculate_for_trade(symbol, entry, stop, max_position_pct=trade_cap_pct)
        if 'error' in rec or rec['recommended_shares'] <= 0:
            print(f"  ERROR: cannot size — {rec.get('error', 'zero shares recommended')}")
            skipped.append(symbol)
            ledger.skipped(symbol, "sizing", rec.get('error', 'zero shares recommended'))
            continue

        full_shares = rec['recommended_shares']
        shares = int(full_shares * mult)
        if shares <= 0:
            print(f"  [SKIPPING] sized-down shares would round to 0 (full={full_shares}, mult={mult:.2f})")
            skipped.append(symbol)
            ledger.skipped(symbol, "zero_shares",
                           f"sized down to 0 shares (full={full_shares}, conviction mult={mult:.2f})")
            continue
        if is_pyramid:
            # A pyramid ADD is a ~10% tranche by design (pyramid.py), but the
            # risk-target sizing above re-sizes it against a tight break-even
            # stop and balloons it toward the 30% combined cap. Honor the
            # tranche design, not the fresh-entry formula.
            tranche_cap_shares = int((MAX_PYRAMID_TRANCHE_PCT * equity) / entry)
            if tranche_cap_shares <= 0:
                print(f"  [SKIPPING] pyramid tranche cap rounds to 0 shares at ${entry:.2f}")
                skipped.append(symbol)
                ledger.skipped(symbol, "zero_shares",
                               f"pyramid tranche cap ({MAX_PYRAMID_TRANCHE_PCT:.0%} of equity) rounds to 0 shares")
                continue
            if shares > tranche_cap_shares:
                print(f"  Pyramid tranche capped: {shares} -> {tranche_cap_shares} shares "
                      f"({MAX_PYRAMID_TRANCHE_PCT:.0%} of equity per add)")
                shares = tranche_cap_shares
        proposed_value = shares * entry
        adjusted_position_pct = proposed_value / equity if equity > 0 else 0
        adjusted_loss_dollars = shares * (entry - stop)
        adjusted_loss_pct = adjusted_loss_dollars / equity if equity > 0 else 0

        print("\n  Sizing:")
        print(f"    Entry:  ${entry:.2f}")
        print(f"    Stop:   ${stop:.2f}  ({stop_pct:.1%} below entry)")
        print(f"    Target: ${target:.2f}  ({(target - entry) / entry:+.1%})")
        if mult < 1.0:
            print(f"    Conviction {conviction:.0f}% -> {mult:.0%} sizing  (full={full_shares} -> {shares} shares)")
        else:
            print(f"    Conviction {conviction:.0f}% -> full size ({shares} shares)")
        print(f"    Position value:  ${proposed_value:,.2f}  ({adjusted_position_pct:.1%} of equity)")
        print(f"    Risk if stopped: ${adjusted_loss_dollars:,.2f}  ({adjusted_loss_pct:.2%} of equity)")
        for w in rec.get('warnings', []):
            print(f"    WARNING: {w}")

        # Per-name concentration check (catches existing same-symbol pushing us over the cap)
        check = validate_concentration(symbol, proposed_value, equity, positions, cap_pct=trade_cap_pct)
        if not check['ok']:
            print(f"\n  CONCENTRATION CHECK FAILED: {check['reason']}")
            skipped.append(symbol)
            ledger.skipped(symbol, "concentration", check['reason'])
            continue
        print(f"  Concentration check OK: {check['would_be_pct']:.1%} of equity (cap {check['cap_pct']:.0%})")

        # Sector concentration check (catches the "3 names, all software" failure mode)
        sec_check = validate_sector_concentration(symbol, proposed_value, equity, positions)
        if not sec_check['ok']:
            print(f"\n  SECTOR CONCENTRATION CHECK FAILED: {sec_check['reason']}")
            skipped.append(symbol)
            ledger.skipped(symbol, "sector", sec_check['reason'])
            continue
        sec_label = sec_check.get('sector') or '?'
        pct = sec_check.get('would_be_pct')
        pct_s = f"{pct:.1%}" if pct is not None else "n/a"
        print(f"  Sector check OK ({sec_label}): {pct_s} of equity (cap {sec_check['cap_pct']:.0%})")

        # Aggregate gross-deployment check (locked 2026-05-18): the hard cap on
        # TOTAL invested. Catches the failure mode where independently-sized
        # per-name positions stack past 100% of equity into accidental margin.
        # NO margin on the automated path.
        gross_check = validate_gross_deployment(symbol, proposed_value, equity, positions)
        if not gross_check['ok']:
            print(f"\n  GROSS DEPLOYMENT CHECK FAILED: {gross_check['reason']}")
            skipped.append(symbol)
            ledger.skipped(symbol, "gross", gross_check['reason'])
            continue
        print(f"  Gross check OK: {gross_check['would_be_pct']:.1%} of equity after add "
              f"(cap {gross_check['cap_pct']:.0%}, headroom ${max(gross_check['headroom'], 0):,.0f})")

        if dry_run:
            print(f"\n  [DRY RUN] would place BRACKET BUY {shares} {symbol} @ ${entry:.2f}  "
                  f"(stop ${stop:.2f}, target ${target:.2f}, gtc)")
            continue

        if not confirm(
            f"\n  Place BRACKET BUY {shares} {symbol} @ ${entry:.2f} "
            f"(stop ${stop:.2f}, target ${target:.2f}, gtc)?  [y/N] ",
            auto_yes,
        ):
            print(f"  Skipped {symbol}")
            skipped.append(symbol)
            ledger.skipped(symbol, "declined", "operator declined the bracket order at confirmation")
            continue

        # Submit bracket order: limit entry + take-profit leg + stop-loss leg, GTC.
        try:
            order = client.submit_order(
                symbol=symbol,
                qty=shares,
                side='buy',
                order_type='limit',
                time_in_force='gtc',
                limit_price=round(entry, 2),
                take_profit=round(target, 2),
                stop_loss_price=round(stop, 2),
            )
            print(f"  Order submitted: id={order.get('id')}, status={order.get('status')}")
        except Exception as e:
            print(f"  ORDER ERROR: {e}")
            skipped.append(symbol)
            ledger.skipped(symbol, "order_error", f"Alpaca submit failed: {e}")
            continue

        ledger.placed(
            symbol, shares, round(entry, 2), order.get('id'),
            detail=f"bracket gtc; stop ${stop:.2f}, target ${target:.2f}; "
                   f"submit status {order.get('status')}",
        )

        # Reflect the new exposure into the running positions list so subsequent
        # trades in the same pass see the per-name and per-sector commitment.
        positions.append({
            'symbol': symbol,
            'market_value': proposed_value,
            'qty': shares,
        })

        # Log the trade in our journal (compact rationale; full text already in latest_strategy.json)
        TradeJournal.log_entry(
            symbol=symbol,
            shares=shares,
            entry_price=entry,
            stop_loss=stop,
            target_price=target,
            rationale=trade.get('rationale', '')[:200],
            source='claude',
        )
        placed.append(symbol)

    # Persist the per-candidate ledger (enhancement #1). Skip on dry-run so
    # local --dry-run tests don't pollute the committed ledger. notify_execute
    # reads this file to explain every placed/skipped name in the daily email.
    if not dry_run:
        ledger.commit()

    # Summary
    print("\n" + "=" * 70)
    print("EXECUTION SUMMARY")
    print("=" * 70)
    print(f"  Placed:  {len(placed)}  {placed if placed else ''}")
    print(f"  Skipped: {len(skipped)} {skipped if skipped else ''}")
    if placed and not dry_run:
        print(f"\n  Placed orders are GTC limit-entry brackets — review Alpaca dashboard.")
        print(f"  If an entry doesn't fill within a few days, consider cancelling it via the dashboard.")
    print()


if __name__ == "__main__":
    # Strategy analysis text from Claude routinely contains → ✓ ★ etc.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Execute trades from latest_strategy.json")
    parser.add_argument('--strategy', default='latest_strategy.json', help='Path to strategy JSON')
    parser.add_argument('--dry-run', action='store_true', help='Show plan, place no orders')
    parser.add_argument('--yes', action='store_true', help='Auto-approve all confirmations (use with care)')
    args = parser.parse_args()

    execute_strategy(strategy_path=args.strategy, dry_run=args.dry_run, auto_yes=args.yes)
