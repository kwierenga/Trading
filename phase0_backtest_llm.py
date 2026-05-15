"""
Phase-0 backtest with Claude (Sonnet 4.6) in the picking loop.

Same trade simulation + 5-gate evaluation as phase0_backtest.py.
What's different: picks come from a weekly Sonnet AM call (a prompt that mirrors
the live `ai_strategy_enhanced` shape), not the technical `setup_score_row`.
Fundamentals are point-in-time from Sharadar SF1 (filed-date alignment via
`datekey`), so the LLM sees the same kind of data it sees in production but
without lookahead bias.

Verdict logic is pre-committed in memory/trading_phase0_llm_verdict_logic.md:
  >=4/5 gates -> Aug 1 real-money flip on
  2-3/5      -> defer to end of Phase 1
  0-1/5      -> STOP, $3k to SPY+USMV

Cost ceiling: $50 API. Abort if exceeded.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import time
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Load .env eagerly so ANTHROPIC_API_KEY is available even if all data caches hit.
load_dotenv()

# Reuse the rules-based harness wherever possible.
from phase0_backtest import (
    BacktestParams,
    Trade,
    CACHE_DIR,
    INITIAL_CAPITAL,
    TRANSACTION_COST_BPS,
    STCG_TAX_RATE,
    LTCG_TAX_RATE,
    LTCG_DAYS,
    SURVIVORSHIP_HAIRCUT_CAGR,
    download_prices,
    compute_indicators,
    compute_atr_stop,
    _universe,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LLM_RESULTS_FILE = Path("phase0_backtest_llm_results.json")
LLM_TRADES_FILE = Path("phase0_backtest_llm_trades.json")
CHECKPOINT_FILE = Path("phase0_backtest_llm_checkpoint.pkl")

OUT_SAMPLE_START = "2019-01-01"
OUT_SAMPLE_END = "2025-12-31"

# Hard cost ceiling per the verdict-logic memory. Abort if total API spend exceeds.
COST_CEILING_USD = 50.0

# Pricing for Claude Sonnet 4.6 (per million tokens).
SONNET_INPUT_PER_MTOK = 3.0
SONNET_OUTPUT_PER_MTOK = 15.0
# Cached input gets the standard 90% discount.
SONNET_CACHED_INPUT_PER_MTOK = 0.30

# Max candidates rendered in the prompt. Matches the live system (MAX_CANDIDATES_FOR_PROMPT=30).
MAX_CANDIDATES_FOR_PROMPT = 30

# Setup-score floor for inclusion in the prompt. Mirrors what the live screen does
# (only show Claude things that already pass a basic technical filter).
SCREEN_SCORE_MIN = 3.0


# ---------------------------------------------------------------------------
# Snapshot construction at historical date
# ---------------------------------------------------------------------------

def _sf1_lookup(sf1: pd.DataFrame, ticker: str, as_of: pd.Timestamp) -> Optional[pd.Series]:
    """Most-recent SF1 row for `ticker` filed strictly before `as_of` (point-in-time)."""
    sub = sf1[(sf1["ticker"] == ticker) & (sf1["datekey"] < as_of)]
    if sub.empty:
        return None
    return sub.sort_values("datekey").iloc[-1]


def _yoy_change(sf1: pd.DataFrame, ticker: str, as_of: pd.Timestamp,
                field: str) -> Optional[float]:
    """YoY change of `field` from 4 quarters ago to the most recent filing before as_of."""
    sub = sf1[(sf1["ticker"] == ticker) & (sf1["datekey"] < as_of)].sort_values("datekey")
    if len(sub) < 5:
        return None
    now = sub.iloc[-1].get(field)
    then = sub.iloc[-5].get(field)
    if now is None or then is None or pd.isna(now) or pd.isna(then) or then == 0:
        return None
    try:
        return (float(now) - float(then)) / abs(float(then))
    except (TypeError, ValueError):
        return None


def build_snapshot(ticker: str, d: pd.Timestamp, ind: pd.DataFrame,
                   sf1: pd.DataFrame, tickers_meta: Dict[str, Dict]) -> Optional[Dict]:
    """Build a market_data-style snapshot for `ticker` as of date `d`."""
    if d not in ind.index:
        return None
    row = ind.loc[d]
    if pd.isna(row.get("sma200")) or pd.isna(row.get("atr14")):
        return None

    fund_row = _sf1_lookup(sf1, ticker, d)
    if fund_row is None:
        return None  # no point-in-time fundamentals; skip

    # Quality filter (hard floor per CLAUDE.md):
    mc = fund_row.get("marketcap")
    de = fund_row.get("de")
    ncfo = fund_row.get("ncfo")
    netmargin = fund_row.get("netmargin")
    if mc is None or pd.isna(mc) or mc < 2e9:
        return None
    if ncfo is None or pd.isna(ncfo) or ncfo <= 0:
        return None
    if de is not None and not pd.isna(de) and de > 3.0:
        return None  # >300% in the legacy yfinance percent scale
    if netmargin is not None and not pd.isna(netmargin) and netmargin < -0.10:
        return None

    # Compute YoY growths
    rev_growth = _yoy_change(sf1, ticker, d, "revenue")
    eps_growth = _yoy_change(sf1, ticker, d, "eps")

    price = float(row["close"])
    atr14 = float(row["atr14"])
    sma50 = row.get("sma50")
    sma200 = row.get("sma200")
    pct_above_50 = ((price / sma50) - 1) * 100 if sma50 and not pd.isna(sma50) else None
    pct_above_200 = ((price / sma200) - 1) * 100 if sma200 and not pd.isna(sma200) else None
    sma50_slope = row.get("sma50_slope")
    in_uptrend = bool((not pd.isna(sma50)) and (price > sma50) and (sma50_slope >= 0))
    in_downtrend = bool(row.get("in_downtrend"))
    is_basing = bool(row.get("is_basing"))
    range_pos = float(row.get("range_pos", 50.0))

    meta = tickers_meta.get(ticker, {})
    op_margin = None
    rev = fund_row.get("revenue")
    opinc = fund_row.get("opinc")
    if rev and opinc and not pd.isna(rev) and not pd.isna(opinc) and rev != 0:
        op_margin = float(opinc) / float(rev)

    return {
        "symbol": ticker,
        "technicals": {
            "price": price,
            "sma50": float(sma50) if sma50 and not pd.isna(sma50) else None,
            "sma200": float(sma200) if sma200 and not pd.isna(sma200) else None,
            "sma50_slope_pct": float(sma50_slope) if sma50_slope and not pd.isna(sma50_slope) else None,
            "pct_above_sma50": pct_above_50,
            "pct_above_sma200": pct_above_200,
            "atr14": atr14,
            "atr14_pct": (atr14 / price) * 100 if price > 0 else None,
            "range_position_pct": range_pos,
            "week52_high": float(price + 0),  # placeholder; could compute exactly but not used in prompt
            "week52_low": float(price + 0),
            "is_basing": is_basing,
            "in_uptrend": in_uptrend,
            "in_downtrend": in_downtrend,
            "is_falling_knife": bool(row.get("is_falling_knife")),
        },
        "fundamentals": {
            "name": meta.get("name"),
            "sector": meta.get("sector"),
            "market_cap": float(mc),
            "trailing_pe": float(fund_row.get("pe")) if fund_row.get("pe") and not pd.isna(fund_row.get("pe")) else None,
            "forward_pe": None,  # Sharadar's pe1 is one-year-trailing; no forward
            "peg_ratio": None,
            "price_to_book": float(fund_row.get("pb")) if fund_row.get("pb") and not pd.isna(fund_row.get("pb")) else None,
            "return_on_equity": float(fund_row.get("roe")) if fund_row.get("roe") and not pd.isna(fund_row.get("roe")) else None,
            "profit_margin": float(netmargin) if netmargin and not pd.isna(netmargin) else None,
            "operating_margin": op_margin,
            "revenue_growth": rev_growth,
            "earnings_growth": eps_growth,
            "debt_to_equity": float(de) * 100 if de and not pd.isna(de) else None,  # match yfinance percent scale
            "current_ratio": float(fund_row.get("currentratio")) if fund_row.get("currentratio") and not pd.isna(fund_row.get("currentratio")) else None,
            "free_cash_flow": float(fund_row.get("fcf")) if fund_row.get("fcf") and not pd.isna(fund_row.get("fcf")) else None,
            "operating_cash_flow": float(ncfo),
            "dividend_yield": float(fund_row.get("divyield")) if fund_row.get("divyield") and not pd.isna(fund_row.get("divyield")) else None,
        },
        "quality": {"passes": True},  # already filtered above
        "setup_score": _setup_score_for_snapshot(in_uptrend, in_downtrend, is_basing, range_pos, rev_growth, fund_row),
    }


def _setup_score_for_snapshot(in_uptrend: bool, in_downtrend: bool, is_basing: bool,
                                range_pos: float, rev_growth: Optional[float],
                                fund_row: pd.Series) -> float:
    """Approximation of market_data._setup_score; used only to rank candidates for prompt inclusion."""
    score = 0.0
    if is_basing and range_pos < 50:
        score += 10
    if (not in_downtrend) and range_pos < 30:
        score += 3
    if in_uptrend:
        score += 4
        if range_pos > 90:
            score -= 3
    if rev_growth is not None and rev_growth > 0.05:
        score += 2
    pe = fund_row.get("pe")
    if pe is not None and not pd.isna(pe) and 5 < pe < 25:
        score += 1
    return score


def format_snapshot_compact(snap: Dict) -> str:
    """Compact one-block-per-ticker rendering for the prompt."""
    t = snap["technicals"]
    f = snap["fundamentals"]

    def pct(v, d=1):
        return f"{v:+.{d}f}%" if v is not None else "n/a"

    def num(v, d=2):
        return f"{v:.{d}f}" if v is not None else "n/a"

    def money(v):
        if v is None:
            return "n/a"
        if v >= 1e9:
            return f"${v / 1e9:.1f}B"
        return f"${v:,.0f}"

    trend = "DOWNTREND" if t["in_downtrend"] else "UPTREND" if t["in_uptrend"] else "MIXED/SIDEWAYS"
    basing = "basing" if t["is_basing"] else "extending"

    lines = [
        f"--- {snap['symbol']} ({f.get('name') or '?'}, {f.get('sector') or '?'}) ---",
        f"  Price: ${t['price']:.2f}  |  52w range pos: {t['range_position_pct']:.0f}%  |  Trend: {trend} ({basing})",
        f"  vs SMA50: {pct(t['pct_above_sma50'])}  |  vs SMA200: {pct(t['pct_above_sma200'])}  |  SMA50 slope (20d): {pct(t['sma50_slope_pct'])}",
        f"  ATR(14): {pct(t['atr14_pct'])} of price",
        f"  Mkt cap: {money(f['market_cap'])}  |  P/E: {num(f['trailing_pe'])}  |  P/B: {num(f['price_to_book'])}",
        f"  ROE: {pct((f['return_on_equity'] or 0) * 100) if f['return_on_equity'] is not None else 'n/a'}  |  "
        f"Op margin: {pct((f['operating_margin'] or 0) * 100) if f['operating_margin'] is not None else 'n/a'}  |  "
        f"Profit margin: {pct((f['profit_margin'] or 0) * 100) if f['profit_margin'] is not None else 'n/a'}",
        f"  Rev growth YoY: {pct((f['revenue_growth'] or 0) * 100) if f['revenue_growth'] is not None else 'n/a'}  |  "
        f"EPS growth YoY: {pct((f['earnings_growth'] or 0) * 100) if f['earnings_growth'] is not None else 'n/a'}  |  "
        f"D/E: {num(f['debt_to_equity'])}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sonnet call
# ---------------------------------------------------------------------------

# System prompt (cacheable): strategy rules + JSON schema, doesn't change between calls.
SYSTEM_PROMPT = """\
YOU ARE AN EXPERT QUALITY-VALUE TRADING ADVISOR.

