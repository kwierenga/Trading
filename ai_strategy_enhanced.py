"""
Enhanced AI Strategy with Quality-Value + Trend Confirmation
Provides Claude with:
  - Account state (equity, positions, cash, recent trades, risk metrics)
  - A pre-screened S&P 500 candidate list (passes hard quality + trend filters)
    with full technical + fundamental snapshots for each.

The prompt enforces the strategy rules from CLAUDE.md:
  - Long-only US stocks, S&P 500 universe
  - 25% max concentration, ~15% stops sized to 1-2% portfolio risk
  - Quality fundamentals + buy basing patterns, not falling knives
  - Hold weeks to months by default; respect 1-year LTCG line
  - 1-3 high-conviction trades or zero (cash is a position)
"""

import json
import os
from typing import Dict, List, Optional
from datetime import datetime, timezone

import anthropic

from alpaca_client import AlpacaClient
from performance_tracker import load_history, calculate_weekly_returns
from trade_journal import TradeJournal
from portfolio_metrics import PortfolioMetrics
from market_data import screen_sp500, format_snapshot_for_prompt
from position_sizer import MAX_GROSS_PCT


# How many top-ranked screen survivors to send to Claude. Enough to give
# meaningful choice without bloating the prompt.
MAX_CANDIDATES_FOR_PROMPT = 30


def build_rich_context() -> Dict:
    """Build account/portfolio context (no candidate data here — see build_candidate_block)."""
    from config import ALPACA_API_KEY

    if not ALPACA_API_KEY:
        return {}

    client = AlpacaClient()
    account = client.get_account()
    positions = client.get_positions()

    equity = float(account['equity'])
    buying_power = float(account['buying_power'])
    cash = float(account['cash'])

    trade_stats = TradeJournal.get_statistics()

    heat = PortfolioMetrics.calculate_portfolio_heat(positions, equity)
    max_dd = PortfolioMetrics.calculate_max_drawdown()
    volatility = PortfolioMetrics.calculate_volatility()
    sortino = PortfolioMetrics.calculate_sortino_ratio()

    history = load_history()
    weekly_perf = None
    if history and len(history) > 0:
        accounts = [h['account'] for h in history]
        weekly_perf = calculate_weekly_returns(accounts)

    trades = TradeJournal.load_trades()
    recent_closed_trades = sorted(
        [t for t in trades if t['status'] == 'closed'],
        key=lambda x: x['exit_time'],
        reverse=True
    )[:5]

    return {
        'account': {
            'equity': equity,
            'buying_power': buying_power,
            'cash': cash,
            'num_positions': len(positions),
        },
        'trade_statistics': {
            'total_trades': trade_stats['total_trades'],
            'win_rate': trade_stats['win_rate'],
            'avg_win_pct': trade_stats['avg_win'],
            'avg_loss_pct': trade_stats['avg_loss'],
            'profit_factor': trade_stats['profit_factor'],
            'wins': trade_stats['wins'],
            'losses': trade_stats['losses'],
        },
        'portfolio_risk': {
            'portfolio_heat_pct': heat['total_heat_pct'],
            'max_drawdown_pct': max_dd,
            'volatility_annualized_pct': volatility,
            'sortino_ratio': sortino,
        },
        'current_positions': [
            {
                'symbol': p['symbol'],
                'shares': p['qty'],
                'avg_entry_price': float(p.get('avg_entry_price', 0)),
                'current_price': float(p.get('current_price', 0)),
                'value': float(p.get('market_value', 0)),
                'unrealized_pnl_pct': float(p.get('unrealized_plpc', 0)) if p.get('unrealized_plpc') else 0,
            }
            for p in positions
        ],
        'weekly_performance': weekly_perf,
        'recent_trades': [
            {
                'symbol': t['symbol'],
                'pnl_pct': t['pnl_pct'],
                'reason': t['reason_exit'],
                'duration_hours': t['duration_minutes'] / 60 if t['duration_minutes'] else 0,
            }
            for t in recent_closed_trades
        ],
    }


