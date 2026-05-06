"""
Downtrend-mode diagnostic CLI.

Standalone tool for reviewing what the downtrend-mode entry path WOULD propose
on a given day. Does NOT modify morning_routine.py / ai_strategy_enhanced.py /
Alpaca state — just prints what the regime detector and Form 4 cluster filter
return so the strategy can be reviewed end-to-end before any wiring decision.

Usage:
  python downtrend_check.py                       # regime + scan if downtrend
  python downtrend_check.py --force               # always run Form 4 scan
  python downtrend_check.py --tickers AAPL MSFT   # scan a subset (faster)
  python downtrend_check.py --days 14             # custom window (default 30)
"""

import sys

from regime import detect_regime, format_regime
from form4_data import fetch_form4_for_universe
from form4_signals import detect_clusters, format_cluster, WINDOW_DAYS


def main() -> int:
    args = sys.argv[1:]

    force = "--force" in args
    if force:
        args = [a for a in args if a != "--force"]

    days = WINDOW_DAYS
    if "--days" in args:
        i = args.index("--days")
        try:
            days = int(args[i + 1])
        except (IndexError, ValueError):
            print("--days requires an integer argument")
            return 1
        args = args[:i] + args[i + 2:]

    tickers = None
    if "--tickers" in args:
        i = args.index("--tickers")
        tickers = [a.upper() for a in args[i + 1:]]
        args = args[:i]

    if args:
        print(f"Unknown args: {args}")
        return 1

    print("=" * 70)
    print("DOWNTREND-MODE DIAGNOSTIC")
    print("=" * 70)

    regime = detect_regime("SPY")
    if regime is None:
        print("\nFailed to detect regime — SPY data unavailable.")
        return 1
    print()
    print(format_regime(regime))

    if not regime.is_downtrend and not force:
        print("\nNot in downtrend; existing trend-mode entry path remains active.")
        print("Pass --force to run Form 4 scan anyway (preview).")
        return 0

    if tickers is None:
        from sp500_universe import get_sp500_tickers
        tickers = get_sp500_tickers()
        print(f"\nScanning S&P 500 ({len(tickers)} tickers) for Form 4 clusters, last {days} days...")
        print("(This takes ~3-5 minutes the first time; subsequent runs faster after CIK cache.)")
    else:
        print(f"\nScanning {len(tickers)} ticker(s) for Form 4 clusters, last {days} days...")

    txns = fetch_form4_for_universe(tickers, since_days=days)
    sigs = detect_clusters(txns)

    print(f"\n{'=' * 70}")
    print(f"FORM 4 CLUSTER SIGNALS ({len(sigs)} qualifying)")
    print("=" * 70)
    if not sigs:
        print("\nNo clusters meet spec §3 criteria in window.")
    else:
        for s in sigs:
            print()
            print(format_cluster(s))

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