STRATEGY: Concentrated long-only US equity. Quality businesses bought at fair prices
when the trend has stopped going down. Holding periods: weeks to months by default.

Buffett's "wonderful business at a fair price" + Weinstein's "buy the basing knife,
not the falling knife." Cash is a position — forcing trades is the enemy.

HARD RULES (do not violate):

1. UNIVERSE & QUALITY
   - Only propose tickers from the screened candidate list provided.
   - All candidates already passed: market cap >= $2B, positive operating cash flow,
     debt/equity < 300, profit margin > -10%.

2. ENTRY TIMING
   - PREFER: basing patterns in the lower half of 52-week range.
   - ACCEPTABLE: confirmed uptrends not yet extended.
   - AVOID: extended late-cycle entries.

3. POSITION SIZING & RISK
   - HARD CAP: 25% of equity per position.
   - Stop loss: 1.5-2x ATR(14). Stops below 4% rejected; above 15% forbidden.
   - Position size such that worst case costs 1-2% of total equity.

4. CONVICTION DISCIPLINE
   - Propose 0-3 trades. NOT MORE. Better fewer high-conviction than many mediocre.
   - If no candidate has a clear asymmetric setup, return zero trades.

Return JSON only with this shape:
{
  "analysis": "<brief>",
  "confidence_target_pct": <int 0-100>,
  "trades": [
    {
      "symbol": "<from candidate list>",
      "entry_price": <number>,
      "stop_loss": <number>,
      "stop_loss_pct": <decimal>,
      "atr_multiple": <number 1.5-2.0>,
      "target": <number>,
      "conviction": <int 0-100>,
      "rationale": "<fundamental + technical>"
    }
  ]
}
"""


def _truncate_universe_for_prompt(snaps: List[Dict]) -> List[Dict]:
    """Sort by setup_score desc, keep top N, drop ones below min score."""
    filtered = [s for s in snaps if s["setup_score"] >= SCREEN_SCORE_MIN]
    filtered.sort(key=lambda s: s["setup_score"], reverse=True)
    return filtered[:MAX_CANDIDATES_FOR_PROMPT]


def build_prompt(d: pd.Timestamp, equity: float, cash: float,
                 open_positions: List[Dict], recent_trades: List[Dict],
                 candidates: List[Dict]) -> str:
    """User message: account context + screened candidates. System prompt is separate."""
    parts = [
        f"DATE: {d.date().isoformat()}",
        f"",
        f"PORTFOLIO:",
        f"- Equity: ${equity:,.0f}  |  Cash: ${cash:,.0f}  |  Open positions: {len(open_positions)}",
    ]
    if open_positions:
        for p in open_positions:
            parts.append(f"  * {p['symbol']}: {p['qty']} shares @ avg ${p['avg_price']:.2f}  (now ${p['price']:.2f}, {p['unrealized_pct']:+.1%})")
    else:
        parts.append("  (none — fully in cash)")

    if recent_trades:
        parts.append("")
        parts.append("RECENT CLOSED TRADES (last 5):")
        for t in recent_trades[-5:]:
            parts.append(f"  * {t['symbol']}: {t['pnl_pct']:+.2%}  ({t['exit_reason']})")

    parts.append("")
    parts.append(f"SCREENED CANDIDATES ({len(candidates)} passed quality + technical filter, ranked by setup score):")
    parts.append("")
    for snap in candidates:
        parts.append(format_snapshot_compact(snap))
        parts.append("")

    return "\n".join(parts)


def call_sonnet(system_prompt: str, user_prompt: str, cost_tracker: Dict) -> Tuple[Optional[Dict], float]:
    """Call Sonnet 4.6 with prompt caching. Returns (parsed_response, cost_usd).
    Retries with exponential backoff on transient 5xx overload/server errors."""
    import anthropic
    client = anthropic.Anthropic()

    backoff_schedule = [5, 15, 45, 90]  # seconds, ~2.5 min total budget
    last_err = None
    response = None
    for attempt, delay in enumerate([0] + backoff_schedule):
        if delay:
            time.sleep(delay)
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_prompt}],
            )
            break
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            last_err = e
            status = getattr(e, "status_code", None)
            if status and 500 <= status < 600:
                print(f"    transient {status}, retry {attempt + 1}/{len(backoff_schedule)} after {backoff_schedule[attempt] if attempt < len(backoff_schedule) else '-'}s")
                continue
            # Non-transient (4xx auth/validation) — abort.
            raise

    if response is None:
        print(f"    cohort skipped after {len(backoff_schedule)} retries: {last_err}")
        return None, 0.0

    usage = response.usage
    in_uncached = getattr(usage, "input_tokens", 0)
    in_cache_create = getattr(usage, "cache_creation_input_tokens", 0)
    in_cache_read = getattr(usage, "cache_read_input_tokens", 0)
    out_tokens = getattr(usage, "output_tokens", 0)

    # Cache creation costs 1.25x normal input; cache reads cost 10% of normal input.
    cost = (
        (in_uncached / 1e6) * SONNET_INPUT_PER_MTOK
        + (in_cache_create / 1e6) * SONNET_INPUT_PER_MTOK * 1.25
        + (in_cache_read / 1e6) * SONNET_CACHED_INPUT_PER_MTOK
        + (out_tokens / 1e6) * SONNET_OUTPUT_PER_MTOK
    )
    cost_tracker["total_usd"] += cost
    cost_tracker["calls"] += 1

    # Parse JSON from the response (Sonnet wraps in code fences sometimes).
    text = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text += block.text
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json\n"):
            text = text[5:]
        text = text.rstrip("`").strip()
    try:
        return json.loads(text), cost
    except json.JSONDecodeError:
        # Tolerate occasional parse failures — return None for this cohort.
        return None, cost


# ---------------------------------------------------------------------------
# Checkpoint persistence
# ---------------------------------------------------------------------------

def save_checkpoint(state: Dict, path: Path = CHECKPOINT_FILE) -> None:
    """Atomic write: temp file then rename, so a crash mid-write can't corrupt."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(state, f)
    tmp.replace(path)


