"""
Exit-strategy sweep (FREE — rules-based picks, zero API cost).

Isolates the single highest-leverage optimization lever identified in the
2026-05-15 diagnostic: the exit methodology. Picking is held IDENTICAL to
phase0_backtest (technical setup_score, top-N) so the ONLY variable across
runs is how positions are exited. Compares 6 exit modes over 2019-2025 OOS
against the baseline + SPY.

Diagnostic that motivated this: 294/296 trades exited at "stop", max winner
only +33%, trade-level win rate 47.6%. Hypothesis: the +1R/+2R scale-out
plus tight 1.5xATR trail structurally caps winners. Letting winners run
should lift CAGR if the picking has any edge.

Post-tax uses the correct phase0_backtest.apply_tax (loss-netting).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from phase0_backtest import (
    BacktestParams, _universe, download_prices, compute_indicators,
    setup_score_row, compute_atr_stop, apply_tax, _safe_cagr,
    INITIAL_CAPITAL, TRANSACTION_COST_BPS, SURVIVORSHIP_HAIRCUT_CAGR,
    OUT_SAMPLE_START, OUT_SAMPLE_END,
)


@dataclass
class STrade:
    symbol: str
    entry_date: str
    entry_price: float
    initial_qty: int
    qty: int
    initial_stop: float
    current_stop: float
    R: float
    high_water: float = 0.0          # highest close since entry (chandelier)
    scaled_1r: bool = False
    scaled_2r: bool = False
    scaled_3r: bool = False
    exits: List[Dict] = field(default_factory=list)
    closed: bool = False
    exit_reason: Optional[str] = None
    entry_idx: int = 0

    def realized_pnl(self) -> float:
        return sum(e["pnl"] for e in self.exits)


# Exit modes. Each is applied per-trade per-day AFTER the hard stop check.
EXIT_MODES = [
    "baseline_scale",   # current production: +1R/+2R scale 25%, +3R trail 1.5xATR
    "runner_wide",      # no scale; at +1R stop->BE; then trail 3.0xATR (let it run)
    "runner_25atr",     # no scale; at +1R stop->BE; then trail 2.5xATR
    "late_scale_3r",    # +1R stop->BE; at +3R scale 50%; rest trail 2.5xATR
    "chandelier",       # no scale; stop = high_water - 3.0xATR, ratchet up only
    "weinstein_ma",     # no scale; initial ATR stop; exit on close < SMA50
]


def _close(tr: STrade, qty: int, price: float, d_iso: str, reason: str, cash_ref: List[float]):
    proceeds = qty * price * (1 - TRANSACTION_COST_BPS / 10000)
    pnl = (price - tr.entry_price) * qty
    cash_ref[0] += proceeds
    tr.exits.append({"date": d_iso, "qty": qty, "price": price, "pnl": pnl, "reason": reason})
    tr.qty -= qty


def manage_trade(tr: STrade, row: pd.Series, d_iso: str, mode: str,
                 params: BacktestParams, cash_ref: List[float]) -> bool:
    """Apply exit logic for `mode`. Returns True if the trade fully closed today."""
    day_low = row["low"]; day_high = row["high"]; day_close = row["close"]
    atr = row["atr14"]
    tr.high_water = max(tr.high_water, day_close)

    # Hard stop check first (worst-case ordering) — common to all modes
    if day_low <= tr.current_stop:
        _close(tr, tr.qty, tr.current_stop, d_iso, "stop", cash_ref)
        tr.closed = True; tr.exit_reason = "stop"
        return True

    if tr.R <= 0 or pd.isna(atr) or atr <= 0:
        return False
    gain_R = (day_close - tr.entry_price) / tr.R

    if mode == "baseline_scale":
        if not tr.scaled_1r and gain_R >= 1.0:
            q = min(max(1, round(tr.initial_qty * params.scale_out_fraction)), tr.qty - 1)
            if q > 0:
                _close(tr, q, day_close, d_iso, "scale_1r", cash_ref)
                tr.scaled_1r = True
                tr.current_stop = max(tr.current_stop, tr.entry_price * 1.001)
        if tr.scaled_1r and not tr.scaled_2r and gain_R >= 2.0:
            q = min(max(1, round(tr.initial_qty * params.scale_out_fraction)), tr.qty - 1)
            if q > 0:
                _close(tr, q, day_close, d_iso, "scale_2r", cash_ref)
                tr.scaled_2r = True
                tr.current_stop = max(tr.current_stop, tr.entry_price + tr.R)
        if tr.scaled_2r and gain_R >= 3.0:
            cand = day_close - 1.5 * atr
            if cand > tr.current_stop:
                tr.current_stop = cand

    elif mode in ("runner_wide", "runner_25atr"):
        mult = 3.0 if mode == "runner_wide" else 2.5
        if gain_R >= 1.0 and tr.current_stop < tr.entry_price:
            tr.current_stop = tr.entry_price * 1.001  # lock breakeven
        if gain_R >= 1.0:
            cand = day_close - mult * atr
            if cand > tr.current_stop:
                tr.current_stop = cand

    elif mode == "late_scale_3r":
        if gain_R >= 1.0 and tr.current_stop < tr.entry_price:
            tr.current_stop = tr.entry_price * 1.001
        if not tr.scaled_3r and gain_R >= 3.0:
            q = min(max(1, round(tr.initial_qty * 0.5)), tr.qty - 1)
            if q > 0:
                _close(tr, q, day_close, d_iso, "scale_3r", cash_ref)
                tr.scaled_3r = True
        if gain_R >= 3.0:
            cand = day_close - 2.5 * atr
            if cand > tr.current_stop:
                tr.current_stop = cand

    elif mode == "chandelier":
        cand = tr.high_water - 3.0 * atr
        if cand > tr.current_stop:
            tr.current_stop = cand

    elif mode == "weinstein_ma":
        sma50 = row.get("sma50")
        if sma50 is not None and not pd.isna(sma50) and day_close < sma50:
            _close(tr, tr.qty, day_close, d_iso, "ma_break", cash_ref)
            tr.closed = True; tr.exit_reason = "ma_break"
            return True

    return tr.qty <= 0


def simulate(mode: str, indicators: Dict[str, pd.DataFrame], spy: pd.DataFrame,
             params: BacktestParams) -> Dict:
    spy_window = spy.loc[OUT_SAMPLE_START:OUT_SAMPLE_END]
    trading_days = list(spy_window.index)

    cash_ref = [INITIAL_CAPITAL]
    equity_hist: List[Tuple[pd.Timestamp, float]] = []
    open_tr: List[STrade] = []
    closed_tr: List[STrade] = []
    prev_day = None

    for i, d in enumerate(trading_days):
        d_iso = d.date().isoformat()
        for tr in list(open_tr):
            ind = indicators.get(tr.symbol)
            if ind is None or d not in ind.index:
                continue
            if manage_trade(tr, ind.loc[d], d_iso, mode, params, cash_ref):
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

        held = {t.symbol for t in open_tr}
        cands: List[Tuple[str, float]] = []
        for sym, ind in indicators.items():
            if sym in held or prev_day not in ind.index:
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

    # Force-close at period end
    last = trading_days[-1]
    for tr in list(open_tr):
        ind = indicators.get(tr.symbol)
        if ind is None or last not in ind.index:
            continue
        _close(tr, tr.qty, ind.loc[last, "close"], last.date().isoformat(), "period_end", cash_ref)
        tr.closed = True; tr.exit_reason = "period_end"; closed_tr.append(tr)

    eq = pd.Series([v for _, v in equity_hist], index=[d for d, _ in equity_hist])
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    final_pre = float(eq.iloc[-1])
    tax = apply_tax(closed_tr)
    final_post = final_pre - tax
    cagr_pre = _safe_cagr(final_pre, INITIAL_CAPITAL, years)
    cagr_post = _safe_cagr(final_post, INITIAL_CAPITAL, years)
    max_dd = float(((eq.cummax() - eq) / eq.cummax()).max())

    wins = [e["pnl"] for tr in closed_tr for e in tr.exits if e["pnl"] > 0]
    losses = [e["pnl"] for tr in closed_tr for e in tr.exits if e["pnl"] <= 0]
    # Trade-level (final disposition)
    twins = sum(1 for tr in closed_tr if tr.realized_pnl() > 0)
    avg_w = np.mean([tr.realized_pnl() / (tr.initial_qty * tr.entry_price)
                     for tr in closed_tr if tr.realized_pnl() > 0]) if twins else 0
    losers = [tr for tr in closed_tr if tr.realized_pnl() <= 0]
    avg_l = np.mean([tr.realized_pnl() / (tr.initial_qty * tr.entry_price)
                     for tr in losers]) if losers else 0
    max_win_pct = max((tr.realized_pnl() / (tr.initial_qty * tr.entry_price)
                       for tr in closed_tr), default=0)

    return {
        "mode": mode,
        "cagr_pre": cagr_pre,
        "cagr_post": cagr_post,
        "cagr_post_haircut": cagr_post - SURVIVORSHIP_HAIRCUT_CAGR,
        "max_dd": max_dd,
        "trades": len(closed_tr),
        "trade_win_rate": twins / len(closed_tr) if closed_tr else 0,
        "avg_win_pct": float(avg_w),
        "avg_loss_pct": float(avg_l),
        "win_loss_ratio": float(abs(avg_w / avg_l)) if avg_l else 0,
        "max_winner_pct": float(max_win_pct),
        "tax": tax,
    }


def main():
    print("=" * 90)
    print("EXIT-STRATEGY SWEEP — rules-based picks held constant, only exit logic varies")
    print(f"OOS {OUT_SAMPLE_START}..{OUT_SAMPLE_END}, FREE (no API)")
    print("=" * 90)

    tickers = _universe()
    price = download_prices(tickers, "2018-01-01", "2025-12-31", "universe_2010_2025")
    spy = download_prices(["SPY"], "2018-01-01", "2025-12-31", "spy_2010_2025").get("SPY")
    indicators = compute_indicators(price)
    params = BacktestParams()

    spy_w = spy.loc[OUT_SAMPLE_START:OUT_SAMPLE_END]["Close"]
    spy_cagr = _safe_cagr(float(spy_w.iloc[-1]), float(spy_w.iloc[0]),
                          (spy_w.index[-1] - spy_w.index[0]).days / 365.25)
    spy_dd = float(((spy_w.cummax() - spy_w) / spy_w.cummax()).max())

    rows = []
    for mode in EXIT_MODES:
        print(f"\n  running {mode}...")
        rows.append(simulate(mode, indicators, spy, params))

    print("\n" + "=" * 110)
    hdr = f"{'mode':<16}{'CAGR_pre':>10}{'CAGR_post':>11}{'post-HC':>9}{'maxDD':>8}{'trades':>8}{'winrate':>8}{'avgW':>8}{'avgL':>8}{'W/L':>6}{'maxWin':>8}"
    print(hdr)
    print("-" * 110)
    for r in rows:
        print(f"{r['mode']:<16}{r['cagr_pre']:>9.2%}{r['cagr_post']:>10.2%}{r['cagr_post_haircut']:>8.2%}"
              f"{r['max_dd']:>7.1%}{r['trades']:>8}{r['trade_win_rate']:>7.1%}"
              f"{r['avg_win_pct']:>7.1%}{r['avg_loss_pct']:>7.1%}{r['win_loss_ratio']:>6.2f}{r['max_winner_pct']:>7.0%}")
    print("-" * 110)
    print(f"{'SPY (benchmark)':<16}{spy_cagr:>9.2%}{'—':>10}{'—':>8}{spy_dd:>7.1%}")
    print("=" * 110)

    best = max(rows, key=lambda r: r["cagr_post_haircut"])
    base = next(r for r in rows if r["mode"] == "baseline_scale")
    print(f"\nBaseline post-tax-haircut CAGR: {base['cagr_post_haircut']:+.2%}")
    print(f"Best mode: {best['mode']} at {best['cagr_post_haircut']:+.2%} "
          f"({(best['cagr_post_haircut'] - base['cagr_post_haircut'])*100:+.1f}pp vs baseline)")
    print(f"SPY bar to beat (gate1: SPY+1%): {spy_cagr + 0.01:+.2%}")

    import json
    with open("exit_sweep_results.json", "w") as f:
        json.dump({"rows": rows, "spy_cagr": spy_cagr, "spy_dd": spy_dd}, f, indent=2, default=str)
    print("\nWrote exit_sweep_results.json")


if __name__ == "__main__":
    main()
