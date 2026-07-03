"""
Position Sizing Module

Strategy rules (see CLAUDE.md):
- Max 25% of equity per single position (HARD CAP).
- Target ~1.5% portfolio risk per trade (capped at 2%).
- Stops are ATR-based (~1.75x ATR(14)), clamped to [4%, 15%]. Quality large-caps
  routinely have ATR(14) ~2-3% — a 1.75x ATR stop on PYPL/MSFT/etc. lands at 3.5-5%,
  which is what the ATR rule intends. The 4% floor only rejects sub-1% ATR readings
  that almost certainly indicate stale or wrong data. The 15% ceiling matches Klaas's
  per-name drawdown tolerance from CLAUDE.md exactly.
- If ATR is missing or zero, REFUSE to size the trade (return error). Previous
  behavior fell back to a hard 15% default stop, which silently applied the
  ceiling-case stop to trades with insufficient data. Better to skip than guess.
"""

import json
import os
from typing import Dict, List, Optional, Tuple
from alpaca_client import AlpacaClient


# ---------------------------------------------------------------------------
# Strategy constants
# ---------------------------------------------------------------------------

MAX_POSITION_PCT = 0.25         # 25% concentration cap for fresh entries (hard)
MAX_PYRAMID_PCT = 0.30          # 30% cap for pyramid-add tranches (already-confirmed winner = different risk profile)
MAX_PYRAMID_TRANCHE_PCT = 0.10  # a single pyramid ADD is ~10% of equity (pyramid.py's 8-12% design band). Execute must cap re-sized tranches here — risk-target sizing on a tight break-even stop otherwise balloons an add to ~18% (LII 2026-06-26..07-01: three days of concentration blocks, then a 29%-of-equity combined position)
MAX_SECTOR_PCT = 0.35           # 35% per GICS sector cap (hard) — see validate_sector_concentration
MAX_GROSS_PCT = 0.95            # 95% aggregate gross-deployment cap (hard) — NO margin on the automated path; see validate_gross_deployment (locked 2026-05-18)
TARGET_RISK_PER_TRADE = 0.015   # 1.5% portfolio risk on a stopped-out trade (target)
MAX_RISK_PER_TRADE = 0.02       # 2% portfolio risk on a stopped-out trade (hard cap)
ATR_STOP_MULTIPLE = 1.75        # stop distance = 1.75 * ATR(14) by default
MIN_STOP_PCT = 0.04             # below ~1% ATR is almost certainly a data issue; rejects those
MAX_STOP_PCT = 0.15             # cap matches CLAUDE.md 15% per-name drawdown tolerance exactly


