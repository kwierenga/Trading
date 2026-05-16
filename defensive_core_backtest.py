"""
Defensive Core + TLH + Regime backtest (FREE, ETF-level, no API, no survivorship
problem — SPY/VTI/BIL are major surviving ETFs with full history).

Tests the 2026-05-15 pivot strategy's MECHANICAL spine (no LLM yet, per the
pre-committed gate condition 4: if mechanical doesn't clear, LLM won't save it).

Variants:
  buyhold_spy      : buy SPY day 1, sell only at period end (ultra tax-efficient — the honest hurdle)
  spy_usmv         : 70/30 SPY/USMV buy-hold (the old fallback blend)
  regime           : SPY when SPY>200dSMA else BIL (Faber GTAA spine), no TLH
  buyhold_tlh      : always invested, TLH SPY<->VTI on -5% lots (isolates TLH alpha)
  regime_tlh       : the full mechanical strategy (regime defense + TLH)

Tax: phase0_backtest.apply_tax (US year-bucketed loss-netting + carryforward).
Compares PRE and POST tax. The strategy's whole pitch is the after-tax + drawdown
story, so post-tax is the number that matters.

Honest modeling notes:
  - TLH at ETF level only harvests when the whole market is down -> CONSERVATIVE
    vs real direct-indexing TLH (individual names diverge far more often).
  - SPY<->VTI rotation is wash-sale-safe (S&P 500 vs total US market = not
    substantially identical).
  - Regime round-trips realize gains -> real tax drag, captured via apply_tax.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from phase0_backtest import (
    download_prices, apply_tax, _safe_cagr,
    INITIAL_CAPITAL, TRANSACTION_COST_BPS,
)

IS_START, IS_END = "2010-01-01", "2018-12-31"
OOS_START, OOS_END = "2019-01-01", "2025-12-31"
TLH_THRESHOLD = -0.05      # harvest a lot down 5%+
SMA_DAYS = 200             # Faber-style trend filter


@dataclass
class Lot:
    ticker: str
    shares: float
    cost_price: float
    buy_date: str


def _month_end_days(idx: pd.DatetimeIndex) -> List[pd.Timestamp]:
    s = pd.Series(idx, index=idx)
    return list(s.groupby([idx.year, idx.month]).last())


def _sell_all(lots: List[Lot], price: float, d_iso: str, exits: List[Dict]) -> float:
    proceeds = 0.0
    for lot in lots:
        gross = lot.shares * price
        proceeds += gross * (1 - TRANSACTION_COST_BPS / 10000)
        exits.append({"date": d_iso, "pnl": (price - lot.cost_price) * lot.shares,
                      "entry_date": lot.buy_date})
    lots.clear()
    return proceeds


def simulate(mode: str, px: Dict[str, pd.Series], start: str, end: str) -> Dict:
    spy = px["SPY"].loc[start:end]
    vti = px["VTI"].reindex(spy.index).ffill()
    bil = px["BIL"].reindex(spy.index).ffill()
    sma = px["SPY"].rolling(SMA_DAYS).mean().reindex(spy.index)
    days = list(spy.index)
    me = set(_month_end_days(spy.index))

    cash = INITIAL_CAPITAL
    eq_lots: List[Lot] = []          # equity sleeve (SPY or VTI lots)
    bil_lots: List[Lot] = []         # defensive sleeve
    cur_etf = "SPY"
    exits: List[Dict] = []
    equity_curve = []

    def price_of(tkr, d):
        return {"SPY": spy, "VTI": vti, "BIL": bil}[tkr].loc[d]

    # initial buy
    p0 = spy.iloc[0]
    sh = (cash * (1 - TRANSACTION_COST_BPS / 10000)) / p0
    eq_lots.append(Lot("SPY", sh, p0, days[0].date().isoformat()))
    cash = 0.0

    for d in days:
        d_iso = d.date().isoformat()
        spx, vpx, bpx = spy.loc[d], vti.loc[d], bil.loc[d]

        if d in me and mode != "buyhold_spy" and mode != "spy_usmv":
            risk_on = (not pd.isna(sma.loc[d])) and (spx > sma.loc[d])

            # --- Regime switching (regime, regime_tlh) ---
            if mode in ("regime", "regime_tlh"):
                if not risk_on and eq_lots:
                    cash += _sell_all(eq_lots, price_of(cur_etf, d), d_iso, exits)
                    bsh = (cash * (1 - TRANSACTION_COST_BPS / 10000)) / bpx
                    bil_lots.append(Lot("BIL", bsh, bpx, d_iso)); cash = 0.0
                elif risk_on and bil_lots:
                    cash += _sell_all(bil_lots, bpx, d_iso, exits)
                    epx = price_of(cur_etf, d)
                    esh = (cash * (1 - TRANSACTION_COST_BPS / 10000)) / epx
                    eq_lots.append(Lot(cur_etf, esh, epx, d_iso)); cash = 0.0

            # --- TLH (buyhold_tlh, regime_tlh) ---
            if mode in ("buyhold_tlh", "regime_tlh") and eq_lots:
                cur_px = price_of(cur_etf, d)
                harvestable = [l for l in eq_lots if (cur_px / l.cost_price - 1) <= TLH_THRESHOLD]
                if harvestable:
                    proceeds = 0.0
                    for lot in harvestable:
                        proceeds += lot.shares * cur_px * (1 - TRANSACTION_COST_BPS / 10000)
                        exits.append({"date": d_iso,
                                      "pnl": (cur_px - lot.cost_price) * lot.shares,
                                      "entry_date": lot.buy_date})
                        eq_lots.remove(lot)
                    alt = "VTI" if cur_etf == "SPY" else "SPY"
                    apx = price_of(alt, d)
                    ash = (proceeds * (1 - TRANSACTION_COST_BPS / 10000)) / apx
                    eq_lots.append(Lot(alt, ash, apx, d_iso))
                    cur_etf = alt

        # mark-to-market
        val = cash
        for l in eq_lots:
            val += l.shares * price_of(l.ticker, d)
        for l in bil_lots:
            val += l.shares * bpx
        equity_curve.append((d, val))

    # close out at period end
    last = days[-1]
    for l in eq_lots:
        exits.append({"date": last.date().isoformat(),
                      "pnl": (price_of(l.ticker, last) - l.cost_price) * l.shares,
                      "entry_date": l.buy_date})
    for l in bil_lots:
        exits.append({"date": last.date().isoformat(),
                      "pnl": (bil.loc[last] - l.cost_price) * l.shares,
                      "entry_date": l.buy_date})

    eq = pd.Series([v for _, v in equity_curve], index=[d for d, _ in equity_curve])
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    final_pre = float(eq.iloc[-1])

    # Build pseudo-trades for apply_tax (it expects objects w/ entry_date + exits list)
    class _T:
        def __init__(self, ed, exs): self.entry_date = ed; self.exits = exs
    by_entry: Dict[str, List[Dict]] = {}
    for ex in exits:
        by_entry.setdefault(ex["entry_date"], []).append({"date": ex["date"], "pnl": ex["pnl"]})
    trades = [_T(ed, exs) for ed, exs in by_entry.items()]
    tax = apply_tax(trades)
    final_post = final_pre - tax

    return {
        "mode": mode,
        "cagr_pre": _safe_cagr(final_pre, INITIAL_CAPITAL, years),
        "cagr_post": _safe_cagr(final_post, INITIAL_CAPITAL, years),
        "max_dd": float(((eq.cummax() - eq) / eq.cummax()).max()),
        "tax": tax,
        "final_pre": final_pre,
        "n_realizations": len(exits),
    }


def _spy_usmv(px, start, end):
    spy = px["SPY"].loc[start:end]; usmv = px["USMV"].reindex(spy.index).ffill()
    s0, u0 = spy.iloc[0], usmv.iloc[0]
    val = 0.7 * INITIAL_CAPITAL * (spy / s0) + 0.3 * INITIAL_CAPITAL * (usmv / u0)
    years = (val.index[-1] - val.index[0]).days / 365.25
    # one terminal realization, ~tax-efficient buy-hold
    gain = float(val.iloc[-1]) - INITIAL_CAPITAL
    tax = gain * 0.15 if gain > 0 else 0.0
    return {"mode": "spy_usmv", "cagr_pre": _safe_cagr(float(val.iloc[-1]), INITIAL_CAPITAL, years),
            "cagr_post": _safe_cagr(float(val.iloc[-1]) - tax, INITIAL_CAPITAL, years),
            "max_dd": float(((val.cummax() - val) / val.cummax()).max()),
            "tax": tax, "final_pre": float(val.iloc[-1]), "n_realizations": 1}


def run_period(px, start, end, label):
    print(f"\n{'='*100}\n{label}  ({start}..{end})\n{'='*100}")
    rows = []
    for m in ["buyhold_spy", "regime", "buyhold_tlh", "regime_tlh"]:
        rows.append(simulate(m, px, start, end))
    rows.append(_spy_usmv(px, start, end))

    print(f"{'mode':<14}{'CAGR_pre':>10}{'CAGR_post':>11}{'maxDD':>9}{'tax$':>14}{'realiz':>8}")
    print("-" * 70)
    for r in rows:
        print(f"{r['mode']:<14}{r['cagr_pre']:>9.2%}{r['cagr_post']:>10.2%}"
              f"{r['max_dd']:>8.1%}{r['tax']:>14,.0f}{r['n_realizations']:>8}")

    bh = next(r for r in rows if r["mode"] == "buyhold_spy")
    full = next(r for r in rows if r["mode"] == "regime_tlh")
    tlh_only = next(r for r in rows if r["mode"] == "buyhold_tlh")
    regime_only = next(r for r in rows if r["mode"] == "regime")
    print("-" * 70)
    print(f"GATE CHECK (vs the pre-committed adapted gate):")
    g1 = full["cagr_post"] >= bh["cagr_post"]
    g2 = full["max_dd"] <= 0.75 * bh["max_dd"]
    g3 = full["cagr_post"] > regime_only["cagr_post"]   # TLH adds positively on top of regime
    print(f"  1. regime_tlh post-tax >= buyhold_spy post-tax : {full['cagr_post']:+.2%} vs {bh['cagr_post']:+.2%}  -> {'PASS' if g1 else 'FAIL'}")
    print(f"  2. regime_tlh maxDD <= 0.75x buyhold_spy maxDD : {full['max_dd']:.1%} vs {0.75*bh['max_dd']:.1%}  -> {'PASS' if g2 else 'FAIL'}")
    print(f"  3. TLH contributes positively (regime_tlh > regime): {full['cagr_post']:+.2%} vs {regime_only['cagr_post']:+.2%}  -> {'PASS' if g3 else 'FAIL'}")
    return {"label": label, "rows": rows,
            "gate": {"g1_post_vs_buyhold": g1, "g2_drawdown": g2, "g3_tlh_positive": g3}}


def main():
    print("Defensive Core + TLH + Regime — MECHANICAL backtest (free, ETF-level)")
    etfs = ["SPY", "VTI", "BIL", "USMV"]
    raw = download_prices(etfs, "2009-01-01", "2025-12-31", "defensive_etfs_2009_2025")
    px = {t: raw[t]["Close"] for t in etfs if t in raw}
    missing = [t for t in etfs if t not in px]
    if missing:
        print(f"Missing ETF data: {missing}; aborting.")
        return

    is_res = run_period(px, IS_START, IS_END, "IN-SAMPLE 2010-2018")
    oos_res = run_period(px, OOS_START, OOS_END, "OUT-OF-SAMPLE 2019-2025 (the one that counts)")

    gates = oos_res["gate"]
    passed = sum(1 for v in gates.values() if v)
    print(f"\n{'='*70}\nOOS GATE RESULT: {passed}/3 mechanical conditions passed")
    if passed == 3:
        print("-> Mechanical spine CLEARS. Proceed to wire Alpaca paper + test LLM regime layer.")
    else:
        print("-> Mechanical spine does NOT clear. Per pre-committed gate cond.4,")
        print("   the LLM won't save it. Honest stop point — discuss before spending more.")
    with open("defensive_core_results.json", "w") as f:
        json.dump({"in_sample": is_res, "out_of_sample": oos_res}, f, indent=2, default=str)
    print("\nWrote defensive_core_results.json")


if __name__ == "__main__":
    main()