def load_checkpoint(path: Path = CHECKPOINT_FILE) -> Optional[Dict]:
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except (pickle.UnpicklingError, EOFError, OSError) as e:
        print(f"  Checkpoint at {path} unreadable ({e}); ignoring.")
        return None


# ---------------------------------------------------------------------------
# Backtest loop
# ---------------------------------------------------------------------------

def _fridays_between(start: pd.Timestamp, end: pd.Timestamp,
                     trading_days: List[pd.Timestamp]) -> List[pd.Timestamp]:
    """All trading-day Fridays (weekday 4) in [start, end]."""
    return [d for d in trading_days if d >= start and d <= end and d.weekday() == 4]


def _tickers_metadata_dict(tickers_meta_df: pd.DataFrame) -> Dict[str, Dict]:
    """Convert the Sharadar TICKERS table into a fast lookup dict."""
    out = {}
    if "ticker" not in tickers_meta_df.columns:
        return out
    for _, row in tickers_meta_df.iterrows():
        out[row["ticker"]] = {
            "name": row.get("name"),
            "sector": row.get("sector"),
            "industry": row.get("industry"),
        }
    return out


def run_llm_backtest(period_start: str, period_end: str,
                     params: BacktestParams,
                     indicators: Dict[str, pd.DataFrame],
                     spy: pd.DataFrame,
                     sf1: pd.DataFrame,
                     tickers_meta: Dict[str, Dict],
                     log_every_n_cohorts: int = 10,
                     dry_run_no_llm: bool = False,
                     verbose: bool = False,
                     resume_state: Optional[Dict] = None) -> Dict:
    """
    Walk forward by day. Every Friday, build screen + call Sonnet for picks for
    the following Monday. Daily: manage open positions (stops, scale-outs, trails).
    If `resume_state` is supplied (loaded from checkpoint), continue from where
    the previous run left off.
    """
    spy_window = spy.loc[period_start:period_end]
    trading_days = list(spy_window.index)
    fridays = _fridays_between(pd.Timestamp(period_start), pd.Timestamp(period_end), trading_days)
    if verbose:
        print(f"  {len(fridays)} Friday cohorts to process over {len(trading_days)} trading days")

    if resume_state is not None:
        cash = resume_state["cash"]
        equity_history = resume_state["equity_history"]
        open_trades = resume_state["open_trades"]
        closed_trades = resume_state["closed_trades"]
        pending_picks = resume_state["pending_picks"]
        cost_tracker = resume_state["cost_tracker"]
        cohort_log = resume_state["cohort_log"]
        skip_through_idx = resume_state["last_iteration_idx"]
        print(f"  RESUMING from checkpoint: iteration {skip_through_idx + 1}/{len(trading_days)}, "
              f"cost ${cost_tracker['total_usd']:.2f}, "
              f"{len(closed_trades)} closed trades, {len(open_trades)} open, "
              f"{len(cohort_log)} prior cohorts")
    else:
        cash = INITIAL_CAPITAL
        equity_history = []
        open_trades = []
        closed_trades = []
        pending_picks = []
        cost_tracker = {"total_usd": 0.0, "calls": 0, "aborted": False}
        cohort_log = []
        skip_through_idx = -1

    for i, d in enumerate(trading_days):
        if i <= skip_through_idx:
            continue
        d_iso = d.date().isoformat()

        # --- Manage open positions using today's bar ---
        for trade in list(open_trades):
            ind = indicators.get(trade.symbol)
            if ind is None or d not in ind.index:
                continue
            row = ind.loc[d]
            day_low = row["low"]; day_high = row["high"]; day_close = row["close"]

            # Stop hit
            if day_low <= trade.current_stop:
                fill = trade.current_stop
                proceeds = trade.qty * fill * (1 - TRANSACTION_COST_BPS / 10000)
                pnl = (fill - trade.entry_price) * trade.qty
                cash += proceeds
                trade.exits.append({"date": d_iso, "qty": trade.qty, "price": fill, "pnl": pnl, "reason": "stop"})
                trade.qty = 0; trade.closed = True; trade.exit_reason = "stop"; trade.final_exit_date = d_iso
                closed_trades.append(trade); open_trades.remove(trade)
                continue

            if trade.R <= 0:
                continue
            gain_in_R = (day_close - trade.entry_price) / trade.R

            if not trade.scaled_25_at_1r and gain_in_R >= 1.0:
                scale_qty = max(1, int(round(trade.initial_qty * params.scale_out_fraction)))
                scale_qty = min(scale_qty, trade.qty - 1)
                if scale_qty > 0:
                    fill = day_close
                    proceeds = scale_qty * fill * (1 - TRANSACTION_COST_BPS / 10000)
                    pnl = (fill - trade.entry_price) * scale_qty
                    cash += proceeds
                    trade.exits.append({"date": d_iso, "qty": scale_qty, "price": fill, "pnl": pnl, "reason": "scale_1r"})
                    trade.qty -= scale_qty
                    trade.scaled_25_at_1r = True
                    trade.current_stop = max(trade.current_stop, trade.entry_price * 1.001)

            if trade.scaled_25_at_1r and not trade.scaled_25_at_2r and gain_in_R >= 2.0:
                scale_qty = max(1, int(round(trade.initial_qty * params.scale_out_fraction)))
                scale_qty = min(scale_qty, trade.qty - 1)
                if scale_qty > 0:
                    fill = day_close
                    proceeds = scale_qty * fill * (1 - TRANSACTION_COST_BPS / 10000)
                    pnl = (fill - trade.entry_price) * scale_qty
                    cash += proceeds
                    trade.exits.append({"date": d_iso, "qty": scale_qty, "price": fill, "pnl": pnl, "reason": "scale_2r"})
                    trade.qty -= scale_qty
                    trade.scaled_25_at_2r = True
                    trade.current_stop = max(trade.current_stop, trade.entry_price + trade.R)

            if trade.scaled_25_at_2r and gain_in_R >= 3.0:
                atr = row["atr14"]
                if atr and not pd.isna(atr) and atr > 0:
                    candidate_stop = day_close - params.atr_trail_multiple * atr
                    if candidate_stop > trade.current_stop:
                        trade.current_stop = candidate_stop

            if trade.days_held_at(d_iso) >= params.max_hold_days:
                fill = day_close
                proceeds = trade.qty * fill * (1 - TRANSACTION_COST_BPS / 10000)
                pnl = (fill - trade.entry_price) * trade.qty
                cash += proceeds
                trade.exits.append({"date": d_iso, "qty": trade.qty, "price": fill, "pnl": pnl, "reason": "time_stop"})
                trade.qty = 0; trade.closed = True; trade.exit_reason = "time_stop"; trade.final_exit_date = d_iso
                closed_trades.append(trade); open_trades.remove(trade)

        # Mark-to-market
        position_value = 0.0
        for tr in open_trades:
            ind = indicators.get(tr.symbol)
            if ind is None or d not in ind.index:
                continue
            position_value += tr.qty * ind.loc[d, "close"]
        equity = cash + position_value
        equity_history.append((d, equity))

        # --- Enter pending picks at today's open ---
        if pending_picks and len(open_trades) < params.max_concurrent_positions:
            held = {t.symbol for t in open_trades}
            for pick in list(pending_picks):
                if pick["symbol"] in held:
                    pending_picks.remove(pick); continue
                if len(open_trades) >= params.max_concurrent_positions:
                    break
                ind = indicators.get(pick["symbol"])
                if ind is None or d not in ind.index:
                    pending_picks.remove(pick); continue
                entry_open = ind.loc[d, "open"]
                # Recompute ATR-anchored stop with today's data
                ref_day = trading_days[i - 1] if i > 0 else d
                if ref_day not in ind.index:
                    pending_picks.remove(pick); continue
                atr = ind.loc[ref_day, "atr14"]
                if pd.isna(entry_open) or pd.isna(atr) or atr <= 0:
                    pending_picks.remove(pick); continue
                stop, R = compute_atr_stop(entry_open, atr, params)
                if R <= 0:
                    pending_picks.remove(pick); continue
                risk_dollars = equity * params.target_risk_per_trade
                shares_by_risk = int(risk_dollars / R)
                shares_by_cap = int((equity * params.max_position_pct) / entry_open)
                shares = max(0, min(shares_by_risk, shares_by_cap))
                cost = shares * entry_open * (1 + TRANSACTION_COST_BPS / 10000)
                if shares <= 0 or cost > cash:
                    pending_picks.remove(pick); continue
                cash -= cost
                open_trades.append(Trade(
                    symbol=pick["symbol"], entry_date=d_iso, entry_price=entry_open,
                    initial_qty=shares, qty=shares,
                    initial_stop=stop, current_stop=stop, R=R,
                ))
                pending_picks.remove(pick)

        # --- Friday: call Sonnet to pick for next week ---
        if d in fridays and not dry_run_no_llm:
            if cost_tracker["total_usd"] >= COST_CEILING_USD:
                if not cost_tracker["aborted"]:
                    print(f"  *** Cost ceiling ${COST_CEILING_USD} reached after {cost_tracker['calls']} calls. Aborting LLM picks; trade mgmt continues. ***")
                    cost_tracker["aborted"] = True
            else:
                snaps = []
                for sym, ind in indicators.items():
                    if sym in {t.symbol for t in open_trades}:
                        continue
                    snap = build_snapshot(sym, d, ind, sf1, tickers_meta)
                    if snap is not None:
                        snaps.append(snap)
                top = _truncate_universe_for_prompt(snaps)

                if top:
                    open_positions_info = []
                    for tr in open_trades:
                        ind = indicators.get(tr.symbol)
                        cur_price = float(ind.loc[d, "close"]) if (ind is not None and d in ind.index) else tr.entry_price
                        open_positions_info.append({
                            "symbol": tr.symbol, "qty": tr.qty, "avg_price": tr.entry_price,
                            "price": cur_price, "unrealized_pct": (cur_price / tr.entry_price - 1),
                        })
                    recent = []
                    for tr in closed_trades[-5:]:
                        pnl = tr.realized_pnl()
                        cost_basis = tr.initial_qty * tr.entry_price
                        recent.append({"symbol": tr.symbol, "pnl_pct": pnl / cost_basis if cost_basis else 0,
                                        "exit_reason": tr.exit_reason})
                    user_prompt = build_prompt(d, equity, cash, open_positions_info, recent, top)
                    response, call_cost = call_sonnet(SYSTEM_PROMPT, user_prompt, cost_tracker)
                    picks_this_cohort = []
                    if response and isinstance(response.get("trades"), list):
                        candidate_symbols = {s["symbol"] for s in top}
                        for trade in response["trades"][:3]:
                            sym = trade.get("symbol")
                            if sym and sym in candidate_symbols:
                                picks_this_cohort.append({"symbol": sym, "conviction": trade.get("conviction", 50)})
                        # Apply conviction sizing (rec #2): skip <60
                        picks_this_cohort = [p for p in picks_this_cohort if p["conviction"] >= 60]
                    pending_picks.extend(picks_this_cohort)

                    cohort_log.append({
                        "date": d_iso, "candidates_shown": len(top),
                        "picks": [p["symbol"] for p in picks_this_cohort],
                        "cost_usd": round(call_cost, 4),
                        "total_cost_usd": round(cost_tracker["total_usd"], 4),
                    })
                    if verbose and (len(cohort_log) % log_every_n_cohorts == 0):
                        print(f"    cohort {len(cohort_log)} {d_iso}: shown={len(top)} picks={[p['symbol'] for p in picks_this_cohort]} cost=${cost_tracker['total_usd']:.3f}")

                    # Checkpoint after every successful cohort. Cheap (~1MB pickle),
                    # makes the run survive any Anthropic flake, billing hiccup, etc.
                    save_checkpoint({
                        "period_start": period_start,
                        "period_end": period_end,
                        "last_iteration_idx": i,
                        "cash": cash,
                        "equity_history": equity_history,
                        "open_trades": open_trades,
                        "closed_trades": closed_trades,
                        "pending_picks": pending_picks,
                        "cost_tracker": cost_tracker,
                        "cohort_log": cohort_log,
                    })

    return {
        "equity_curve": equity_history,
        "trades": closed_trades + open_trades,
        "cohort_log": cohort_log,
        "cost_tracker": cost_tracker,
    }