def build_regime_capacity_block(context: Dict) -> str:
    """
    Market-regime + deployment-capacity context (suggestions #2 and #3).

    - Regime: a broad-market downtrend (SPY < SMA50, falling) is where quality-value
      takes its worst losses, so the prompt tells Claude to shrink size / prefer cash.
      Soft guidance, not a hard block — reversible and learning-friendly.
    - Capacity: when gross headroom is ~0 (the book is at/over the 95% cap, as it is
      with 3% cash), there is no room for new buys, so Claude is put in MANAGE-ONLY
      mode and asked to review existing positions instead of forcing trades.
    """
    acct = context.get("account", {})
    equity = float(acct.get("equity", 0) or 0)
    positions = context.get("current_positions", [])
    gross = sum(float(p.get("value", 0) or 0) for p in positions)
    headroom = MAX_GROSS_PCT * equity - gross if equity else 0.0
    deployed_pct = (gross / equity) if equity else 0.0
    manage_only = headroom <= 0

    lines = ["MARKET REGIME & CAPACITY (factor both into today's decision):"]

    # Regime — defensive: any failure just omits the line, never breaks the prompt.
    try:
        from regime import detect_regime
        r = detect_regime("SPY")
        if r is not None:
            vs = f"{r.pct_above_sma50:+.1f}%" if r.pct_above_sma50 is not None else "n/a"
            slope = f"{r.sma50_slope_pct:+.2f}%" if r.sma50_slope_pct is not None else "n/a"
            lines.append(
                f"- Market regime (SPY): {r.label} — price {vs} vs SMA50, 20d slope {slope}."
            )
            if r.is_downtrend:
                lines.append(
                    "  BROAD DOWNTREND ACTIVE: prefer cash or materially smaller size. "
                    "Quality-value's deepest drawdowns are market-wide, not name-specific."
                )
        else:
            lines.append("- Market regime (SPY): unavailable this cycle.")
    except Exception as e:
        lines.append(f"- Market regime (SPY): unavailable ({e}).")

    lines.append(
        f"- Deployment: {deployed_pct:.0%} of equity in {len(positions)} position(s); "
        f"gross cap {MAX_GROSS_PCT:.0%}; remaining headroom for new buys "
        f"${max(headroom, 0):,.0f}."
    )
    if manage_only:
        lines.append(
            "  MANAGE-ONLY MODE: the book is at/over the gross cap — do NOT propose new "
            "buys (they will be hard-blocked at execution). Instead, review existing "
            "positions for thesis-break or over-concentration and return trades=[]."
        )
    return "\n".join(lines)


def build_candidate_block(force_refresh: bool = False, max_candidates: int = MAX_CANDIDATES_FOR_PROMPT) -> str:
    """Run the S&P 500 screen and render top survivors as a prompt-ready string."""
    candidates = screen_sp500(force_refresh=force_refresh, max_candidates=max_candidates)
    if not candidates:
        return "(NO CANDIDATES SURVIVED THE SCREEN — recommend cash this cycle.)"

    lines = [f"({len(candidates)} candidates passed the quality + trend filters, ranked by setup score)\n"]
    for c in candidates:
        lines.append(format_snapshot_for_prompt(c))
        lines.append("")
    return "\n".join(lines)


