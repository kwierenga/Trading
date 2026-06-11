"""
Market Data Module
Pulls technicals (price history, MAs, ATR, range) and fundamentals (yfinance)
for stock candidates. Provides a quality filter to exclude obvious junk before
Claude weighs the rest.

Strategy style: quality-value with trend confirmation.
- Don't buy in a confirmed downtrend (50-day MA falling).
- Prefer setups where price is in lower half of 52-week range AND has stopped
  making new lows for 2+ weeks (basing pattern).
- Quality fundamentals (positive FCF, reasonable leverage) are a hard floor;
  valuation/growth/margins are soft inputs Claude weighs.
"""

import json
import math
import os
import statistics
import time
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import yfinance as yf


# Minimum bars needed to compute the longest MA we report.
MIN_BARS_FOR_TECHNICALS = 200

# Snapshot cache — 24h TTL is appropriate for a swing/position strategy that runs
# the screen once per trading day.
SNAPSHOT_CACHE_FILE = "market_snapshots_cache.json"
SNAPSHOT_TTL_SECONDS = 24 * 3600


@dataclass
class Technicals:
    symbol: str
    price: float
    sma20: Optional[float]
    sma50: Optional[float]
    sma200: Optional[float]
    sma50_slope_pct: Optional[float]   # % change in SMA50 over last 20 trading days
    pct_above_sma50: Optional[float]
    pct_above_sma200: Optional[float]
    week52_high: float
    week52_low: float
    range_position_pct: float          # 0 = at 52w low, 100 = at 52w high
    atr14: Optional[float]             # Average True Range, 14-day
    atr14_pct: Optional[float]         # ATR as % of price
    is_basing: bool                    # No new 20-day low in last 10 sessions
    in_uptrend: bool                   # Price > SMA50 and SMA50 slope >= 0
    in_downtrend: bool                 # Price < SMA50 and SMA50 slope < 0
    is_falling_knife: bool             # SMA50 slope < -3% per 20 sessions (steep descent)


@dataclass
class Fundamentals:
    symbol: str
    name: Optional[str]
    sector: Optional[str]
    market_cap: Optional[float]
    trailing_pe: Optional[float]
    forward_pe: Optional[float]
    peg_ratio: Optional[float]
    price_to_book: Optional[float]
    return_on_equity: Optional[float]   # decimal (0.25 = 25%)
    profit_margin: Optional[float]
    operating_margin: Optional[float]
    revenue_growth: Optional[float]     # YoY decimal
    earnings_growth: Optional[float]    # YoY decimal
    debt_to_equity: Optional[float]     # yfinance reports as percent (e.g. 50.0 = 0.50)
    current_ratio: Optional[float]
    free_cash_flow: Optional[float]
    operating_cash_flow: Optional[float]
    dividend_yield: Optional[float]
    # Analyst estimate revisions (60-day change in next-fiscal-year EPS estimate).
    # Positive = analysts revising up = bullish post-earnings-announcement-drift signal.
    # Source: yfinance Ticker.eps_trend['+1y'] row, (current - 60daysAgo) / 60daysAgo.
    eps_estimate_60d_change: Optional[float] = None


def latest_price(symbol: str) -> Optional[float]:
    """Quick yfinance lookup for the most recent close. Used by pre-trade checks."""
    try:
        hist = yf.Ticker(symbol).history(period="1d", auto_adjust=True)
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None


