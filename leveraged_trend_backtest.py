"""
Leveraged trend-following backtest (FREE, ETF-level, no API).

The one evidence-backed strategy whose only blocker was tax drag — which Klaas
explicitly waived 2026-05-16. Thesis: apply 2x leverage ONLY in favorable
regimes; T-bills when the trend is down.

  risk-on  (SPY monthly close > SPY 200d SMA): hold SSO (2x S&P 500)
  risk-off (SPY <= 200d SMA)                 : hold BIL (1-3mo T-bills)

Uses REAL SSO prices (2x DAILY-reset ETF) so volatility decay is honestly
captured — NOT synthetic 2x of SPY (which would understate the cost).

Pre-committed gate (LOCKED 2026-05-16, do not move):
  1. OOS pre-tax CAGR > buy-hold SPY by >= 3pp
  2. OOS max DD <= 1.5x buy-hold SPY OOS max DD
  3. Positive in BOTH in-sample and out-of-sample

UPRO (3x) shown informational-only — outside the stated 2x comfort envelope.
"""

from __future__ import annotations

import json
from typing import Dict, List

import numpy as np
import pandas as pd

from phase0_backtest import (
    download_prices, apply_tax, _safe_cagr, INITIAL_CAPITAL, TRANSACTION_COST_BPS,
)

IS_START, IS_END = "2010-01-01", "2018-12-31"
OOS_START, OOS_END = "2019-01-01", "2025-12-31"
SMA_DAYS = 200


def _month_end_days(idx: pd.DatetimeIndex) -> set:
    s = pd.Series(idx, index=idx)
    return set(s.groupby([idx.year, idx.month]).last())


def simulate_trend(lev_etf: str, px: Dict[str, pd.Series], start: str, end: str) -> Dict:
    """2x/3x ETF when SPY>200dMA (monthly), else BIL. Real leveraged-ETF prices."""
    spy = px["SPY"].loc[start:end]
    lev = px[lev_etf].reindex(spy.index).ffill()
    bil = px["BIL"].reindex(spy.index).ffill()
    sma = px["SPY"].rolling(SMA_DAYS).mean().reindex(spy.index)
    days = list(spy.index)
    me = _month_end_days(spy.index)

    holding = None          # 'LEV' or 'BIL'
    shares = 0.0
    cost_price = 0.0
    entry_date = None
    cash = INITIAL_CAPITAL
    exits: List[Dict] = []
    curve = []

    def px_of(tag, d):
        return (lev if tag == "LEV" else bil).loc[d]

    for d in days:
        d_iso = d.date().isoformat()
        if d in me:
            risk_on = (not pd.isna(sma.loc[d])) and (spy.loc[d] > sma.loc[d])
            want = "LEV" if risk_on else "BIL"
            if holding != want:
                if holding is not None:           # sell current
                    p = px_of(holding, d)
                    cash = shares * p * (1 - TRANSACTION_COST_BPS / 10000)
                    exits.append({"date": d_iso, "entry_date": entry_date,
                                  "pnl": (p - cost_price) * shares})
                p = px_of(want, d)                # buy target
                shares = cash * (1 - TRANSACTION_COST_BPS / 10000) / p
                cost_price = p
                entry_date = d_iso
                holding = want
                cash = 0.0
        val = shares * px_of(holding, d) if holding else cash
        curve.append((d, val))

    last = days[-1]
    if holding:
        p = px_of(holding, last)
        exits.append({"date": last.date().isoformat(), "entry_date": entry_date,
                      "pnl": (p - cost_price) * shares})

    eq = pd.Series([v for _, v in curve], index=[d for d, _ in curve])
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    final_pre = float(eq.iloc[-1])

    class _T:
        def __init__(s, ed, exs): s.entry_date = ed; s.exits = exs
    by_e: Dict[str, List[Dict]] = {}
    for ex in exits:
        by_e.setdefault(ex["entry_date"], []).append({"date": ex["date"], "pnl": ex["pnl"]})
    tax = apply_tax([_T(ed, exs) for ed, exs in by_e.items()])

    return {
        "strategy": f"trend_{lev_etf}",
        "cagr_pre": _safe_cagr(final_pre, INITIAL_CAPITAL, years),
        "cagr_post": _safe_cagr(final_pre - tax, INITIAL_CAPITAL, years),
        "max_dd": float(((eq.cummax() - eq) / eq.cummax()).max()),
        "switches": len(exits),
        "final_pre": final_pre,
    }