class PositionSizer:
    """Calculate position sizes based on the strategy's risk rules."""

    def __init__(self, account_equity: float, win_rate: float = 0.50,
                 avg_win: float = 0.015, avg_loss: float = 0.010):
        self.account_equity = account_equity
        self.win_rate = win_rate
        self.avg_win = avg_win
        self.avg_loss = avg_loss

    # ----- Sizing helpers (legacy methods kept; now bounded by MAX_POSITION_PCT) -----

    def kelly_criterion(self, max_risk_pct: float = MAX_RISK_PER_TRADE) -> Tuple[float, float]:
        """Fractional Kelly. Returns (kelly_fraction, position_size_pct)."""
        if self.win_rate <= 0 or self.win_rate >= 1:
            return 0.0, 0.0
        p, q = self.win_rate, 1 - self.win_rate
        b = self.avg_win / self.avg_loss if self.avg_loss > 0 else 1.0
        kelly_fraction = max(0, (p * b - q) / b)
        kelly_fraction = min(kelly_fraction, MAX_POSITION_PCT)  # cap at concentration limit
        return kelly_fraction, kelly_fraction * max_risk_pct

    def fixed_fractional(self, risk_per_trade_pct: float = TARGET_RISK_PER_TRADE) -> float:
        return min(risk_per_trade_pct, MAX_RISK_PER_TRADE)

    def volatility_adjusted(self, stock_price: float, atr: float,
                            risk_per_trade_pct: float = TARGET_RISK_PER_TRADE) -> Tuple[float, float]:
        """Size shares so that 1xATR adverse move loses risk_per_trade_pct of equity."""
        if atr <= 0 or stock_price <= 0:
            return 0, 0
        max_loss = self.account_equity * risk_per_trade_pct
        shares = max_loss / atr
        position_value = shares * stock_price
        position_pct = position_value / self.account_equity
        if position_pct > MAX_POSITION_PCT:
            shares = (self.account_equity * MAX_POSITION_PCT) / stock_price
            position_pct = MAX_POSITION_PCT
        return shares, position_pct

    def max_position_size(self, risk_pct: float = TARGET_RISK_PER_TRADE) -> float:
        return MAX_POSITION_PCT

    # ----- New ATR-based sizing (preferred path) -----

    @staticmethod
    def atr_based_stop(entry_price: float, atr14: Optional[float],
                        multiple: float = ATR_STOP_MULTIPLE) -> Tuple[float, float, str]:
        """
        Compute stop price from entry + ATR(14).
        Returns (stop_price, stop_pct, note). stop_pct is decimal (0.15 = 15% below entry).
        Falls back to default 15% if ATR is missing.

        The stop is clamped to [MIN_STOP_PCT, MAX_STOP_PCT] so:
          - Quiet stocks don't get whipsawed by a 4% noise stop.
          - Volatile stocks don't blow past 18% per-name risk.
        """
        if atr14 is None or atr14 <= 0 or entry_price <= 0:
            # Refuse to fabricate a stop when ATR data is missing. A trade without
            # ATR is a trade without enough information; let the caller skip it.
            note = "no ATR data — cannot compute ATR-based stop; trade should be skipped"
            return 0.0, 0.0, note

        raw_stop_distance = atr14 * multiple
        raw_stop_pct = raw_stop_distance / entry_price

        clamped_pct = max(MIN_STOP_PCT, min(MAX_STOP_PCT, raw_stop_pct))
        note = f"{multiple:.2f}x ATR = {raw_stop_pct:.1%}"
        if clamped_pct != raw_stop_pct:
            note += f" -> clamped to {clamped_pct:.1%}"

        stop_price = entry_price * (1 - clamped_pct)
        return stop_price, clamped_pct, note

    def calculate_for_trade(self, symbol: str, current_price: float,
                            stop_loss_price: float, atr14: Optional[float] = None,
                            method: str = "atr",
                            max_position_pct: Optional[float] = None) -> Dict:
        """
        Calculate recommended position size for a trade, honoring:
          - target ~1.5% portfolio risk on full stop hit
          - hard 25% concentration cap
          - stop floor/ceiling

        Args:
            symbol: ticker
            current_price: entry price (must be > 0)
            stop_loss_price: proposed stop (will be sanity-checked)
            atr14: ATR(14) for ATR-based sizing (optional)
            method: 'atr' (preferred), 'fixed', 'kelly', 'volatility'

        Returns dict with shares, position_value, position_pct, expected_loss_pct,
        method, and a 'warnings' list.
        """
        warnings = []

        if current_price <= 0 or stop_loss_price <= 0 or stop_loss_price >= current_price:
            return {
                'symbol': symbol,
                'recommended_shares': 0,
                'position_value': 0,
                'position_pct': 0,
                'error': 'invalid entry/stop prices',
            }

        risk_per_share = current_price - stop_loss_price
        risk_pct_per_trade = risk_per_share / current_price

        if risk_pct_per_trade < MIN_STOP_PCT:
            warnings.append(f"stop {risk_pct_per_trade:.1%} below MIN_STOP_PCT {MIN_STOP_PCT:.0%} — likely whipsaw risk")
        if risk_pct_per_trade > MAX_STOP_PCT:
            warnings.append(f"stop {risk_pct_per_trade:.1%} above MAX_STOP_PCT {MAX_STOP_PCT:.0%} — single-name risk too high")

        # Position-sizing core: risk_dollars / risk_per_share = shares
        target_risk_dollars = self.account_equity * TARGET_RISK_PER_TRADE
        shares_by_risk = target_risk_dollars / risk_per_share

        # Concentration cap (hard ceiling). Pyramid adds may pass a larger cap
        # since they're going into already-confirmed winners (different risk profile).
        cap_pct = max_position_pct if max_position_pct is not None else MAX_POSITION_PCT
        max_shares_by_concentration = (self.account_equity * cap_pct) / current_price

        if shares_by_risk > max_shares_by_concentration:
            shares = int(max_shares_by_concentration)
            warnings.append(
                f"concentration cap binding: risk-based size ({int(shares_by_risk)}) "
                f"exceeds {cap_pct:.0%} cap ({shares} shares = ${shares*current_price:,.0f})"
            )
        else:
            shares = int(shares_by_risk)

        position_value = shares * current_price
        position_pct = position_value / self.account_equity if self.account_equity > 0 else 0
        expected_loss_dollars = shares * risk_per_share
        expected_loss_pct = expected_loss_dollars / self.account_equity if self.account_equity > 0 else 0

        return {
            'symbol': symbol,
            'recommended_shares': shares,
            'position_value': position_value,
            'position_pct': position_pct,
            'entry_price': current_price,
            'stop_loss': stop_loss_price,
            'stop_pct': risk_pct_per_trade,
            'expected_loss_dollars': expected_loss_dollars,
            'expected_loss_pct': expected_loss_pct,
            'method': method,
            'warnings': warnings,
        }