def _atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def get_technicals(symbol: str) -> Optional[Technicals]:
    """Fetch 1y of daily bars and derive trend / volatility metrics."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1y", auto_adjust=True)
    except Exception as e:
        print(f"  yfinance history error for {symbol}: {e}")
        return None

    if hist is None or hist.empty or len(hist) < 20:
        return None

    # Fail closed on NaN bars. When yfinance is rate-limited/blocked it can return
    # frames full of NaN, and NaN comparisons silently pass every trend filter
    # (nan > sma50 and nan < sma50 are both False → "not a downtrend"). Seen live
    # 2026-06-10: all screened "survivors" had price=nan. Drop NaN rows; if too
    # few real bars remain, treat the symbol as unpriceable.
    highs, lows, closes = [], [], []
    for h, l, c in zip(hist["High"].tolist(), hist["Low"].tolist(), hist["Close"].tolist()):
        if all(isinstance(v, (int, float)) and math.isfinite(v) for v in (h, l, c)):
            highs.append(float(h))
            lows.append(float(l))
            closes.append(float(c))
    if len(closes) < 20:
        return None

    price = closes[-1]
    sma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
    sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None

    sma50_slope_pct = None
    if len(closes) >= 70:
        sma50_now = sum(closes[-50:]) / 50
        sma50_then = sum(closes[-70:-20]) / 50
        if sma50_then > 0:
            sma50_slope_pct = (sma50_now - sma50_then) / sma50_then * 100

    pct_above_sma50 = (price - sma50) / sma50 * 100 if sma50 else None
    pct_above_sma200 = (price - sma200) / sma200 * 100 if sma200 else None

    week52_high = max(closes[-252:]) if len(closes) >= 20 else max(closes)
    week52_low = min(closes[-252:]) if len(closes) >= 20 else min(closes)
    rng = week52_high - week52_low
    range_position_pct = ((price - week52_low) / rng * 100) if rng > 0 else 50.0

    atr14 = _atr(highs, lows, closes, period=14)
    atr14_pct = (atr14 / price * 100) if atr14 and price > 0 else None

    # Basing: lowest low of last 20 sessions occurred more than 10 sessions ago.
    is_basing = False
    if len(lows) >= 20:
        recent_lows = lows[-20:]
        idx_of_low = max(i for i, v in enumerate(recent_lows) if v == min(recent_lows))
        is_basing = idx_of_low < 10  # min was in the older half of the window

    in_uptrend = bool(sma50 and price > sma50 and (sma50_slope_pct or 0) >= 0)
    in_downtrend = bool(sma50 and price < sma50 and (sma50_slope_pct or 0) < 0)
    # Falling knife — broader than in_downtrend. Catches Stage 3 → Stage 4 transitions
    # where the SMA50 is falling steeply even if price hasn't broken below it yet.
    # CLAUDE.md mantra: "buy the basing knife, not the falling knife." The original
    # in_downtrend filter only blocks active Stage 4 (price already below SMA50);
    # this catches the dangerous transition territory before it. -3% per 20 sessions
    # = ~-15% annualized SMA50 trajectory, which is a clearly falling knife.
    is_falling_knife = bool(sma50_slope_pct is not None and sma50_slope_pct < -3.0)

    return Technicals(
        symbol=symbol,
        price=price,
        sma20=sma20,
        sma50=sma50,
        sma200=sma200,
        sma50_slope_pct=sma50_slope_pct,
        pct_above_sma50=pct_above_sma50,
        pct_above_sma200=pct_above_sma200,
        week52_high=week52_high,
        week52_low=week52_low,
        range_position_pct=range_position_pct,
        atr14=atr14,
        atr14_pct=atr14_pct,
        is_basing=is_basing,
        in_uptrend=in_uptrend,
        in_downtrend=in_downtrend,
        is_falling_knife=is_falling_knife,
    )


def _fetch_eps_estimate_60d_change(ticker) -> Optional[float]:
    """
    60-day change in the +1y (next fiscal year) analyst-mean EPS estimate.
    Returns a decimal (0.05 = +5%) or None if data unavailable.

    Source: yfinance Ticker.eps_trend, which gives current vs 7/30/60/90 days ago.
    Using the +1y row because it captures structural revisions to the company's
    earnings power — the strongest post-earnings-announcement-drift signal in
    50+ years of academic research (Bernard & Thomas; Chordia et al).
    """
    try:
        trend = ticker.eps_trend
    except Exception:
        return None
    if trend is None or getattr(trend, "empty", True):
        return None
    if "+1y" not in trend.index:
        return None
    try:
        cur = float(trend.loc["+1y", "current"])
        prev = float(trend.loc["+1y", "60daysAgo"])
    except (KeyError, TypeError, ValueError):
        return None
    if not prev or prev <= 0:
        return None
    return (cur - prev) / prev


def get_fundamentals(symbol: str) -> Optional[Fundamentals]:
    """Fetch fundamental snapshot from yfinance .info."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
    except Exception as e:
        print(f"  yfinance fundamentals error for {symbol}: {e}")
        return None

    if not info or not info.get("symbol") and not info.get("shortName"):
        return None

    eps_change = _fetch_eps_estimate_60d_change(ticker)

    return Fundamentals(
        symbol=symbol,
        name=info.get("shortName") or info.get("longName"),
        sector=info.get("sector"),
        market_cap=_safe_float(info.get("marketCap")),
        trailing_pe=_safe_float(info.get("trailingPE")),
        forward_pe=_safe_float(info.get("forwardPE")),
        peg_ratio=_safe_float(info.get("pegRatio") or info.get("trailingPegRatio")),
        price_to_book=_safe_float(info.get("priceToBook")),
        return_on_equity=_safe_float(info.get("returnOnEquity")),
        profit_margin=_safe_float(info.get("profitMargins")),
        operating_margin=_safe_float(info.get("operatingMargins")),
        revenue_growth=_safe_float(info.get("revenueGrowth")),
        earnings_growth=_safe_float(info.get("earningsGrowth")),
        debt_to_equity=_safe_float(info.get("debtToEquity")),
        current_ratio=_safe_float(info.get("currentRatio")),
        free_cash_flow=_safe_float(info.get("freeCashflow")),
        operating_cash_flow=_safe_float(info.get("operatingCashflow")),
        dividend_yield=_safe_float(info.get("dividendYield")),
        eps_estimate_60d_change=eps_change,
    )