# ---------------------------------------------------------------------------
# Stats + gate evaluation (delegating to phase0_backtest where possible)
# ---------------------------------------------------------------------------

def compute_run_stats(result: Dict, initial: float = INITIAL_CAPITAL) -> Dict:
    """CAGR (pre + post-tax), max DD, volatility, Sharpe, win rate."""
    equity = result["equity_curve"]
    trades = result["trades"]
    if not equity:
        return {"available": False}

    final_pre = equity[-1][1]
    start_d = equity[0][0]; end_d = equity[-1][0]
    years = (end_d - start_d).days / 365.25
    cagr_pre = (final_pre / initial) ** (1 / years) - 1 if years > 0 else 0

    # Tax model: 32% STCG / 15% LTCG per partial exit
    tax = 0.0
    for tr in trades:
        for exit in tr.exits:
            held_days = (date.fromisoformat(exit["date"]) - date.fromisoformat(tr.entry_date)).days
            rate = LTCG_TAX_RATE if held_days >= LTCG_DAYS else STCG_TAX_RATE
            if exit["pnl"] > 0:
                tax += exit["pnl"] * rate
    final_post = final_pre - tax
    cagr_post = (final_post / initial) ** (1 / years) - 1 if (years > 0 and final_post > 0) else -1

    eq_series = pd.Series([e for _, e in equity], index=[d for d, _ in equity])
    daily_ret = eq_series.pct_change().dropna()
    max_dd = ((eq_series.cummax() - eq_series) / eq_series.cummax()).max()
    vol = daily_ret.std() * np.sqrt(252) if len(daily_ret) > 1 else 0
    sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0

    # Win rate over partial exits
    all_exits = [e for tr in trades for e in tr.exits]
    wins = sum(1 for e in all_exits if e["pnl"] > 0)
    win_rate = wins / len(all_exits) if all_exits else 0

    return {
        "available": True,
        "duration_years": years,
        "initial": initial,
        "final_pre_tax": final_pre,
        "final_post_tax": final_post,
        "tax_dollars": tax,
        "cagr_pre_tax": cagr_pre,
        "cagr_post_tax": cagr_post,
        "max_drawdown": float(max_dd) if not pd.isna(max_dd) else 0,
        "vol_annualized": float(vol),
        "sharpe": float(sharpe),
        "trade_cohorts": len(trades),
        "partial_exits": len(all_exits),
        "win_rate": win_rate,
    }


