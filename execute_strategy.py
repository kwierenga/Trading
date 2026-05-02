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
    PositionSizer, validate_concentration,
    MAX_POSITION_PCT, MIN_STOP_PCT, MAX_STOP_PCT,
)
from trade_journal import TradeJournal
from market_data import latest_price


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

    for i, trade in enumerate(trades, 1):
        symbol = trade['symbol']
        direction = trade['direction']

        print("=" * 70)
        print(f"TRADE {i}/{len(trades)}: {symbol} {direction}  [{trade.get('holding_period', '?')}]")
        print("=" * 70)
        print(f"  Rationale:      {trade.get('rationale', '')}")
        print(f"  Entry strategy: {trade.get('entry_strategy', '')}")
        print(f"  Exit plan:      {trade.get('exit_plan', '')}")
        print(f"  Conviction:     {trade.get('conviction', 0)}%   R/R: {trade.get('risk_reward_ratio', 0):.2f}")

        if direction != 'BUY':
            print("  [SKIPPING] non-BUY directives are not auto-executed yet (use Alpaca UI for sells/closes).")
            skipped.append(symbol)
            continue

        try:
            entry = float(trade['entry_price'])
            stop = float(trade['stop_loss'])
            target = float(trade['target'])
        except (KeyError, TypeError, ValueError) as e:
            print(f"  ERROR: malformed trade data ({e}) — skipping")
            skipped.append(symbol)
            continue

        if entry <= 0 or stop <= 0 or target <= 0 or stop >= entry or target <= entry:
            print(f"  ERROR: invalid prices (entry ${entry}, stop ${stop}, target ${target}) — skipping")
            skipped.append(symbol)
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
                continue

        # Stop sanity bounds
        stop_pct = (entry - stop) / entry
        if stop_pct < MIN_STOP_PCT:
            print(f"  WARNING: stop {stop_pct:.1%} narrower than {MIN_STOP_PCT:.0%} floor — likely whipsaw risk")
        if stop_pct > MAX_STOP_PCT:
            print(f"  WARNING: stop {stop_pct:.1%} wider than {MAX_STOP_PCT:.0%} ceiling — single-name risk too high")

        # Compute position size (risk-targeted, concentration-capped)
        rec = sizer.calculate_for_trade(symbol, entry, stop)
        if 'error' in rec or rec['recommended_shares'] <= 0:
            print(f"  ERROR: cannot size — {rec.get('error', 'zero shares recommended')}")
            skipped.append(symbol)
            continue

        shares = rec['recommended_shares']
        proposed_value = shares * entry

        print("\n  Sizing:")
        print(f"    Entry:  ${entry:.2f}")
        print(f"    Stop:   ${stop:.2f}  ({stop_pct:.1%} below entry)")
        print(f"    Target: ${target:.2f}  ({(target - entry) / entry:+.1%})")
        print(f"    Shares: {shares}")
        print(f"    Position value:  ${proposed_value:,.2f}  ({rec['position_pct']:.1%} of equity)")
        print(f"    Risk if stopped: ${rec['expected_loss_dollars']:,.2f}  ({rec['expected_loss_pct']:.2%} of equity)")
        for w in rec.get('warnings', []):
            print(f"    WARNING: {w}")

        # Concentration check (catches the case where existing same-symbol position pushes us over the cap)
        check = validate_concentration(symbol, proposed_value, equity, positions)
        if not check['ok']:
            print(f"\n  CONCENTRATION CHECK FAILED: {check['reason']}")
            skipped.append(symbol)
            continue
        print(f"  Concentration check OK: {check['would_be_pct']:.1%} of equity (cap {check['cap_pct']:.0%})")

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
            continue

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