def passes_quality_filter(f: Fundamentals) -> Dict:
    """
    Minimal hard floor — excludes obvious junk. Soft criteria stay in the prompt.

    Hard floors:
      - market cap >= $2B (no micro-caps; liquidity + accounting reliability)
      - operating cash flow > 0 (the business actually generates cash)
      - debt-to-equity < 300 (yfinance %, so ratio < 3.0)
      - profit margin > -10% (small losses OK; deep losses are a red flag)

    Returns {'passes': bool, 'reasons': [str]} so callers can log why something
    was excluded.
    """
    reasons = []

    if f.market_cap is None or f.market_cap < 2_000_000_000:
        reasons.append(f"market cap below $2B threshold ({f.market_cap})")

    if f.operating_cash_flow is None or f.operating_cash_flow <= 0:
        reasons.append(f"non-positive operating cash flow ({f.operating_cash_flow})")

    if f.debt_to_equity is not None and f.debt_to_equity > 300:
        reasons.append(f"debt/equity too high ({f.debt_to_equity})")

    if f.profit_margin is not None and f.profit_margin < -0.10:
        reasons.append(f"deeply unprofitable ({f.profit_margin:.1%})")

    return {"passes": len(reasons) == 0, "reasons": reasons}


def get_snapshot(symbol: str) -> Optional[Dict]:
    """
    Combined technicals + fundamentals + quality verdict for a single symbol.
    Returns None if the symbol can't be priced at all.
    """
    tech = get_technicals(symbol)
    if tech is None:
        return None

    fund = get_fundamentals(symbol)
    quality = passes_quality_filter(fund) if fund else {"passes": False, "reasons": ["fundamentals unavailable"]}

    return {
        "symbol": symbol,
        "technicals": asdict(tech),
        "fundamentals": asdict(fund) if fund else None,
        "quality": quality,
    }


def get_snapshots(symbols: List[str]) -> List[Dict]:
    """Bulk snapshot for a list of symbols. Skips symbols that fail to price."""
    out = []
    for s in symbols:
        snap = get_snapshot(s)
        if snap:
            out.append(snap)
    return out