def _cagr_pct(eq_series: pd.Series) -> float:
    if len(eq_series) < 2:
        return 0
    years = (eq_series.index[-1] - eq_series.index[0]).days / 365.25
    if years <= 0:
        return 0
    return (eq_series.iloc[-1] / eq_series.iloc[0]) ** (1 / years) - 1


def evaluate_gates(stats: Dict, spy: pd.DataFrame, usmv: pd.DataFrame,
                   equity_history: List[Tuple[pd.Timestamp, float]],
                   period_start: str, period_end: str) -> Dict:
    """Same 5 gates as phase0_backtest."""
    spy_window = spy.loc[period_start:period_end]["Close"]
    usmv_window = usmv.loc[period_start:period_end]["Close"]
    spy_cagr = _cagr_pct(spy_window)
    usmv_cagr = _cagr_pct(usmv_window)
    spy_dd = ((spy_window.cummax() - spy_window) / spy_window.cummax()).max()

    # Apply survivorship haircut
    strat_cagr_net = stats["cagr_post_tax"] - SURVIVORSHIP_HAIRCUT_CAGR

    gates = {}
    gates["gate1_cagr_vs_spy_plus_1pct"] = {
        "passed": strat_cagr_net > spy_cagr + 0.01,
        "strategy_cagr_net": strat_cagr_net,
        "spy_cagr_plus_1pct": spy_cagr + 0.01,
    }
    gates["gate2_dd_le_80pct_spy"] = {
        "passed": stats["max_drawdown"] <= 0.8 * spy_dd,
        "strategy_max_dd": stats["max_drawdown"],
        "spy_max_dd_x_0.8": 0.8 * spy_dd,
    }

    # Rolling 3-year window CAGR comparison
    strat_eq = pd.Series([e for _, e in equity_history], index=[d for d, _ in equity_history])
    n3y = 252 * 3
    wins_vs_spy = 0; wins_vs_usmv = 0; windows = 0
    for i in range(0, len(strat_eq) - n3y, 21):  # step monthly
        strat_slice = strat_eq.iloc[i:i + n3y]
        spy_slice = spy_window.reindex(strat_slice.index).ffill().dropna()
        usmv_slice = usmv_window.reindex(strat_slice.index).ffill().dropna()
        if len(strat_slice) < n3y // 2 or len(spy_slice) < n3y // 2:
            continue
        windows += 1
        if _cagr_pct(strat_slice) > _cagr_pct(spy_slice):
            wins_vs_spy += 1
        if _cagr_pct(strat_slice) > _cagr_pct(usmv_slice):
            wins_vs_usmv += 1
    gates["gate3_rolling_3y_vs_spy_55pct"] = {
        "passed": (wins_vs_spy / windows) >= 0.55 if windows else False,
        "outperform_fraction": wins_vs_spy / windows if windows else 0,
        "threshold": 0.55,
    }
    gates["gate4_rolling_3y_vs_usmv_45pct"] = {
        "passed": (wins_vs_usmv / windows) >= 0.45 if windows else False,
        "outperform_fraction": wins_vs_usmv / windows if windows else 0,
        "threshold": 0.45,
    }
    # Gate 5 (param sensitivity) requires running variants — done in main().
    return gates


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slice-month", type=str, default=None,
                        help="YYYY-MM: run only that month for a quick test (no full OOS, no cost ceiling enforcement)")
    parser.add_argument("--refresh", action="store_true", help="Re-download all caches")
    parser.add_argument("--dry-run-no-llm", action="store_true", help="Skip Sonnet calls; trade mgmt only (free)")
    parser.add_argument("--start-fresh", action="store_true",
                        help="Ignore any existing checkpoint and start the OOS run from scratch")
    args = parser.parse_args()

    print("=" * 70)
    print("Phase-0 LLM-in-loop backtest")
    print(f"  Model: claude-sonnet-4-6")
    print(f"  Cost ceiling: ${COST_CEILING_USD}")
    print("=" * 70)

    print("\n[1/4] Loading caches...")
    tickers = _universe()
    price_data = download_prices(tickers, "2018-01-01", "2025-12-31",
                                  "universe_2010_2025", refresh=args.refresh)
    spy_data = download_prices(["SPY"], "2018-01-01", "2025-12-31", "spy_2010_2025",
                                refresh=args.refresh).get("SPY")
    usmv_data = download_prices(["USMV"], "2018-01-01", "2025-12-31", "usmv_2011_2025",
                                 refresh=args.refresh).get("USMV")
    if spy_data is None or usmv_data is None:
        print("Missing benchmark data; aborting.")
        return

    print("[2/4] Computing indicators...")
    indicators = compute_indicators(price_data)
    print(f"  Indicators ready for {len(indicators)} tickers")

    print("[3/4] Loading Sharadar SF1 + TICKERS metadata...")
    from sharadar_client import fetch_sf1_for_universe, fetch_tickers_metadata
    sf1 = fetch_sf1_for_universe(tickers, "2018-01-01", "2025-12-31")
    meta_df = fetch_tickers_metadata()
    tickers_meta = _tickers_metadata_dict(meta_df)
    print(f"  SF1: {len(sf1):,} rows, {sf1['ticker'].nunique()} tickers; metadata: {len(tickers_meta)} tickers")

    params = BacktestParams()

    if args.slice_month:
        # Single-month test slice — start a month earlier so SMA/ATR have warmup
        slice_start = pd.Timestamp(f"{args.slice_month}-01")
        slice_end = (slice_start + pd.offsets.MonthEnd(0))
        print(f"\n[4/4] Running 1-month test slice: {slice_start.date()} to {slice_end.date()}")
        result = run_llm_backtest(slice_start.strftime("%Y-%m-%d"), slice_end.strftime("%Y-%m-%d"),
                                   params, indicators, spy_data, sf1, tickers_meta,
                                   dry_run_no_llm=args.dry_run_no_llm, verbose=True)
        stats = compute_run_stats(result)
        print(f"\nSLICE RESULT:")
        print(f"  Cohorts processed: {len(result['cohort_log'])}")
        print(f"  API cost: ${result['cost_tracker']['total_usd']:.4f}")
        print(f"  Equity start: ${result['equity_curve'][0][1]:,.2f}  end: ${result['equity_curve'][-1][1]:,.2f}")
        if stats["available"]:
            print(f"  Pre-tax CAGR (extrapolated): {stats['cagr_pre_tax']:+.2%}")
            print(f"  Trade cohorts: {stats['trade_cohorts']}, partial exits: {stats['partial_exits']}, win rate: {stats['win_rate']:.1%}")
        # Save cohort log for inspection
        out = {"slice": args.slice_month, "cohorts": result["cohort_log"], "stats": stats}
        with open("phase0_backtest_llm_slice.json", "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"  Wrote cohort log to phase0_backtest_llm_slice.json")
        return

    # Full OOS run — handle resume from checkpoint if present
    print(f"\n[4/4] Running full OOS {OUT_SAMPLE_START} to {OUT_SAMPLE_END}")
    resume_state = None
    if args.start_fresh and CHECKPOINT_FILE.exists():
        print(f"  --start-fresh: removing existing checkpoint {CHECKPOINT_FILE}")
        CHECKPOINT_FILE.unlink()
    else:
        ckpt = load_checkpoint()
        if ckpt is not None:
            # Sanity-check the checkpoint matches this run's period
            if ckpt.get("period_start") == OUT_SAMPLE_START and ckpt.get("period_end") == OUT_SAMPLE_END:
                resume_state = ckpt
            else:
                print(f"  Found checkpoint for different period ({ckpt.get('period_start')}..{ckpt.get('period_end')}); ignoring.")
    result = run_llm_backtest(OUT_SAMPLE_START, OUT_SAMPLE_END, params, indicators,
                               spy_data, sf1, tickers_meta,
                               dry_run_no_llm=args.dry_run_no_llm, verbose=True,
                               resume_state=resume_state)
    stats = compute_run_stats(result)
    print(f"\nOOS stats: {json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in stats.items()}, indent=2)}")

    print("\nEvaluating gates...")
    gates = evaluate_gates(stats, spy_data, usmv_data, result["equity_curve"],
                           OUT_SAMPLE_START, OUT_SAMPLE_END)

    # Gate 5: parameter sensitivity (run 2 perturbations dry-run-no-llm via the rules engine?
    # For the LLM backtest, we can't cheaply re-run with different params. Mark as N/A.
    gates["gate5_param_sensitivity_le_3pct"] = {
        "passed": None, "note": "Not run for LLM backtest (cost prohibitive). Rules-based run already failed this gate.",
    }

    passed = sum(1 for g in gates.values() if g.get("passed") is True)
    total_eval = sum(1 for g in gates.values() if g.get("passed") in (True, False))
    print(f"\nGATES PASSED: {passed}/{total_eval} evaluable (gate 5 skipped — see note)")

    verdict = ("STOP -- $3k to SPY+USMV blend" if passed <= 1
               else "Defer to end of Phase 1" if passed <= 3
               else "Aug 1 flip on")

    final = {
        "params": asdict(params),
        "stats": stats,
        "gates": gates,
        "gates_passed_of_evaluable": f"{passed}/{total_eval}",
        "verdict": verdict,
        "cost_tracker": result["cost_tracker"],
        "spy_cagr_oos": _cagr_pct(spy_data.loc[OUT_SAMPLE_START:OUT_SAMPLE_END]["Close"]),
        "usmv_cagr_oos": _cagr_pct(usmv_data.loc[OUT_SAMPLE_START:OUT_SAMPLE_END]["Close"]),
        "ran_at": pd.Timestamp.now().isoformat(),
    }
    with open(LLM_RESULTS_FILE, "w") as f:
        json.dump(final, f, indent=2, default=str)
    print(f"\nFull results written to {LLM_RESULTS_FILE}")
    print(f"\nVERDICT: {verdict}")


if __name__ == "__main__":
    main()