# ---------------------------------------------------------------------------
# Concentration validation (used by claude_trader / pre-trade hook)
# ---------------------------------------------------------------------------

def validate_concentration(
    symbol: str,
    proposed_value: float,
    account_equity: float,
    current_positions: List[Dict],
    cap_pct: Optional[float] = None,
) -> Dict:
    """
    Pre-trade check: does adding this position violate the concentration cap?

    Args:
        symbol: ticker being added (or added to)
        proposed_value: dollar value of NEW shares being added (entry_price * new_shares)
        account_equity: current total equity
        current_positions: list from AlpacaClient.get_positions()
        cap_pct: override for the cap (defaults to MAX_POSITION_PCT 25%; pass
                 MAX_PYRAMID_PCT 30% for pyramid trades on confirmed winners)

    Returns dict with:
      ok (bool), reason (str), would_be_pct (float), cap_pct (float)
    """
    effective_cap = cap_pct if cap_pct is not None else MAX_POSITION_PCT

    existing_value = 0.0
    for p in current_positions:
        if p.get('symbol', '').upper() == symbol.upper():
            existing_value = float(p.get('market_value', 0))
            break

    combined_value = existing_value + proposed_value
    would_be_pct = combined_value / account_equity if account_equity > 0 else 1.0

    if would_be_pct > effective_cap:
        return {
            'ok': False,
            'reason': (
                f"{symbol} would be {would_be_pct:.1%} of equity (existing ${existing_value:,.0f} + "
                f"new ${proposed_value:,.0f} = ${combined_value:,.0f}); cap is {effective_cap:.0%}"
            ),
            'would_be_pct': would_be_pct,
            'cap_pct': effective_cap,
            'existing_value': existing_value,
            'proposed_value': proposed_value,
        }

    return {
        'ok': True,
        'reason': f"{symbol} concentration OK ({would_be_pct:.1%} of equity, cap {effective_cap:.0%})",
        'would_be_pct': would_be_pct,
        'cap_pct': effective_cap,
        'existing_value': existing_value,
        'proposed_value': proposed_value,
    }


# ---------------------------------------------------------------------------
# Aggregate gross-deployment validation (locked 2026-05-18)
# ---------------------------------------------------------------------------