def build_enhanced_claude_prompt(force_refresh_candidates: bool = False) -> str:
    """Build the full prompt with account context + screened candidates + strategy rules."""
    context = build_rich_context()
    if not context:
        return ""

    account = context['account']
    stats = context['trade_statistics']
    risk = context['portfolio_risk']
    positions = context['current_positions']

    candidate_block = build_candidate_block(force_refresh=force_refresh_candidates)
    regime_capacity_block = build_regime_capacity_block(context)

    prompt = f"""
YOU ARE AN EXPERT QUALITY-VALUE TRADING ADVISOR.

STRATEGY: Concentrated long-only US equity. Quality businesses bought at fair prices
when the trend has stopped going down. Holding periods: weeks to months by default,
or longer when thesis holds and tax efficiency benefits. 3-5 high-conviction positions,
not many mediocre ones.

OBJECTIVE (read carefully — this is a $100k PAPER LEARNING account, not a
return-chasing one): Preserve capital and compound steadily on a RISK-ADJUSTED basis.
The honest, evidence-based bar is to beat a passive SPY+cash blend over months — NOT
to hit any weekly return target. Repeated backtests of THIS EXACT STYLE found no
stock-picking or timing edge over buy-and-hold SPY, so do not force trades to
manufacture alpha. Your value here is disciplined risk management and clear, honest
reasoning. A quiet, mostly-cash book that protects capital is a SUCCESS, not a failure.

EACH CYCLE ALSO: (a) review existing positions for thesis-break or over-concentration
and say so plainly in your analysis, and (b) propose a new BUY only when there is a
genuinely asymmetric setup AND the capacity to size it properly (see capacity below).

Buffett's "wonderful business at a fair price" + Weinstein's "buy the basing knife,
not the falling knife." Cash is a position — forcing trades is the enemy.

CURRENT PORTFOLIO STATUS (as of {datetime.now(timezone.utc).isoformat()}):
- Total Equity: ${account['equity']:,.2f}
- Available Cash: ${account['cash']:,.2f}
- Buying Power: ${account['buying_power']:,.2f}
- Open Positions: {account['num_positions']}

HISTORICAL PERFORMANCE (from trade journal):
- Total Trades: {stats['total_trades']}
- Win Rate: {stats['win_rate']:.1%} ({stats['wins']}W / {stats['losses']}L)
- Avg Winning Trade: {stats['avg_win_pct']:+.2%}
- Avg Losing Trade: {stats['avg_loss_pct']:+.2%}
- Profit Factor: {stats['profit_factor']:.2f}x

CURRENT RISK METRICS:
- Portfolio Heat (% at risk): {risk['portfolio_heat_pct']:.2%}
- Max Drawdown: {risk['max_drawdown_pct']:.2%}
- Annualized Volatility: {risk['volatility_annualized_pct']:.2%}
- Sortino Ratio: {risk['sortino_ratio']:.2f}

CURRENT POSITIONS:
"""

    if positions:
        for pos in positions:
            prompt += (
                f"- {pos['symbol']}: {pos['shares']} shares @ avg ${pos['avg_entry_price']:.2f}  "
                f"(now ${pos['current_price']:.2f}, {pos['unrealized_pnl_pct']:+.2%}, ${pos['value']:,.0f})\n"
            )
    else:
        prompt += "- (none — fully in cash)\n"

    if context['recent_trades']:
        prompt += "\nRECENT CLOSED TRADES:\n"
        for trade in context['recent_trades']:
            emoji = "+" if trade['pnl_pct'] > 0 else "-"
            prompt += f"- [{emoji}] {trade['symbol']}: {trade['pnl_pct']:+.2%} ({trade['reason']}, {trade['duration_hours']:.1f}h)\n"

    prompt += f"""

==============================================================================
{regime_capacity_block}
==============================================================================

SCREENED CANDIDATES (S&P 500, passed hard quality filter + no confirmed downtrend)
==============================================================================

{candidate_block}

==============================================================================
HARD RULES (do not violate)
==============================================================================

1. UNIVERSE & QUALITY
   - Only propose tickers from the screened candidate list above.
   - All listed candidates already passed: market cap >= $2B, positive operating
     cash flow, debt/equity < 300, profit margin > -10%, no confirmed downtrend.
   - Never propose a stock NOT in the candidate list.

2. ENTRY TIMING (TREND CONFIRMATION)
   - PREFER: basing patterns (is_basing=true) in the lower half of 52-week range.
     This is Stage 1 -> Stage 2 transition territory; best risk/reward.
   - ACCEPTABLE: confirmed uptrends not yet extended (range_position_pct < 80,
     not far above SMA50).
   - AVOID: extended late-cycle entries (range_position_pct > 90, +20%+ above SMA50).

3. POSITION SIZING & RISK
   - HARD CAP: 25% of equity per position. Smaller is fine; larger is forbidden.
   - Stop loss: 1.5-2x ATR(14) of the candidate. For quality large-caps with
     ATR around 2-3%, this typically produces stops 4-8% below entry. The
     15% level is a CEILING (Klaas's per-name drawdown tolerance), NOT a
     target — never set a stop at exactly 15% as a default.
   - Stops below 4% are rejected as data anomalies; stops above 15% are
     forbidden (they exceed Klaas's drawdown tolerance).
   - Position size such that worst case (full stop hit) costs 1-2% of total equity.
     Formula: shares = (equity * 0.015) / (entry_price - stop_loss).
     Then verify shares * entry <= equity * 0.25 (the concentration cap).
   - Aim for 3-5 concentrated positions total across the portfolio.

4. HOLDING PERIOD & TAX EFFICIENCY (US)
   - Default holding period: weeks to months. Don't force quick exits.
   - US short-term gains taxed up to ~37%. Prefer holding past the 1-year LTCG
     line when thesis remains intact.
   - For each trade, set holding_period: "swing" (weeks), "position" (months),
     or "LTCG" (intend to hold past 1 year).

5. CONVICTION DISCIPLINE
   - Propose 0-3 trades. NOT MORE. Better fewer high-conviction than many mediocre.
   - If no candidate has a clear, asymmetric setup, return zero trades and
     recommend cash. Cash is a position. Forcing trades is the enemy.
   - Each trade needs: a fundamental reason (the why) AND a technical reason
     (the when). Both, not either.

==============================================================================
RETURN JSON ONLY
==============================================================================
Schema fields per trade:
  symbol (must be from candidate list), direction (BUY/SELL),
  entry_strategy (text), entry_price (number), stop_loss (number),
  stop_loss_pct (decimal, ~0.15), atr_multiple (1.5-2.0 typically),
  target (number), target_gain_pct (decimal), risk_reward_ratio (number),
  conviction (0-100 integer), holding_period (swing/position/LTCG),
  rationale (combine fundamental + technical reasoning),
  exit_plan (when to exit besides target/stop)

If no good setups: return trades=[] with analysis explaining why cash is correct now.
"""

    return prompt


