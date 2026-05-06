"""
Market regime detector — determines whether the index is in a confirmed downtrend.

Used by the downtrend-mode entry path (Form 4 cluster signals). When SPY is in
a confirmed downtrend, the existing trend-mode entry rules in ai_strategy_enhanced
suspend in favor of insider-cluster signals.

Definition mirrors the per-stock downtrend rule in market_data.Technicals applied
to SPY as the regime proxy: price < SMA50 AND SMA50 slope < 0 over 20 sessions.
This keeps the regime selector consistent with how the stock-level filter already
works in market_data.py.

Not wired into morning_routine.py / ai_strategy_enhanced.py yet — runnable as a
standalone diagnostic via `python regime.py`.
"""

from dataclasses import dataclass
from typing import Optional

from market_data import get_technicals


@dataclass
class Regime:
    is_downtrend: bool
    label: str                          # "DOWNTREND" / "UPTREND" / "MIXED"
    proxy_symbol: str                   # which ticker we used (default SPY)
    price: float
    sma50: Optional[float]
    pct_above_sma50: Optional[float]
    sma50_slope_pct: Optional[float]    # 20-session change in SMA50


def detect_regime(symbol: str = "SPY") -> Optional[Regime]:
    """Returns regime based on the proxy's SMA50 + slope. None if data unavailable."""
    t = get_technicals(symbol)
    if t is None:
        return None

    label = (
        "DOWNTREND" if t.in_downtrend
        else "UPTREND" if t.in_uptrend
        else "MIXED"
    )
    return Regime(
        is_downtrend=t.in_downtrend,
        label=label,
        proxy_symbol=symbol,
        price=t.price,
        sma50=t.sma50,
        pct_above_sma50=t.pct_above_sma50,
        sma50_slope_pct=t.sma50_slope_pct,
    )


def format_regime(r: Regime) -> str:
    def fmt(v, d=1):
        return f"{v:+.{d}f}%" if v is not None else "n/a"
    return (
        f"Regime ({r.proxy_symbol}): {r.label}\n"
        f"  Price: ${r.price:.2f}  |  SMA50: "
        f"{f'${r.sma50:.2f}' if r.sma50 else 'n/a'}  |  vs SMA50: {fmt(r.pct_above_sma50)}\n"
        f"  SMA50 slope (20d): {fmt(r.sma50_slope_pct, d=2)}\n"
        f"  Downtrend mode active: {'YES' if r.is_downtrend else 'NO'}"
    )


if __name__ == "__main__":
    import sys
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "SPY"
    r = detect_regime(sym)
    if r is None:
        print(f"Failed to load technicals for {sym}")
        sys.exit(1)
    print(format_regime(r))