def validate_gross_deployment(
    symbol: str,
    proposed_value: float,
    account_equity: float,
    current_positions: List[Dict],
    cap_pct: Optional[float] = None,
) -> Dict:
    """
    Pre-trade check: would adding this position push TOTAL deployment over the
    aggregate gross cap? Closes the gap where five independently-sized 25%
    positions stacked into 1.38x accidental margin. NO margin on the automated
    path — see CLAUDE.md "Aggregate gross cap (locked 2026-05-18)".

    Unlike validate_concentration (which only counts the same-symbol existing
    value), this sums the market value of ALL current positions, since
    proposed_value is the NEW exposure being layered on top of the whole book.

    Args:
        symbol: ticker being added (or added to) — for the message only
        proposed_value: dollar value of NEW shares (entry_price * new_shares)
        account_equity: current total equity
        current_positions: list from AlpacaClient.get_positions()
        cap_pct: override for the cap (defaults to MAX_GROSS_PCT 95%)

    Returns dict with:
      ok (bool), reason (str), would_be_pct (float), cap_pct (float),
      current_gross (float), proposed_value (float), headroom (float)
    """
    effective_cap = cap_pct if cap_pct is not None else MAX_GROSS_PCT

    current_gross = 0.0
    for p in current_positions:
        try:
            current_gross += float(p.get('market_value', 0) or 0)
        except (TypeError, ValueError):
            continue

    combined_value = current_gross + proposed_value
    would_be_pct = combined_value / account_equity if account_equity > 0 else 1.0
    headroom = effective_cap * account_equity - current_gross

    if would_be_pct > effective_cap:
        return {
            'ok': False,
            'reason': (
                f"{symbol} would push gross to {would_be_pct:.1%} of equity "
                f"(current ${current_gross:,.0f} + new ${proposed_value:,.0f} = "
                f"${combined_value:,.0f}); cap is {effective_cap:.0%} "
                f"(${effective_cap*account_equity:,.0f}), headroom "
                f"${max(headroom, 0):,.0f}. NO margin on the automated path."
            ),
            'would_be_pct': would_be_pct,
            'cap_pct': effective_cap,
            'current_gross': current_gross,
            'proposed_value': proposed_value,
            'headroom': headroom,
        }

    return {
        'ok': True,
        'reason': (
            f"gross OK ({would_be_pct:.1%} of equity after add, cap "
            f"{effective_cap:.0%}, headroom ${max(headroom, 0):,.0f})"
        ),
        'would_be_pct': would_be_pct,
        'cap_pct': effective_cap,
        'current_gross': current_gross,
        'proposed_value': proposed_value,
        'headroom': headroom,
    }


# ---------------------------------------------------------------------------
# Sector concentration validation (RECOMMENDATIONS_top10 #6)
# ---------------------------------------------------------------------------

def _resolve_sector(symbol: str) -> Optional[str]:
    """
    Look up the GICS sector for `symbol`. Reads the snapshot cache first
    (instant if the symbol was screened recently), falls back to a fresh
    yfinance call otherwise. Returns None on any failure.
    """
    try:
        from market_data import _load_snapshot_cache, get_fundamentals
    except ImportError:
        return None

    cache = _load_snapshot_cache()
    entry = cache.get(symbol)
    if entry and entry.get("snapshot"):
        fund = entry["snapshot"].get("fundamentals") or {}
        sec = fund.get("sector")
        if sec:
            return sec

    fund = get_fundamentals(symbol)
    return fund.sector if fund else None


def validate_sector_concentration(
    symbol: str,
    proposed_value: float,
    account_equity: float,
    current_positions: List[Dict],
    sector_lookup=None,
) -> Dict:
    """
    Pre-trade check: would adding `proposed_value` of `symbol` push that sector's
    share of equity over MAX_SECTOR_PCT?

    Args:
        symbol: ticker being added
        proposed_value: dollar value of NEW shares being added
        account_equity: current total equity
        current_positions: list from AlpacaClient.get_positions()
        sector_lookup: optional callable str -> Optional[str] (e.g. for tests
                       or to share a pre-built cache). Defaults to _resolve_sector.

    Returns dict with: ok (bool), reason (str), sector (str), would_be_pct (float),
    cap_pct (float). On sector lookup failure, returns ok=True with reason noting
    the lookup couldn't resolve — better to allow than to block on data error,
    since per-name concentration is still enforced separately.
    """
    lookup = sector_lookup or _resolve_sector
    new_sector = lookup(symbol)
    if not new_sector:
        return {
            "ok": True,
            "reason": f"sector lookup failed for {symbol} — sector cap not enforced (per-name cap still binds)",
            "sector": None,
            "would_be_pct": None,
            "cap_pct": MAX_SECTOR_PCT,
        }

    existing_by_sector: Dict[str, float] = {}
    for p in current_positions:
        sym = (p.get("symbol") or "").upper()
        if not sym:
            continue
        sec = lookup(sym)
        if not sec:
            continue
        existing_by_sector[sec] = existing_by_sector.get(sec, 0.0) + float(p.get("market_value", 0))

    combined_sector_value = existing_by_sector.get(new_sector, 0.0) + proposed_value
    would_be_pct = combined_sector_value / account_equity if account_equity > 0 else 1.0

    if would_be_pct > MAX_SECTOR_PCT:
        return {
            "ok": False,
            "reason": (
                f"{new_sector} sector would be {would_be_pct:.1%} of equity "
                f"(existing ${existing_by_sector.get(new_sector, 0.0):,.0f} + new ${proposed_value:,.0f} "
                f"= ${combined_sector_value:,.0f}); cap is {MAX_SECTOR_PCT:.0%}"
            ),
            "sector": new_sector,
            "would_be_pct": would_be_pct,
            "cap_pct": MAX_SECTOR_PCT,
        }

    return {
        "ok": True,
        "reason": f"{new_sector} sector concentration OK ({would_be_pct:.1%} of equity, cap {MAX_SECTOR_PCT:.0%})",
        "sector": new_sector,
        "would_be_pct": would_be_pct,
        "cap_pct": MAX_SECTOR_PCT,
    }


