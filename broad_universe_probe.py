"""
Broad-universe DIRECTIONAL PROBE (yfinance prices — survivorship-biased UP).

NOT a gate input. Question it answers: does expanding beyond the S&P 500 to the
broad US universe (mktcap>$1B, ADV>$5M, quality floor) even *plausibly* help,
using the best exit (late_scale_3r) and rules-based picks?

  - If even this optimistically-biased result doesn't beat SPY -> expansion is
    dead; save the SEP money; go back to S&P-500 picking optimization.
  - If it looks promising -> worth ~$30-50 for Sharadar SEP to get a clean,
    survivorship-safe number that CAN gate real money.

Also measures + reports the survivorship gap (SF1-qualifying tickers vs how many
yfinance actually serves) so "biased" is a number, not a hand-wave.

Post-tax uses phase0_backtest.apply_tax (loss-netting). Universe spec v2 LOCKED
in CLAUDE.md / memory — thresholds are NOT tuned here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Set

import numpy as np
import pandas as pd

from phase0_backtest import (
    BacktestParams, download_prices, compute_indicators, setup_score_row,
    compute_atr_stop, apply_tax, _safe_cagr,
    INITIAL_CAPITAL, TRANSACTION_COST_BPS, SURVIVORSHIP_HAIRCUT_CAGR,
    OUT_SAMPLE_START, OUT_SAMPLE_END,
)
from exit_sweep import STrade, manage_trade
from sharadar_client import CACHE_DIR

BROAD_SF1_CACHE = CACHE_DIR / "sharadar_sf1_broad_2018_2025.pkl"
BROAD_PRICE_CACHE = "universe_broad_2018_2025"   # download_prices cache key
EXIT_MODE = "late_scale_3r"                       # winner from exit_sweep
MKTCAP_FLOOR = 1.0e9
ADV_FLOOR = 5.0e6                                 # trailing-20d avg dollar volume


def ever_investable_tickers(sf1: pd.DataFrame) -> List[str]:
    """Tickers whose SF1 marketcap exceeded the $1B floor at least once in-window."""
    g = sf1.groupby("ticker")["marketcap"].max()
    return sorted(g[g >= MKTCAP_FLOOR].index.tolist())


def build_pit_universe(sf1: pd.DataFrame, price_data: Dict[str, pd.DataFrame],
                       trading_days: List[pd.Timestamp]) -> Dict[pd.Timestamp, Set[str]]:
    """Point-in-time eligible set per trading day: mktcap>$1B (most-recent filing
    before the day) AND trailing-20d avg dollar volume > $5M."""
    # Pre-index SF1 by ticker, sorted by datekey, for fast as-of lookups.
    sf1 = sf1.sort_values("datekey")
    mc_by_tkr = {t: g[["datekey", "marketcap"]].reset_index(drop=True)
                 for t, g in sf1.groupby("ticker")}

    # Pre-compute trailing-20d dollar volume per ticker (Close * Volume).
    dollarvol: Dict[str, pd.Series] = {}
    for t, df in price_data.items():
        if df is None or df.empty or "Volume" not in df:
            continue
        dv = (df["Close"] * df["Volume"]).rolling(20).mean()
        dollarvol[t] = dv

    universe: Dict[pd.Timestamp, Set[str]] = {}
    for d in trading_days:
        eligible: Set[str] = set()
        for t, dv in dollarvol.items():
            if d not in dv.index:
                continue
            adv = dv.loc[d]
            if pd.isna(adv) or adv < ADV_FLOOR:
                continue
            mc = mc_by_tkr.get(t)
            if mc is None:
                continue
            prior = mc[mc["datekey"] < d]
            if prior.empty:
                continue
            if prior["marketcap"].iloc[-1] >= MKTCAP_FLOOR:
                eligible.add(t)
        universe[d] = eligible
    return universe


def measure_survivorship_gap(sf1: pd.DataFrame, price_data: Dict[str, pd.DataFrame],
                             sample_dates: List[pd.Timestamp]) -> Dict:
    """For sample dates: how many $1B+ SF1 tickers does yfinance actually serve?"""
    served = set(price_data.keys())
    rows = []
    for d in sample_dates:
        prior = sf1[sf1["datekey"] < d]
        qualifying = set(prior.groupby("ticker")["marketcap"].last()
                         .pipe(lambda s: s[s >= MKTCAP_FLOOR]).index)
        have = qualifying & served
        miss = len(qualifying) - len(have)
        rows.append({
            "date": d.date().isoformat(),
            "sf1_qualifying": len(qualifying),
            "yfinance_has": len(have),
            "missing": miss,
            "missing_pct": round(100 * miss / len(qualifying), 1) if qualifying else 0.0,
        })
    return {"by_date": rows,
            "avg_missing_pct": round(float(np.mean([r["missing_pct"] for r in rows])), 1)}


def run_probe(indicators: Dict[str, pd.DataFrame], spy: pd.DataFrame,
              universe: Dict[pd.Timestamp, Set[str]], params: BacktestParams) -> Dict:
    spy_window = spy.loc[OUT_SAMPLE_START:OUT_SAMPLE_END]
    trading_days = list(spy_window.index)
    cash_ref = [INITIAL_CAPITAL]
    equity_hist = []
    open_tr: List[STrade] = []
    closed_tr: List[STrade] = []
    prev_day = None

    for i, d in enumerate(trading_days):
        d_iso = d.date().isoformat()
        for tr in list(open_tr):
            ind = indicators.get(tr.symbol)
            if ind is None or d not in ind.index:
                continue
            if manage_trade(tr, ind.loc[d], d_iso, EXIT_MODE, params, cash_ref):
                tr.closed = True
                tr.exit_reason = tr.exit_reason or "stop"
                closed_tr.append(tr); open_tr.remove(tr)

        pos_val = sum(tr.qty * indicators[tr.symbol].loc[d, "close"]
                      for tr in open_tr
                      if tr.symbol in indicators and d in indicators[tr.symbol].index)
        equity = cash_ref[0] + pos_val
        equity_hist.append((d, equity))

        if prev_day is None or len(open_tr) >= params.max_concurrent_positions:
            prev_day = d
            continue

        eligible = universe.get(prev_day, set())
        held = {t.symbol for t in open_tr}
        cands = []
        for sym in eligible:
            if sym in held:
                continue
            ind = indicators.get(sym)
            if ind is None or prev_day not in ind.index:
                continue
            row = ind.loc[prev_day]
            if pd.isna(row["sma200"]) or pd.isna(row["atr14"]):
                continue
            if bool(row["in_downtrend"]) or bool(row["is_falling_knife"]):
                continue
            sc = setup_score_row(row)
            if sc >= params.setup_score_threshold:
                cands.append((sym, sc))
        cands.sort(key=lambda x: x[1], reverse=True)

        slots = params.max_concurrent_positions - len(open_tr)
        for sym, _ in cands[:slots]:
            ind = indicators[sym]
            if d not in ind.index:
                continue
            entry = ind.loc[d, "open"]
            atr = ind.loc[prev_day, "atr14"]
            if pd.isna(entry) or pd.isna(atr) or atr <= 0:
                continue
            stop, R = compute_atr_stop(entry, atr, params)
            if R <= 0 or entry <= 0:
                continue
            shares = max(0, min(int((equity * params.target_risk_per_trade) / R),
                                int((equity * params.max_position_pct) / entry)))
            cost = shares * entry * (1 + TRANSACTION_COST_BPS / 10000)
            if shares <= 0 or cost > cash_ref[0]:
                continue
            cash_ref[0] -= cost
            open_tr.append(STrade(symbol=sym, entry_date=d_iso, entry_price=entry,
                                  initial_qty=shares, qty=shares, initial_stop=stop,
                                  current_stop=stop, R=R, high_water=entry, entry_idx=i))
        prev_day = d

    last = trading_days[-1]
    for tr in list(open_tr):
        ind = indicators.get(tr.symbol)
        if ind is None or last not in ind.index:
            continue
        px = ind.loc[last, "close"]
        proceeds = tr.qty * px * (1 - TRANSACTION_COST_BPS / 10000)
        cash_ref[0] += proceeds
        tr.exits.append({"date": last.date().isoformat(), "qty": tr.qty,
                         "price": px, "pnl": (px - tr.entry_price) * tr.qty,
                         "reason": "period_end"})
        tr.closed = True; tr.exit_reason = "period_end"; closed_tr.append(tr)

    eq = pd.Series([v for _, v in equity_hist], index=[d for d, _ in equity_hist])
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    final_pre = float(eq.iloc[-1])
    tax = apply_tax(closed_tr)
    final_post = final_pre - tax
    return {
        "cagr_pre": _safe_cagr(final_pre, INITIAL_CAPITAL, years),
        "cagr_post": _safe_cagr(final_post, INITIAL_CAPITAL, years),
        "cagr_post_haircut": _safe_cagr(final_post, INITIAL_CAPITAL, years) - SURVIVORSHIP_HAIRCUT_CAGR,
        "max_dd": float(((eq.cummax() - eq) / eq.cummax()).max()),
        "trades": len(closed_tr),
        "tax": tax,
    }


def main():
    print("=" * 80)
    print("BROAD-UNIVERSE DIRECTIONAL PROBE (yfinance, survivorship-biased UP)")
    print("NOT a gate input — see memory/trading_optimization_project.md")
    print("=" * 80)

    if not BROAD_SF1_CACHE.exists():
        print(f"Missing {BROAD_SF1_CACHE}. Run download_broad_sf1.py first.")
        return
    sf1 = pd.read_pickle(BROAD_SF1_CACHE)
    print(f"Broad SF1: {len(sf1):,} rows, {sf1['ticker'].nunique()} tickers")

    tickers = ever_investable_tickers(sf1)
    print(f"Ever-investable (mktcap >= $1B at some point): {len(tickers)} tickers")

    price_data = download_prices(tickers, "2018-01-01", "2025-12-31",
                                 BROAD_PRICE_CACHE)
    print(f"yfinance served {len(price_data)}/{len(tickers)} tickers "
          f"({100*len(price_data)/len(tickers):.0f}%)")

    spy = download_prices(["SPY"], "2018-01-01", "2025-12-31", "spy_2010_2025").get("SPY")
    indicators = compute_indicators(price_data)
    spy_window = spy.loc[OUT_SAMPLE_START:OUT_SAMPLE_END]
    trading_days = list(spy_window.index)

    # Survivorship gap on quarterly sample dates
    sample = trading_days[::63]  # ~quarterly
    gap = measure_survivorship_gap(sf1, price_data, sample)
    print(f"\nSURVIVORSHIP GAP (avg across {len(sample)} quarterly samples): "
          f"{gap['avg_missing_pct']}% of $1B+ tickers missing from yfinance")

    print("\nBuilding point-in-time universe (this is the slow part)...")
    universe = build_pit_universe(sf1, price_data, trading_days)
    sizes = [len(v) for v in universe.values()]
    print(f"  Universe size: avg {np.mean(sizes):.0f}, min {min(sizes)}, max {max(sizes)} eligible/day")

    params = BacktestParams()
    print(f"\nRunning probe (exit={EXIT_MODE})...")
    res = run_probe(indicators, spy, universe, params)

    spy_w = spy_window["Close"]
    spy_cagr = _safe_cagr(float(spy_w.iloc[-1]), float(spy_w.iloc[0]),
                          (spy_w.index[-1] - spy_w.index[0]).days / 365.25)

    print("\n" + "=" * 80)
    print(f"  Broad-universe probe   pre-tax CAGR : {res['cagr_pre']:+.2%}")
    print(f"                         post-tax     : {res['cagr_post']:+.2%}")
    print(f"                         post-haircut : {res['cagr_post_haircut']:+.2%}")
    print(f"                         max DD       : {res['max_dd']:.1%}")
    print(f"                         trades       : {res['trades']}")
    print(f"  SPY benchmark          CAGR         : {spy_cagr:+.2%}")
    print("=" * 80)
    print(f"\n  S&P-500 best (late_scale_3r, rules) was: +3.86% pre / -0.22% post-HC")
    print(f"  SURVIVORSHIP CAVEAT: ~{gap['avg_missing_pct']}% of qualifying names missing")
    print(f"  -> result is biased UP. NOT a gate input. Directional only.")

    verdict = ("PROMISING — broad universe plausibly helps; worth Sharadar SEP "
               "for a clean gating run" if res["cagr_pre"] > spy_cagr
               else "WEAK — even biased-up it doesn't beat SPY; expansion likely dead")
    print(f"\n  PROBE READ: {verdict}")

    with open("broad_universe_probe_results.json", "w") as f:
        json.dump({"result": res, "spy_cagr": spy_cagr,
                   "survivorship_gap": gap, "verdict": verdict}, f, indent=2, default=str)
    print("\nWrote broad_universe_probe_results.json")


if __name__ == "__main__":
    main()