def buyhold(px: Dict[str, pd.Series], tkr: str, start: str, end: str) -> Dict:
    s = px[tkr].loc[start:end]
    years = (s.index[-1] - s.index[0]).days / 365.25
    final = INITIAL_CAPITAL * float(s.iloc[-1] / s.iloc[0])
    gain = final - INITIAL_CAPITAL
    return {
        "strategy": f"buyhold_{tkr}",
        "cagr_pre": _safe_cagr(final, INITIAL_CAPITAL, years),
        "cagr_post": _safe_cagr(final - (gain * 0.15 if gain > 0 else 0), INITIAL_CAPITAL, years),
        "max_dd": float(((s.cummax() - s) / s.cummax()).max()),
        "switches": 1, "final_pre": final,
    }


def run(px, start, end, label):
    print(f"\n{'='*92}\n{label}  ({start}..{end})\n{'='*92}")
    bh_spy = buyhold(px, "SPY", start, end)
    rows = [
        bh_spy,
        simulate_trend("SSO", px, start, end),     # 2x — the decision variable
        simulate_trend("UPRO", px, start, end),    # 3x — informational only
        buyhold(px, "SSO", start, end),            # 2x buy-and-hold (no trend filter) for contrast
    ]
    print(f"{'strategy':<16}{'CAGR_pre':>10}{'CAGR_post':>11}{'maxDD':>9}{'switches':>10}")
    print("-" * 60)
    for r in rows:
        flag = "  <- 2x decision" if r["strategy"] == "trend_SSO" else (
               "  (3x, outside envelope)" if r["strategy"] == "trend_UPRO" else "")
        print(f"{r['strategy']:<16}{r['cagr_pre']:>9.2%}{r['cagr_post']:>10.2%}"
              f"{r['max_dd']:>8.1%}{r['switches']:>10}{flag}")
    return bh_spy, next(r for r in rows if r["strategy"] == "trend_SSO"), rows


def main():
    print("Leveraged trend-following backtest (free, real leveraged-ETF prices)")
    etfs = ["SPY", "SSO", "UPRO", "BIL"]
    raw = download_prices(etfs, "2009-01-01", "2025-12-31", "leveraged_etfs_2009_2025")
    px = {t: raw[t]["Close"] for t in etfs if t in raw}
    miss = [t for t in etfs if t not in px]
    if miss:
        print(f"Missing ETF data {miss}; aborting.")
        return

    is_bh, is_sso, is_rows = run(px, IS_START, IS_END, "IN-SAMPLE 2010-2018")
    oos_bh, oos_sso, oos_rows = run(px, OOS_START, OOS_END,
                                    "OUT-OF-SAMPLE 2019-2025 (the one that counts)")

    print(f"\n{'='*70}\nPRE-COMMITTED GATE (locked 2026-05-16 — SSO 2x is the decision):")
    g1 = oos_sso["cagr_pre"] >= oos_bh["cagr_pre"] + 0.03
    g2 = oos_sso["max_dd"] <= 1.5 * oos_bh["max_dd"]
    g3 = (is_sso["cagr_pre"] > 0) and (oos_sso["cagr_pre"] > 0)
    print(f"  1. OOS pre-tax CAGR > buyhold SPY +3pp : "
          f"{oos_sso['cagr_pre']:+.2%} vs need >{oos_bh['cagr_pre']+0.03:+.2%}  -> {'PASS' if g1 else 'FAIL'}")
    print(f"  2. OOS maxDD <= 1.5x buyhold SPY maxDD : "
          f"{oos_sso['max_dd']:.1%} vs cap {1.5*oos_bh['max_dd']:.1%}  -> {'PASS' if g2 else 'FAIL'}")
    print(f"  3. Positive IS AND OOS : "
          f"IS {is_sso['cagr_pre']:+.2%}, OOS {oos_sso['cagr_pre']:+.2%}  -> {'PASS' if g3 else 'FAIL'}")
    passed = sum([g1, g2, g3])
    print(f"\nGATE: {passed}/3")
    if passed == 3:
        print("-> CLEARS. Real candidate. Next: wire Alpaca paper for forward test.")
    else:
        print("-> FAILS. The evidence-backed strategy space is now exhausted.")
        print("   Honest answer: buy-hold SPY. Stop optimizing.")

    with open("leveraged_trend_results.json", "w") as f:
        json.dump({"in_sample": is_rows, "out_of_sample": oos_rows,
                   "gate": {"g1": g1, "g2": g2, "g3": g3, "passed": passed}},
                  f, indent=2, default=str)
    print("\nWrote leveraged_trend_results.json")


if __name__ == "__main__":
    main()