STRATEGY_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {
            "type": "string",
            "description": "Brief market overview, current portfolio assessment, and strategy summary",
        },
        "confidence_target_pct": {
            "type": "integer",
            "description": "Confidence (0-100) that the proposed plan preserves capital and beats a passive SPY+cash blend on a risk-adjusted basis over the coming months",
        },
        "trades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Must be a ticker from the screened candidate list"},
                    "direction": {"type": "string", "enum": ["BUY", "SELL"]},
                    "entry_strategy": {"type": "string", "description": "How/when to enter (e.g. 'limit at $180 or pullback to SMA50')"},
                    "entry_price": {"type": "number"},
                    "stop_loss": {"type": "number"},
                    "stop_loss_pct": {"type": "number", "description": "Stop as decimal fraction below entry, typically ~0.15"},
                    "atr_multiple": {"type": "number", "description": "Stop distance as multiple of ATR(14). Typically 1.5-2.0."},
                    "target": {"type": "number", "description": "Realistic price target — months out, not days"},
                    "target_gain_pct": {"type": "number", "description": "Target gain as decimal fraction"},
                    "risk_reward_ratio": {"type": "number", "description": "target_gain / stop_distance — should be >= 2 for high conviction"},
                    "conviction": {"type": "integer", "description": "0-100"},
                    "holding_period": {
                        "type": "string",
                        "enum": ["swing", "position", "LTCG"],
                        "description": "swing=weeks; position=months; LTCG=intend to hold past 1 year for tax efficiency",
                    },
                    "rationale": {"type": "string", "description": "Combine fundamental (the why) and technical (the when) reasoning"},
                    "exit_plan": {"type": "string", "description": "When to exit besides target/stop — thesis-break conditions, time stops, etc."},
                },
                "required": [
                    "symbol", "direction", "entry_strategy", "entry_price",
                    "stop_loss", "stop_loss_pct", "atr_multiple",
                    "target", "target_gain_pct", "risk_reward_ratio",
                    "conviction", "holding_period", "rationale", "exit_plan",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["analysis", "confidence_target_pct", "trades"],
    "additionalProperties": False,
}


