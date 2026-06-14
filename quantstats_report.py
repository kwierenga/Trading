"""
QuantStats reporting layer (read-only analytics — never touches the trade path).

Turns the daily equity series in `portfolio_history.json` into:
  - a compact text metrics block (Sharpe / Sortino / max-drawdown / CAGR /
    volatility, plus the SPY-relative numbers) for the weekly email + journal, and
  - a full HTML tear-sheet, benchmarked against SPY, attached to the weekly email.

Why weekly and not daily: the tear-sheet is rich and pulls matplotlib/seaborn on
import, so it belongs in the once-a-week reflection step, not the lean daily EOD
path. The honest-scoreboard question this answers — "is the picking adding
risk-adjusted value over just owning SPY?" — is the same one the shadow benchmark
(SPY 70% + USMV 30%) asks in the EOD email; this adds the risk-adjusted lens
(Sharpe/Sortino/drawdown) on top of the raw spread.

Everything here is best-effort and defensive: any failure returns an
``{"available": False, "reason": ...}`` dict (or None for the HTML) so the caller
can degrade gracefully, exactly like `shadow_benchmark.py`.

SPY is downloaded via yfinance (the project's only price source), matching the
pattern in `shadow_benchmark.py`, rather than relying on quantstats' internal
downloader.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

HISTORY_FILE = Path("portfolio_history.json")
BENCHMARK_TICKER = "SPY"

# quantstats needs a minimum of daily observations to produce meaningful
# annualized figures. Below this we report "not enough history yet" rather than
# emit a misleading Sharpe off a handful of points.
MIN_RETURN_POINTS = 10


def _load_equity_series(history_file: Path = HISTORY_FILE) -> Optional[pd.Series]:
    """Daily equity series from portfolio_history.json (one value per calendar
    date — the last snapshot of each day wins). Returns None if unavailable."""
    if not history_file.exists():
        return None
    try:
        history = json.loads(history_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not history:
        return None

    by_date: Dict[pd.Timestamp, float] = {}
    for snap in history:
        try:
            ts = snap.get("timestamp")
            equity = float(snap["account"]["equity"])
            day = pd.Timestamp(ts).tz_localize(None).normalize()
        except (KeyError, ValueError, TypeError):
            continue
        by_date[day] = equity  # later snapshot for the same day overwrites

    if not by_date:
        return None
    series = pd.Series(by_date).sort_index()
    series.index = pd.DatetimeIndex(series.index)
    return series


def _to_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def _spy_returns(start: pd.Timestamp, end: pd.Timestamp) -> Optional[pd.Series]:
    """Daily SPY returns over [start, end] via yfinance. None on any failure."""
    try:
        import yfinance as yf

        hist = yf.Ticker(BENCHMARK_TICKER).history(
            start=(start - pd.Timedelta(days=5)).date().isoformat(),
            end=(end + pd.Timedelta(days=2)).date().isoformat(),
            auto_adjust=True,
        )
    except Exception:
        return None
    if hist is None or hist.empty:
        return None
    close = hist["Close"]
    close.index = pd.DatetimeIndex(close.index).tz_localize(None).normalize()
    rets = close.pct_change().dropna()
    rets.name = BENCHMARK_TICKER
    return rets


def compute_metrics(history_file: Path = HISTORY_FILE) -> Dict:
    """Risk-adjusted metrics for the live equity curve, with SPY comparison.

    Returns a dict with ``available`` and, when available, the metric fields.
    Never raises.
    """
    equity = _load_equity_series(history_file)
    if equity is None or len(equity) < 2:
        return {"available": False, "reason": "no portfolio_history.json equity data yet"}

    returns = _to_returns(equity)
    if len(returns) < MIN_RETURN_POINTS:
        return {
            "available": False,
            "reason": f"only {len(returns)} daily return(s); need >= {MIN_RETURN_POINTS}",
        }

    try:
        import quantstats as qs
    except ImportError:
        return {"available": False, "reason": "quantstats not installed"}

    try:
        out = {
            "available": True,
            "n_days": int(len(returns)),
            "start": equity.index[0].date().isoformat(),
            "end": equity.index[-1].date().isoformat(),
            "sharpe": float(qs.stats.sharpe(returns)),
            "sortino": float(qs.stats.sortino(returns)),
            "max_drawdown": float(qs.stats.max_drawdown(returns)),
            "cagr": float(qs.stats.cagr(returns)),
            "volatility": float(qs.stats.volatility(returns)),
        }
    except Exception as e:  # quantstats internals are not exception-typed cleanly
        return {"available": False, "reason": f"quantstats stats failed: {e}"}

    spy = _spy_returns(equity.index[0], equity.index[-1])
    if spy is not None and not spy.empty:
        try:
            out["spy_cagr"] = float(qs.stats.cagr(spy))
            out["spy_sharpe"] = float(qs.stats.sharpe(spy))
            out["spy_max_drawdown"] = float(qs.stats.max_drawdown(spy))
            out["excess_cagr_vs_spy"] = out["cagr"] - out["spy_cagr"]
        except Exception:
            pass  # keep the strategy-only metrics; SPY comparison is a bonus

    return out


def text_block(metrics: Optional[Dict] = None, history_file: Path = HISTORY_FILE) -> str:
    """Compact email/journal lines. Computes metrics if not passed in."""
    if metrics is None:
        metrics = compute_metrics(history_file)

    if not metrics.get("available"):
        return f"RISK-ADJUSTED METRICS (quantstats): not available ({metrics.get('reason', 'unknown')})"

    lines = [
        f"RISK-ADJUSTED METRICS (quantstats, {metrics['start']} → {metrics['end']}, "
        f"{metrics['n_days']} trading days):",
        f"  Sharpe {metrics['sharpe']:+.2f}   Sortino {metrics['sortino']:+.2f}   "
        f"Vol(ann) {metrics['volatility']:.1%}   MaxDD {metrics['max_drawdown']:.1%}   "
        f"CAGR {metrics['cagr']:+.1%}",
    ]
    if "spy_cagr" in metrics:
        lines.append(
            f"  vs SPY:  CAGR {metrics['spy_cagr']:+.1%}   Sharpe {metrics['spy_sharpe']:+.2f}   "
            f"MaxDD {metrics['spy_max_drawdown']:.1%}   "
            f"→ excess CAGR {metrics['excess_cagr_vs_spy']:+.1%}"
        )
    return "\n".join(lines)


def generate_html(
    output_path: str = "weekly_tearsheet.html",
    title: str = "Strategy vs SPY — Weekly Tear-Sheet",
    history_file: Path = HISTORY_FILE,
) -> Optional[str]:
    """Write a full SPY-benchmarked HTML tear-sheet. Returns the path on success,
    None on any failure (caller should treat the attachment as optional)."""
    equity = _load_equity_series(history_file)
    if equity is None or len(equity) < 2:
        return None
    returns = _to_returns(equity)
    if len(returns) < MIN_RETURN_POINTS:
        return None

    try:
        import quantstats as qs
    except ImportError:
        return None

    spy = _spy_returns(equity.index[0], equity.index[-1])
    try:
        qs.reports.html(
            returns,
            benchmark=spy if (spy is not None and not spy.empty) else None,
            output=output_path,
            title=title,
            benchmark_title="SPY" if spy is not None else None,
            strategy_title="Strategy",
        )
    except Exception:
        # Retry without benchmark — a flaky SPY download shouldn't kill the sheet.
        try:
            qs.reports.html(returns, output=output_path, title=title, strategy_title="Strategy")
        except Exception:
            return None

    return output_path if Path(output_path).exists() else None


if __name__ == "__main__":
    import sys

    # Match the daily/weekly routines: force UTF-8 so the → glyph in the metrics
    # block doesn't crash print() under a cp1252 Windows console.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(text_block())
    if "--html" in sys.argv:
        path = generate_html()
        print(f"HTML tear-sheet: {path}")