# ---------------------------------------------------------------------------
# Trade statistics (used by AI strategy and portfolio_monitor)
# ---------------------------------------------------------------------------

def get_trade_statistics() -> Dict:
    """Load trade history and compute win-rate stats."""
    default = {'win_rate': 0.50, 'avg_win': 0.015, 'avg_loss': 0.010, 'trades': 0}

    if not os.path.exists('trade_journal.json'):
        return default

    try:
        with open('trade_journal.json', 'r') as f:
            trades = json.load(f)
    except (OSError, json.JSONDecodeError):
        return default

    if not trades:
        return default

    closed = [t for t in trades if t.get('status') == 'closed']
    if not closed:
        return default

    wins = sum(1 for t in closed if (t.get('pnl_pct') or 0) > 0)
    total = len(closed)
    win_rate = wins / total if total else 0.5

    winning_trades = [t for t in closed if (t.get('pnl_pct') or 0) > 0]
    losing_trades = [t for t in closed if (t.get('pnl_pct') or 0) <= 0]

    avg_win = (sum((t.get('pnl_pct') or 0) for t in winning_trades) / len(winning_trades)) if winning_trades else 0.015
    avg_loss = abs(sum((t.get('pnl_pct') or 0) for t in losing_trades) / len(losing_trades)) if losing_trades else 0.010

    return {
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'trades': total,
        'wins': wins,
        'losses': total - wins,
    }


def calculate_position_size(symbol: str, current_price: float,
                            stop_loss_price: float,
                            atr14: Optional[float] = None) -> Dict:
    """Top-level helper: pulls account state, then sizes a trade."""
    from config import ALPACA_API_KEY

    if not ALPACA_API_KEY:
        print("ALPACA_API_KEY not set in .env")
        return {}

    client = AlpacaClient()
    account = client.get_account()
    stats = get_trade_statistics()

    sizer = PositionSizer(
        account_equity=float(account['equity']),
        win_rate=stats['win_rate'],
        avg_win=stats['avg_win'],
        avg_loss=stats['avg_loss'],
    )

    return sizer.calculate_for_trade(symbol, current_price, stop_loss_price, atr14=atr14)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 4:
        print("Usage: python position_sizer.py <symbol> <entry_price> <stop_loss_price> [atr14]")
        sys.exit(1)

    symbol = sys.argv[1]
    entry = float(sys.argv[2])
    stop = float(sys.argv[3])
    atr = float(sys.argv[4]) if len(sys.argv) > 4 else None

    rec = calculate_position_size(symbol, entry, stop, atr14=atr)

    if 'error' in rec:
        print(f"Error: {rec['error']}")
        sys.exit(1)

    print(f"\nPosition sizing for {rec['symbol']}")
    print(f"  Entry:  ${rec['entry_price']:.2f}")
    print(f"  Stop:   ${rec['stop_loss']:.2f}  ({rec['stop_pct']:.1%})")
    print(f"  Shares: {rec['recommended_shares']}")
    print(f"  Value:  ${rec['position_value']:,.2f}  ({rec['position_pct']:.2%} of equity)")
    print(f"  Risk:   ${rec['expected_loss_dollars']:,.2f}  ({rec['expected_loss_pct']:.2%} of equity if stopped)")
    if rec.get('warnings'):
        print("  Warnings:")
        for w in rec['warnings']:
            print(f"    - {w}")