def get_enhanced_strategy(force_refresh_candidates: bool = False) -> Optional[Dict]:
    """Get Claude's trade recommendations with quality-value + trend-confirmed context."""
    from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY not set in .env")
        return None

    print("Building prompt — running S&P 500 screen (cache-aware)...")
    prompt = build_enhanced_claude_prompt(force_refresh_candidates=force_refresh_candidates)

    if not prompt:
        print("Unable to build context")
        return None

    print("Requesting strategy from Claude...")
    client = anthropic.Anthropic()

    text = ""
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            # Adaptive thinking on a 250-candidate stock-picking prompt routinely uses
            # 6-10k tokens before any text output. Budget needs headroom for both.
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": STRATEGY_SCHEMA},
            },
            messages=[{"role": "user", "content": prompt}],
        )

        text = next((b.text for b in response.content if b.type == "text"), "")

        if not text:
            thinking_chars = sum(len(getattr(b, "thinking", "") or "") for b in response.content)
            print(
                f"Claude returned no text (stop_reason={response.stop_reason}, "
                f"thinking_chars={thinking_chars}, output_tokens={response.usage.output_tokens}). "
                "Likely max_tokens too low for adaptive thinking + JSON output."
            )
            return None

        return json.loads(text)

    except anthropic.APIError as e:
        print(f"Claude API error: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        with open('latest_strategy_raw.txt', 'w', encoding='utf-8') as f:
            f.write(text)
        print("   Raw response saved to latest_strategy_raw.txt for debugging")
        return None


def display_strategy(strategy: Dict):
    """Display Claude's recommendations with the new schema fields."""
    print("\n" + "="*70)
    print("AI STRATEGY RECOMMENDATIONS")
    print("="*70)

    print(f"\nMarket Analysis:")
    print(strategy.get('analysis', 'N/A'))

    print(f"\nTarget Confidence: {strategy.get('confidence_target_pct', 0)}%")

    trades = strategy.get('trades', [])
    if not trades:
        print("\nNo trades proposed. Recommendation: hold cash.")
        print("="*70 + "\n")
        return

    print(f"\nTrade Ideas ({len(trades)}):")
    for i, trade in enumerate(trades, 1):
        print(f"\n  Trade {i}: {trade['symbol']} - {trade['direction']}  [{trade['holding_period']}]")
        print(f"    Entry: ${trade['entry_price']:.2f}  ({trade['entry_strategy']})")
        print(f"    Stop:  ${trade['stop_loss']:.2f}  ({trade['stop_loss_pct']:+.2%}, {trade['atr_multiple']:.1f}x ATR)")
        print(f"    Target: ${trade['target']:.2f}  ({trade['target_gain_pct']:+.2%})")
        print(f"    R/R: {trade['risk_reward_ratio']:.2f}  |  Conviction: {trade['conviction']}%")
        print(f"    Rationale: {trade['rationale']}")
        print(f"    Exit plan: {trade['exit_plan']}")

    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    import sys

    # Windows console defaults to cp1252 — Claude routinely emits → ✓ ★ etc.
    # Reconfigure stdout to UTF-8 so display_strategy() doesn't crash on them.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    force = "--refresh" in sys.argv

    strategy = get_enhanced_strategy(force_refresh_candidates=force)

    if strategy:
        # Persist BEFORE displaying so a console-encoding crash can't lose the result.
        with open('latest_strategy.json', 'w', encoding='utf-8') as f:
            json.dump(strategy, f, indent=2)
        print("Strategy saved to latest_strategy.json")
        display_strategy(strategy)
    else:
        print("Failed to get strategy")