def format_snapshot_for_prompt(snap: Dict) -> str:
    """Render a snapshot as compact text for inclusion in Claude's prompt."""
    t = snap["technicals"]
    f = snap.get("fundamentals")
    q = snap["quality"]

    def fmt_pct(v, digits=1):
        return f"{v:+.{digits}f}%" if v is not None else "n/a"

    def fmt_num(v, digits=2):
        return f"{v:.{digits}f}" if v is not None else "n/a"

    def fmt_money(v):
        if v is None:
            return "n/a"
        if v >= 1e9:
            return f"${v/1e9:.1f}B"
        if v >= 1e6:
            return f"${v/1e6:.0f}M"
        return f"${v:,.0f}"

    trend_label = (
        "DOWNTREND" if t["in_downtrend"]
        else "UPTREND" if t["in_uptrend"]
        else "MIXED/SIDEWAYS"
    )
    basing_label = "basing" if t["is_basing"] else "extending"

    lines = [
        f"--- {snap['symbol']} ({(f or {}).get('name', '?')}, {(f or {}).get('sector', '?')}) ---",
        f"  Price: ${t['price']:.2f}  |  52w range pos: {t['range_position_pct']:.0f}%  |  Trend: {trend_label} ({basing_label})",
        f"  vs SMA50: {fmt_pct(t['pct_above_sma50'])}  |  vs SMA200: {fmt_pct(t['pct_above_sma200'])}  |  SMA50 slope (20d): {fmt_pct(t['sma50_slope_pct'])}",
        f"  ATR(14): {fmt_pct(t['atr14_pct'])} of price  |  52w high: ${t['week52_high']:.2f}  low: ${t['week52_low']:.2f}",
    ]

    if f:
        lines.append(
            f"  Mkt cap: {fmt_money(f['market_cap'])}  |  P/E: {fmt_num(f['trailing_pe'])} (fwd {fmt_num(f['forward_pe'])})  |  PEG: {fmt_num(f['peg_ratio'])}  |  P/B: {fmt_num(f['price_to_book'])}"
        )
        lines.append(
            f"  ROE: {fmt_pct((f['return_on_equity'] or 0)*100) if f['return_on_equity'] is not None else 'n/a'}  |  Op margin: {fmt_pct((f['operating_margin'] or 0)*100) if f['operating_margin'] is not None else 'n/a'}  |  Rev growth: {fmt_pct((f['revenue_growth'] or 0)*100) if f['revenue_growth'] is not None else 'n/a'}"
        )
        lines.append(
            f"  D/E: {fmt_num(f['debt_to_equity'])}  |  FCF: {fmt_money(f['free_cash_flow'])}  |  Op CF: {fmt_money(f['operating_cash_flow'])}"
        )
        eps_chg = f.get("eps_estimate_60d_change")
        if eps_chg is not None:
            arrow = "↑" if eps_chg > 0.01 else ("↓" if eps_chg < -0.01 else "→")
            lines.append(f"  Analyst +1y EPS revision (60d): {eps_chg:+.1%} {arrow}")

    lines.append(f"  Quality filter: {'PASS' if q['passes'] else 'FAIL — ' + '; '.join(q['reasons'])}")

    earn = snap.get("earnings") or {}
    if earn.get("next_date"):
        lines.append(f"  Next earnings: {earn['next_date']} ({earn.get('days_until')}d out)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Snapshot cache + universe screening
# ---------------------------------------------------------------------------

def _load_snapshot_cache() -> Dict:
    """Load the per-symbol snapshot cache from disk."""
    if not os.path.exists(SNAPSHOT_CACHE_FILE):
        return {}
    try:
        with open(SNAPSHOT_CACHE_FILE, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_snapshot_cache(cache: Dict) -> None:
    try:
        with open(SNAPSHOT_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except OSError as e:
        print(f"  Warning: could not write {SNAPSHOT_CACHE_FILE}: {e}")


def get_snapshot_cached(
    symbol: str,
    ttl: int = SNAPSHOT_TTL_SECONDS,
    cache: Optional[Dict] = None,
) -> Optional[Dict]:
    """
    Cached version of get_snapshot. Returns cached snapshot if fresh, refetches otherwise.
    Mutates the passed cache in place; caller is responsible for saving.
    """
    own_cache = cache is None
    if own_cache:
        cache = _load_snapshot_cache()

    entry = cache.get(symbol)
    age = (time.time() - entry["fetched_at"]) if entry else float("inf")

    if entry and age < ttl:
        snap = entry.get("snapshot")
        if snap:
            return snap

    snap = get_snapshot(symbol)
    if snap:
        cache[symbol] = {"snapshot": snap, "fetched_at": time.time()}

    if own_cache:
        _save_snapshot_cache(cache)

    return snap


def _setup_score(snap: Dict) -> float:
    """
    Setup quality score for ranking surviving candidates. Higher is better.
    Encodes the strategy bias: prefer basing patterns at the cheap end of the range,
    reward sound fundamentals, neutral on uptrends already extending.
    """
    t = snap["technicals"]
    f = snap.get("fundamentals") or {}
    score = 0.0

    # Basing in the lower half of the range = best setup (Stage 1 → Stage 2 candidate)
    if t["is_basing"] and t["range_position_pct"] < 50:
        score += 10

    # Already in an uptrend without being too extended
    if t["in_uptrend"]:
        score += 4
        if t["range_position_pct"] is not None and t["range_position_pct"] > 90:
            score -= 3  # extended, late-cycle entry

    # Cheap end of range adds, but only when not still falling
    if t["range_position_pct"] < 30 and not t["in_downtrend"]:
        score += 3

    # Reasonable PEG (growth at fair price)
    peg = f.get("peg_ratio")
    if peg is not None and 0 < peg < 1.5:
        score += 2

    # High ROE = quality compounder
    roe = f.get("return_on_equity")
    if roe is not None and roe > 0.15:
        score += 2

    # Strong margins
    op_margin = f.get("operating_margin")
    if op_margin is not None and op_margin > 0.20:
        score += 1

    # Analyst estimate revisions (60-day change in +1y EPS forecast).
    # Post-earnings-announcement drift is one of the most robust equity factors
    # in academic literature — analysts revising estimates up tends to lead
    # outperformance for 60+ days.
    eps_chg = f.get("eps_estimate_60d_change")
    if eps_chg is not None:
        if eps_chg > 0.05:
            score += 5
        elif eps_chg > 0:
            score += 2
        elif eps_chg < -0.02:
            score -= 3

    return score


def screen_universe(
    tickers: List[str],
    force_refresh: bool = False,
    max_candidates: int = 50,
    verbose: bool = True,
) -> List[Dict]:
    """
    Screen a ticker list and return top candidates passing the strategy filters.

    Pipeline:
      1. Fetch snapshot for each (cached, 24h TTL)
      2. Hard quality filter (market cap, op CF, debt, margin)
      3. Hard trend filter (exclude confirmed downtrends)
      4. Hard earnings-proximity filter (no candidates within 7 calendar days
         of earnings — see earnings_calendar.py)
      5. Rank survivors by setup score
      6. Return top max_candidates

    Note: 500 yfinance calls is slow on first run (~5-15 minutes). Subsequent runs
    use cache. Failed fetches are skipped silently and retried next call.
    """
    from earnings_calendar import passes_earnings_filter, _load_cache as _load_earnings_cache, save_earnings_cache

    cache = {} if force_refresh else _load_snapshot_cache()
    earnings_cache = {} if force_refresh else _load_earnings_cache()

    survivors = []
    quality_failures = 0
    downtrend_failures = 0
    falling_knife_failures = 0
    earnings_failures = 0
    fetch_failures = 0

    n = len(tickers)
    progress_step = max(1, n // 20)

    for i, sym in enumerate(tickers):
        if verbose and i % progress_step == 0:
            print(
                f"  Screening {i}/{n}  |  survivors: {len(survivors)}  |  "
                f"q_fail: {quality_failures}  |  trend_fail: {downtrend_failures}  |  "
                f"knife_fail: {falling_knife_failures}  |  earnings_fail: {earnings_failures}"
            )

        snap = get_snapshot_cached(sym, cache=cache)
        if not snap:
            fetch_failures += 1
            continue

        if not snap["quality"]["passes"]:
            quality_failures += 1
            continue

        if snap["technicals"]["in_downtrend"]:
            downtrend_failures += 1
            continue

        # CLAUDE.md "buy the basing knife, not the falling knife" — exclude steep
        # SMA50 descents even if price hasn't broken through yet (Stage 3 → Stage 4).
        if snap["technicals"].get("is_falling_knife"):
            falling_knife_failures += 1
            continue

        # Earnings-proximity filter: skip names with a binary event in the next week.
        earn = passes_earnings_filter(sym, cache=earnings_cache)
        snap["earnings"] = {
            "next_date": earn.get("next_date"),
            "days_until": earn.get("days_until"),
        }
        if not earn["passes"]:
            earnings_failures += 1
            continue

        survivors.append(snap)

    _save_snapshot_cache(cache)
    save_earnings_cache(earnings_cache)

    # Data-quality circuit breaker: if most of the universe failed to price, the
    # data source is down (not the market) — abort loudly instead of emitting a
    # plan built from whatever stale survivors remain. (2026-06-10: a yfinance
    # outage produced an all-NaN screen that read as a normal "no setups" day.)
    if n >= 50 and fetch_failures > n * 0.5:
        raise RuntimeError(
            f"Screen data-quality failure: {fetch_failures}/{n} tickers unpriceable — "
            "price source likely rate-limited or down; refusing to emit a plan"
        )

    if verbose:
        print(
            f"\n  Screen complete: {len(survivors)} survivors / {n} screened  "
            f"(quality_fail={quality_failures}, downtrend_fail={downtrend_failures}, "
            f"falling_knife_fail={falling_knife_failures}, earnings_fail={earnings_failures}, "
            f"fetch_fail={fetch_failures})"
        )

    ranked = sorted(survivors, key=_setup_score, reverse=True)
    return ranked[:max_candidates]


def screen_sp500(force_refresh: bool = False, max_candidates: int = 50) -> List[Dict]:
    """Convenience: screen the full S&P 500 universe and return top candidates."""
    from sp500_universe import get_sp500_tickers
    tickers = get_sp500_tickers()
    print(f"Screening {len(tickers)} S&P 500 tickers (cache TTL {SNAPSHOT_TTL_SECONDS//3600}h)...")
    return screen_universe(tickers, force_refresh=force_refresh, max_candidates=max_candidates)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python market_data.py <SYMBOL> [<SYMBOL> ...]    # snapshot one or more symbols")
        print("  python market_data.py --screen [N]               # screen S&P 500, show top N (default 20)")
        print("  python market_data.py --screen-fresh [N]         # screen ignoring cache")
        sys.exit(1)

    if sys.argv[1] in ("--screen", "--screen-fresh"):
        force = sys.argv[1] == "--screen-fresh"
        top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        candidates = screen_sp500(force_refresh=force, max_candidates=top_n)
        print(f"\n{'='*70}\nTOP {len(candidates)} CANDIDATES (ranked by setup score)\n{'='*70}\n")
        for c in candidates:
            print(format_snapshot_for_prompt(c))
            print(f"  Setup score: {_setup_score(c):.1f}")
            print()
    else:
        for sym in sys.argv[1:]:
            snap = get_snapshot(sym.upper())
            if snap:
                print(format_snapshot_for_prompt(snap))
                print()
            else:
                print(f"{sym}: could not load snapshot\n")
