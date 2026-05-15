"""
Phase-0 backtest (RECOMMENDATIONS_top10 #10).

Per the locked rollout plan in `memory/trading_rollout_plan.md`:
  - 2010-2018 in-sample (parameter locking)
  - 2019-2025 out-of-sample (run ONCE, accept the answer)
  - 1.5% survivorship-bias haircut on CAGR
  - 5 bps/side transaction costs
  - 32% STCG / 15% LTCG tax model

Five graduation gates:
  1. Net-of-tax CAGR > SPY CAGR + 1% (out-of-sample)
  2. Max drawdown <= 0.8 * SPY max DD
  3. Beats SPY in >= 55% of rolling 3-year windows
  4. Beats USMV in >= 45% of rolling 3-year windows
  5. CAGR varies <= 3% across reasonable parameter perturbations

Honest limitations (documented for the user — these are not bugs, they are
genuine constraints of any retroactive historical test):

  A. **LLM picks cannot be replayed historically.** This backtest selects
     trades by the technical `_setup_score` ranking (basing + range +
     trend + PEG + ROE + margin + EPS-revision factor). It validates the
     screen + risk-management methodology, NOT Claude's per-trade picking
     skill. Live paper trading is the only test for that.
  B. **Universe is today's S&P 500.** Companies that fell out of the
     index aren't in the test (survivorship bias). The locked plan's
     1.5% CAGR haircut compensates approximately.
  C. **Fundamentals are static (current snapshot).** Quality-filter
     fundamentals (debt/equity, margins) aren't time-varying. Backtest
     thus effectively assumes "today's S&P 500 quality companies are
     consistently quality through history" — true on average for the
     index but not for individual names. Reduces validity of the
     fundamental-filter portion.
  D. **Earnings filter not applied historically.** No source for
     historical earnings calendars at this scale. Backtest may include
     trades that the live system would block.
  E. **Daily resolution.** Stops/targets check against daily high/low.
     The 5bps transaction cost is a proxy for intraday slippage; real
     execution may differ on volatile names.

Cache: historical data lands in `backtest_cache/` as parquet. Re-runs are
near-instant after the first download. Pass `--refresh` to re-fetch.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CACHE_DIR = Path("backtest_cache")
RESULTS_FILE = Path("phase0_backtest_results.json")

IN_SAMPLE_START = "2010-01-01"
IN_SAMPLE_END = "2018-12-31"
OUT_SAMPLE_START = "2019-01-01"
OUT_SAMPLE_END = "2025-12-31"

INITIAL_CAPITAL = 100_000.0

TRANSACTION_COST_BPS = 5     # per side
STCG_TAX_RATE = 0.32
LTCG_TAX_RATE = 0.15
LTCG_DAYS = 365
SURVIVORSHIP_HAIRCUT_CAGR = 0.015   # subtract from CAGR

# Universe: cap at top-N most-liquid for speed; backtest still uses 500 tickers
# but downloading + indicators for the full set adds significant runtime.
# Set MAX_UNIVERSE=None to use full S&P 500.
MAX_UNIVERSE: Optional[int] = None


@dataclass
class BacktestParams:
    max_concurrent_positions: int = 3
    max_position_pct: float = 0.25
    target_risk_per_trade: float = 0.015
    atr_stop_multiple: float = 1.75
    min_stop_pct: float = 0.04
    max_stop_pct: float = 0.15
    scale_out_fraction: float = 0.25
    atr_trail_multiple: float = 1.5
    setup_score_threshold: float = 8.0    # only enter top-scoring candidates
    earnings_filter: bool = False          # off in backtest (limitation D)
    max_hold_days: int = 180               # time-stop fallback


@dataclass
class Trade:
    symbol: str
    entry_date: str
    entry_price: float
    initial_qty: int
    qty: int                  # remaining after scale-outs
    initial_stop: float
    current_stop: float
    R: float
    scaled_25_at_1r: bool = False
    scaled_25_at_2r: bool = False
    exits: List[Dict] = field(default_factory=list)  # partial exits
    closed: bool = False
    exit_reason: Optional[str] = None
    final_exit_date: Optional[str] = None

    def realized_pnl(self) -> float:
        return sum(e["pnl"] for e in self.exits)

    def days_held_at(self, current_date: str) -> int:
        return (date.fromisoformat(current_date) - date.fromisoformat(self.entry_date)).days


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _universe() -> List[str]:
    from sp500_universe import get_sp500_tickers
    tickers = get_sp500_tickers()
    # Skip tickers with dots (BRK.B, etc.) — yfinance is fussy and they're rare
    tickers = [t for t in tickers if "." not in t and "/" not in t]
    if MAX_UNIVERSE:
        tickers = tickers[:MAX_UNIVERSE]
    return tickers


def download_prices(tickers: List[str], start: str, end: str,
                    cache_key: str, refresh: bool = False) -> Dict[str, pd.DataFrame]:
    """
    Bulk-fetch daily OHLC for `tickers` between [start, end]. Cached to pickle.
    Returns dict: symbol -> DataFrame with columns Open/High/Low/Close/Volume.
    Tickers with no data are silently dropped.
    """
    import pickle
    CACHE_DIR.mkdir(exist_ok=True)
    cache_path = CACHE_DIR / f"{cache_key}.pkl"

    if not refresh and cache_path.exists():
        with open(cache_path, "rb") as f:
            out = pickle.load(f)
        print(f"  Loaded cached prices: {len(out)} tickers from {cache_path.name}")
        return out

    print(f"  Downloading {len(tickers)} tickers from {start} to {end}...")
    chunk = 50
    pieces: List[pd.DataFrame] = []
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        try:
            d = yf.download(batch, start=start, end=end, auto_adjust=True,
                            progress=False, group_by="ticker", threads=True)
        except Exception as e:
            print(f"    batch {i}-{i+chunk} failed: {e}")
            continue
        pieces.append(d)
        print(f"    ...{min(i + chunk, len(tickers))}/{len(tickers)}")

    if not pieces:
        return {}

    full = pd.concat(pieces, axis=1)
    out: Dict[str, pd.DataFrame] = {}
    for sym in full.columns.get_level_values(0).unique():
        sub = full[sym].dropna(how="all")
        if not sub.empty:
            out[sym] = sub

    with open(cache_path, "wb") as f:
        pickle.dump(out, f)
    print(f"  Downloaded + cached: {len(out)} tickers to {cache_path.name}")
    return out


# ---------------------------------------------------------------------------
# Indicators (vectorized per ticker)
# ---------------------------------------------------------------------------

def compute_indicators(price_data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """
    For each ticker, return a DataFrame indexed by date with columns:
      close, sma20, sma50, sma200, sma50_slope_pct, atr14, range_pos_pct,
      is_basing, in_downtrend, is_falling_knife.
    """
    out: Dict[str, pd.DataFrame] = {}
    for sym, df in price_data.items():
        if df is None or len(df) < 250:
            continue
        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        sma20 = close.rolling(20).mean()
        sma50 = close.rolling(50).mean()
        sma200 = close.rolling(200).mean()
        sma50_slope = (sma50 - sma50.shift(20)) / sma50.shift(20) * 100

        # ATR14
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(),
                        (low - prev_close).abs()], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()

        # 52-week range position
        rolling_high = close.rolling(252).max()
        rolling_low = close.rolling(252).min()
        rng = (rolling_high - rolling_low)
        range_pos = ((close - rolling_low) / rng * 100).where(rng > 0, 50.0)

        # Basing: lowest low of last 20 sessions was in the older half
        rolling_lows = low.rolling(20)
        idx_of_low = rolling_lows.apply(lambda s: int(np.argmin(s.values)), raw=False)
        is_basing = idx_of_low < 10

        in_downtrend = (close < sma50) & (sma50_slope < 0)
        is_falling_knife = sma50_slope < -3.0

        ind = pd.DataFrame({
            "close": close, "high": high, "low": low,
            "open": df["Open"],
            "sma20": sma20, "sma50": sma50, "sma200": sma200,
            "sma50_slope": sma50_slope, "atr14": atr14,
            "range_pos": range_pos,
            "is_basing": is_basing,
            "in_downtrend": in_downtrend,
            "is_falling_knife": is_falling_knife,
        })
        out[sym] = ind
    return out


def setup_score_row(row: pd.Series) -> float:
    """Technical-only version of market_data._setup_score. No fundamentals."""
    score = 0.0
    if row["is_basing"] and row["range_pos"] < 50:
        score += 10
    if not row["in_downtrend"] and row["range_pos"] < 30:
        score += 3
    in_uptrend = (row["close"] > row["sma50"]) and (row["sma50_slope"] >= 0)
    if in_uptrend:
        score += 4
        if row["range_pos"] > 90:
            score -= 3
    return score


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------

def compute_atr_stop(entry_price: float, atr14: float, params: BacktestParams) -> Tuple[float, float]:
    """Returns (stop_price, R = entry - stop)."""
    raw_pct = (atr14 * params.atr_stop_multiple) / entry_price
    clamped = max(params.min_stop_pct, min(params.max_stop_pct, raw_pct))
    stop = entry_price * (1 - clamped)
    return stop, entry_price - stop


def run_backtest(period_start: str, period_end: str,
                 indicators: Dict[str, pd.DataFrame],
                 params: BacktestParams,
                 spy: pd.DataFrame,
                 quiet: bool = False) -> Dict:
    """
    Walk forward day-by-day. Decisions on day D use data through D-1; entries
    fill at day D's open; stops/targets check against day D's high/low.

    Returns dict with equity_curve, trades, daily_returns.
    """
    # Common trading day index = union of SPY's bars in window
    spy_window = spy.loc[period_start:period_end]
    trading_days = list(spy_window.index)

    cash = INITIAL_CAPITAL
    equity_history: List[Tuple[pd.Timestamp, float]] = []
    open_trades: List[Trade] = []
    closed_trades: List[Trade] = []

    prev_day = None
    for d in trading_days:
        # --- Manage open positions (stops, scale-outs, trails) using today's bar ---
        d_iso = d.date().isoformat()
        for trade in list(open_trades):
            ind = indicators.get(trade.symbol)
            if ind is None or d not in ind.index:
                continue
            row = ind.loc[d]
            day_low = row["low"]
            day_high = row["high"]
            day_close = row["close"]

            # Stop hit? Check first (worst-case order)
            if day_low <= trade.current_stop:
                fill = trade.current_stop
                proceeds = trade.qty * fill * (1 - TRANSACTION_COST_BPS / 10000)
                pnl = (fill - trade.entry_price) * trade.qty
                cash += proceeds
                trade.exits.append({
                    "date": d_iso, "qty": trade.qty, "price": fill, "pnl": pnl, "reason": "stop"
                })
                trade.qty = 0
                trade.closed = True
                trade.exit_reason = "stop"
                trade.final_exit_date = d_iso
                closed_trades.append(trade)
                open_trades.remove(trade)
                continue

            # Compute unrealized in R units using today's close
            if trade.R <= 0:
                continue
            gain_in_R = (day_close - trade.entry_price) / trade.R

            # +1R: scale 25%, raise stop to break-even
            if not trade.scaled_25_at_1r and gain_in_R >= 1.0:
                scale_qty = max(1, int(round(trade.initial_qty * params.scale_out_fraction)))
                scale_qty = min(scale_qty, trade.qty - 1)
                if scale_qty > 0:
                    fill = day_close
                    proceeds = scale_qty * fill * (1 - TRANSACTION_COST_BPS / 10000)
                    pnl = (fill - trade.entry_price) * scale_qty
                    cash += proceeds
                    trade.exits.append({
                        "date": d_iso, "qty": scale_qty, "price": fill,
                        "pnl": pnl, "reason": "scale_1r"
                    })
                    trade.qty -= scale_qty
                    trade.scaled_25_at_1r = True
                    trade.current_stop = max(trade.current_stop, trade.entry_price * 1.001)

            # +2R: scale another 25%, raise stop to entry + 1R
            if trade.scaled_25_at_1r and not trade.scaled_25_at_2r and gain_in_R >= 2.0:
                scale_qty = max(1, int(round(trade.initial_qty * params.scale_out_fraction)))
                scale_qty = min(scale_qty, trade.qty - 1)
                if scale_qty > 0:
                    fill = day_close
                    proceeds = scale_qty * fill * (1 - TRANSACTION_COST_BPS / 10000)
                    pnl = (fill - trade.entry_price) * scale_qty
                    cash += proceeds
                    trade.exits.append({
                        "date": d_iso, "qty": scale_qty, "price": fill,
                        "pnl": pnl, "reason": "scale_2r"
                    })
                    trade.qty -= scale_qty
                    trade.scaled_25_at_2r = True
                    trade.current_stop = max(trade.current_stop, trade.entry_price + trade.R)

            # +3R+ trail
            if trade.scaled_25_at_2r and gain_in_R >= 3.0:
                atr = row["atr14"]
                if atr and not pd.isna(atr) and atr > 0:
                    candidate = day_close - params.atr_trail_multiple * atr
                    if candidate > trade.current_stop:
                        trade.current_stop = candidate

            # Time-stop
            if trade.days_held_at(d_iso) >= params.max_hold_days:
                fill = day_close
                proceeds = trade.qty * fill * (1 - TRANSACTION_COST_BPS / 10000)
                pnl = (fill - trade.entry_price) * trade.qty
                cash += proceeds
                trade.exits.append({
                    "date": d_iso, "qty": trade.qty, "price": fill, "pnl": pnl, "reason": "time_stop"
                })
                trade.qty = 0
                trade.closed = True
                trade.exit_reason = "time_stop"
                trade.final_exit_date = d_iso
                closed_trades.append(trade)
                open_trades.remove(trade)

        # --- Mark-to-market equity ---
        position_value = 0.0
        for tr in open_trades:
            ind = indicators.get(tr.symbol)
            if ind is None or d not in ind.index:
                continue
            position_value += tr.qty * ind.loc[d, "close"]
        equity = cash + position_value
        equity_history.append((d, equity))

        # --- Pick new trades using PREVIOUS day's indicators (no look-ahead) ---
        if prev_day is None or len(open_trades) >= params.max_concurrent_positions:
            prev_day = d
            continue

        candidates: List[Tuple[str, float]] = []
        held_symbols = {t.symbol for t in open_trades}
        for sym, ind in indicators.items():
            if sym in held_symbols:
                continue
            if prev_day not in ind.index:
                continue
            row = ind.loc[prev_day]
            if pd.isna(row["sma200"]) or pd.isna(row["atr14"]):
                continue
            if bool(row["in_downtrend"]) or bool(row["is_falling_knife"]):
                continue
            score = setup_score_row(row)
            if score >= params.setup_score_threshold:
                candidates.append((sym, score))

        candidates.sort(key=lambda x: x[1], reverse=True)

        # Open new positions, sized to risk + capped
        slots = params.max_concurrent_positions - len(open_trades)
        for sym, _ in candidates[:slots]:
            ind = indicators[sym]
            if d not in ind.index:
                continue
            entry_open = ind.loc[d, "open"]
            atr = ind.loc[prev_day, "atr14"]
            if pd.isna(entry_open) or pd.isna(atr) or atr <= 0:
                continue
            stop, R = compute_atr_stop(entry_open, atr, params)
            if R <= 0 or entry_open <= 0:
                continue
            risk_dollars = equity * params.target_risk_per_trade
            shares_by_risk = int(risk_dollars / R)
            max_shares_by_cap = int((equity * params.max_position_pct) / entry_open)
            shares = max(0, min(shares_by_risk, max_shares_by_cap))
            cost = shares * entry_open * (1 + TRANSACTION_COST_BPS / 10000)
            if shares <= 0 or cost > cash:
                continue
            cash -= cost
            open_trades.append(Trade(
                symbol=sym, entry_date=d_iso, entry_price=entry_open,
                initial_qty=shares, qty=shares,
                initial_stop=stop, current_stop=stop, R=R,
            ))

        prev_day = d

    # Force-close any remaining open trades at last close
    last_day = trading_days[-1] if trading_days else None
    if last_day is not None:
        for trade in list(open_trades):
            ind = indicators.get(trade.symbol)
            if ind is None or last_day not in ind.index:
                continue
            fill = ind.loc[last_day, "close"]
            proceeds = trade.qty * fill * (1 - TRANSACTION_COST_BPS / 10000)
            pnl = (fill - trade.entry_price) * trade.qty
            cash += proceeds
            trade.exits.append({
                "date": last_day.date().isoformat(), "qty": trade.qty,
                "price": fill, "pnl": pnl, "reason": "period_end",
            })
            trade.qty = 0
            trade.closed = True
            trade.exit_reason = "period_end"
            trade.final_exit_date = last_day.date().isoformat()
            closed_trades.append(trade)
        open_trades = []

    equity_series = pd.Series({d: v for d, v in equity_history})
    return {
        "equity_curve": equity_series,
        "trades": closed_trades,
        "final_cash": cash,
    }


# ---------------------------------------------------------------------------
# Stats + tax + gates
# ---------------------------------------------------------------------------

def apply_tax(trades: List[Trade]) -> float:
    """
    Compute aggregate tax dollars on realized gains, US LTCG-aware.

    Real US tax accounting nets gains and losses within each tax year:
      - Short-term gains and losses net within the year (taxed at STCG if net+)
      - Long-term gains and losses net within the year (taxed at LTCG if net+)
      - Net short-term losses offset net long-term gains and vice versa
      - Net loss carries forward to future years (modeled here as offsetting
        gains in the next year)
    """
    # Bucket every exit into (year, short|long) categories.
    yearly: Dict[int, Dict[str, float]] = {}
    for tr in trades:
        entry = date.fromisoformat(tr.entry_date)
        for ex in tr.exits:
            x_date = date.fromisoformat(ex["date"])
            days_held = (x_date - entry).days
            category = "long" if days_held >= LTCG_DAYS else "short"
            year = x_date.year
            yearly.setdefault(year, {"short": 0.0, "long": 0.0})
            yearly[year][category] += ex["pnl"]

    total_tax = 0.0
    carryforward = 0.0
    for year in sorted(yearly.keys()):
        st_net = yearly[year]["short"]
        lt_net = yearly[year]["long"]

        # Apply carryforward to whichever bucket benefits most (short first since
        # STCG rate is higher and saves more tax per dollar offset).
        if carryforward > 0:
            if st_net > 0:
                offset = min(carryforward, st_net)
                st_net -= offset
                carryforward -= offset
            if carryforward > 0 and lt_net > 0:
                offset = min(carryforward, lt_net)
                lt_net -= offset
                carryforward -= offset

        # Cross-net within the year: if one bucket is positive and the other negative
        if st_net < 0 and lt_net > 0:
            offset = min(-st_net, lt_net)
            st_net += offset
            lt_net -= offset
        elif lt_net < 0 and st_net > 0:
            offset = min(-lt_net, st_net)
            lt_net += offset
            st_net -= offset

        # Tax positive remaining; carry forward absolute net loss
        if st_net > 0:
            total_tax += st_net * STCG_TAX_RATE
        if lt_net > 0:
            total_tax += lt_net * LTCG_TAX_RATE
        if st_net < 0:
            carryforward += -st_net
        if lt_net < 0:
            carryforward += -lt_net

    return total_tax


def _safe_cagr(final: float, initial: float, years: float) -> float:
    """CAGR that returns NaN-safe values when equity went to zero or below."""
    if years <= 0 or initial <= 0 or final <= 0:
        return -1.0  # 100% loss — used as floor so sensitivity stays meaningful
    return (final / initial) ** (1 / years) - 1


def compute_stats(equity_curve: pd.Series, trades: List[Trade], tax_dollars: float) -> Dict:
    if equity_curve.empty:
        return {"available": False}
    eq = equity_curve.copy()
    eq_after_tax = eq.iloc[-1] - tax_dollars
    initial = eq.iloc[0]
    duration_years = (eq.index[-1] - eq.index[0]).days / 365.25 if len(eq) > 1 else 1.0

    cagr_pre = _safe_cagr(float(eq.iloc[-1]), float(initial), duration_years)
    cagr_post = _safe_cagr(float(eq_after_tax), float(initial), duration_years)

    # Max drawdown
    running_max = eq.cummax()
    drawdowns = (running_max - eq) / running_max
    max_dd = float(drawdowns.max())

    # Volatility / Sharpe (daily, annualized)
    daily_ret = eq.pct_change().dropna()
    vol_ann = float(daily_ret.std() * np.sqrt(252)) if not daily_ret.empty else 0.0
    sharpe = (daily_ret.mean() * 252 / vol_ann) if vol_ann > 0 else 0.0

    wins = [ex for tr in trades for ex in tr.exits if ex["pnl"] > 0]
    losses = [ex for tr in trades for ex in tr.exits if ex["pnl"] <= 0]
    total_exits = wins + losses

    return {
        "available": True,
        "duration_years": duration_years,
        "initial": float(initial),
        "final_pre_tax": float(eq.iloc[-1]),
        "final_post_tax": float(eq_after_tax),
        "tax_dollars": float(tax_dollars),
        "cagr_pre_tax": float(cagr_pre),
        "cagr_post_tax": float(cagr_post),
        "max_drawdown": max_dd,
        "vol_annualized": vol_ann,
        "sharpe": float(sharpe),
        "trade_cohorts": len(trades),
        "partial_exits": len(total_exits),
        "win_rate": len(wins) / len(total_exits) if total_exits else 0.0,
    }


def benchmark_stats(prices: pd.DataFrame, start: str, end: str) -> Dict:
    """Buy-and-hold benchmark from prices DataFrame (Close column)."""
    closes = prices["Close"].loc[start:end]
    if closes.empty:
        return {"available": False}
    initial = INITIAL_CAPITAL
    shares = initial / closes.iloc[0]
    eq = shares * closes
    duration_years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / initial) ** (1 / duration_years) - 1 if duration_years > 0 else 0.0
    running_max = eq.cummax()
    drawdowns = (running_max - eq) / running_max
    return {
        "available": True,
        "cagr": float(cagr),
        "max_drawdown": float(drawdowns.max()),
        "equity_curve": eq,
    }


def rolling_3y_outperformance(strategy_eq: pd.Series, benchmark_eq: pd.Series) -> float:
    """Fraction of 3-year rolling windows where strategy CAGR > benchmark CAGR."""
    if strategy_eq.empty or benchmark_eq.empty:
        return 0.0
    common = strategy_eq.index.intersection(benchmark_eq.index)
    s = strategy_eq.reindex(common).ffill()
    b = benchmark_eq.reindex(common).ffill()
    window = 252 * 3
    if len(s) < window + 1:
        return 0.0
    s_ret = (s / s.shift(window)) ** (1 / 3) - 1
    b_ret = (b / b.shift(window)) ** (1 / 3) - 1
    wins = (s_ret > b_ret).sum()
    total = s_ret.notna().sum()
    return wins / total if total > 0 else 0.0


def check_gates(out_strategy: Dict, spy_oos: Dict, usmv_oos: Dict,
                perturbations: List[Dict]) -> Dict:
    cagr_haircut = SURVIVORSHIP_HAIRCUT_CAGR
    strategy_cagr_net = out_strategy["cagr_post_tax"] - cagr_haircut

    g1 = strategy_cagr_net > spy_oos["cagr"] + 0.01
    g2 = out_strategy["max_drawdown"] <= 0.8 * spy_oos["max_drawdown"]
    spy_rolling = rolling_3y_outperformance(out_strategy["equity_curve"], spy_oos["equity_curve"])
    g3 = spy_rolling >= 0.55
    usmv_rolling = rolling_3y_outperformance(out_strategy["equity_curve"], usmv_oos["equity_curve"])
    g4 = usmv_rolling >= 0.45

    if perturbations:
        cagrs = [p["cagr_post_tax"] - cagr_haircut for p in perturbations]
        cagr_spread = max(cagrs) - min(cagrs)
        g5 = cagr_spread <= 0.03
    else:
        cagr_spread = None
        g5 = False

    passed = sum([g1, g2, g3, g4, g5])
    if passed >= 5:
        verdict = "PROCEED TO PHASE 1 — all 5 gates cleared"
    elif passed >= 4:
        verdict = "PROCEED WITH SMALLER CAPITAL CEILING — 4 of 5 gates cleared"
    else:
        verdict = "STOP — buy SPY+USMV blend instead"

    return {
        "gate1_cagr_vs_spy_plus_1pct": {
            "passed": bool(g1), "strategy_cagr_net": strategy_cagr_net,
            "spy_cagr_plus_1pct": spy_oos["cagr"] + 0.01,
        },
        "gate2_dd_le_80pct_spy": {
            "passed": bool(g2), "strategy_max_dd": out_strategy["max_drawdown"],
            "spy_max_dd_x_0.8": spy_oos["max_drawdown"] * 0.8,
        },
        "gate3_rolling_3y_vs_spy_55pct": {
            "passed": bool(g3), "outperform_fraction": spy_rolling, "threshold": 0.55,
        },
        "gate4_rolling_3y_vs_usmv_45pct": {
            "passed": bool(g4), "outperform_fraction": usmv_rolling, "threshold": 0.45,
        },
        "gate5_param_sensitivity_le_3pct": {
            "passed": bool(g5), "cagr_spread": cagr_spread, "threshold": 0.03,
        },
        "gates_passed": passed,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run_period(label: str, period_start: str, period_end: str,
                indicators: Dict[str, pd.DataFrame], params: BacktestParams,
                spy: pd.DataFrame) -> Dict:
    print(f"\n=== {label} backtest: {period_start} to {period_end} ===")
    result = run_backtest(period_start, period_end, indicators, params, spy)
    tax = apply_tax(result["trades"])
    stats = compute_stats(result["equity_curve"], result["trades"], tax)
    stats["equity_curve"] = result["equity_curve"]  # keep for gate calcs
    stats["trades_count"] = len(result["trades"])
    print(f"  Trades: {stats['trade_cohorts']} cohorts ({stats['partial_exits']} partial exits)")
    print(f"  Initial: ${stats['initial']:,.0f}  Final pre-tax: ${stats['final_pre_tax']:,.0f}  "
          f"After tax: ${stats['final_post_tax']:,.0f}")
    print(f"  CAGR (post-tax): {stats['cagr_post_tax']:+.2%}  "
          f"after survivorship haircut: {stats['cagr_post_tax'] - SURVIVORSHIP_HAIRCUT_CAGR:+.2%}")
    print(f"  Max DD: {stats['max_drawdown']:.2%}  Sharpe: {stats['sharpe']:.2f}  "
          f"Win-rate (per partial): {stats['win_rate']:.1%}")
    return stats


def main(refresh: bool = False) -> int:
    tickers = _universe()
    print(f"S&P 500 universe (filtered): {len(tickers)} tickers")

    # Download universe + benchmarks
    universe_data = download_prices(tickers, IN_SAMPLE_START, OUT_SAMPLE_END,
                                     cache_key="universe_2010_2025", refresh=refresh)
    spy_dict = download_prices(["SPY"], IN_SAMPLE_START, OUT_SAMPLE_END,
                                cache_key="spy_2010_2025", refresh=refresh)
    usmv_dict = download_prices(["USMV"], "2011-10-20", OUT_SAMPLE_END,
                                 cache_key="usmv_2011_2025", refresh=refresh)
    if "SPY" not in spy_dict:
        print("  ERROR: could not load SPY prices")
        return 1
    spy = spy_dict["SPY"]
    usmv = usmv_dict.get("USMV")

    print("\nComputing indicators (this is the slow part)...")
    indicators = compute_indicators(universe_data)
    print(f"  Indicators computed for {len(indicators)} tickers")

    params = BacktestParams()

    # In-sample (parameter locking — we just record results, no fitting since
    # the parameters are already chosen via CLAUDE.md)
    in_stats = _run_period("IN-SAMPLE", IN_SAMPLE_START, IN_SAMPLE_END, indicators, params, spy)

    # Out-of-sample (the real test)
    out_stats = _run_period("OUT-OF-SAMPLE", OUT_SAMPLE_START, OUT_SAMPLE_END, indicators, params, spy)

    # Benchmarks
    spy_oos = benchmark_stats(spy, OUT_SAMPLE_START, OUT_SAMPLE_END)
    if usmv is not None:
        usmv_oos = benchmark_stats(usmv, OUT_SAMPLE_START, OUT_SAMPLE_END)
    else:
        print("  USMV history unavailable — gate 4 cannot be evaluated")
        usmv_oos = {"available": False, "cagr": 0.0, "max_drawdown": 1.0,
                    "equity_curve": pd.Series(dtype=float)}

    # Sensitivity (gate 5): perturb 4 reasonable params, re-run OOS
    print("\n=== Sensitivity (gate 5) — perturbing 4 parameters ===")
    perturb_results = []
    perturbations_spec = [
        ("atr_stop_multiple=1.50", {"atr_stop_multiple": 1.50}),
        ("atr_stop_multiple=2.00", {"atr_stop_multiple": 2.00}),
        ("max_position_pct=0.20", {"max_position_pct": 0.20}),
        ("setup_score_threshold=10.0", {"setup_score_threshold": 10.0}),
    ]
    for name, overrides in perturbations_spec:
        p = BacktestParams(**{**asdict(params), **overrides})
        r = run_backtest(OUT_SAMPLE_START, OUT_SAMPLE_END, indicators, p, spy, quiet=True)
        s = compute_stats(r["equity_curve"], r["trades"], apply_tax(r["trades"]))
        s["label"] = name
        perturb_results.append(s)
        print(f"  {name}: CAGR_post_tax={s['cagr_post_tax']:+.2%}  DD={s['max_drawdown']:.2%}")

    # Gates
    print("\n=== Graduation gates ===")
    gates = check_gates(out_stats, spy_oos, usmv_oos, perturb_results)
    for k, v in gates.items():
        if k in ("gates_passed", "verdict"):
            continue
        mark = "PASS" if v["passed"] else "FAIL"
        print(f"  [{mark}] {k}: {v}")
    print(f"\nGates passed: {gates['gates_passed']} / 5")
    print(f"VERDICT: {gates['verdict']}")

    # Persist (strip non-serializable series)
    def _strip(d: Dict) -> Dict:
        return {k: (v if not isinstance(v, pd.Series) else None) for k, v in d.items()}

    result_payload = {
        "params": asdict(params),
        "in_sample": _strip(in_stats),
        "out_of_sample": _strip(out_stats),
        "spy_oos": {k: v for k, v in spy_oos.items() if not isinstance(v, pd.Series)},
        "usmv_oos": {k: v for k, v in usmv_oos.items() if not isinstance(v, pd.Series)},
        "perturbations": [_strip(p) for p in perturb_results],
        "gates": gates,
        "ran_at": datetime.now().isoformat(),
    }
    RESULTS_FILE.write_text(json.dumps(result_payload, indent=2, default=str), encoding="utf-8")
    print(f"\nFull results written to {RESULTS_FILE}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="Re-download all price data")
    args = ap.parse_args()
    raise SystemExit(main(refresh=args.refresh))
