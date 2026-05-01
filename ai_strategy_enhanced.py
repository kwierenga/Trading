"""
Enhanced AI Strategy with Rich Context
Provides Claude with detailed account state, performance history, and risk metrics
"""

import json
import os
import anthropic
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from alpaca_client import AlpacaClient
from performance_tracker import load_history, calculate_weekly_returns
from trade_journal import TradeJournal
from portfolio_metrics import PortfolioMetrics


def build_rich_context() -> Dict:
    """Build comprehensive context for Claude"""
    from config import ALPACA_API_KEY
    
    if not ALPACA_API_KEY:
        return {}
    
    client = AlpacaClient()
    account = client.get_account()
    positions = client.get_positions()
    
    # 1. Account Status
    equity = float(account['equity'])
    buying_power = float(account['buying_power'])
    cash = float(account['cash'])
    
    # 2. Trade Journal Statistics
    trade_stats = TradeJournal.get_statistics()
    
    # 3. Portfolio Metrics
    heat = PortfolioMetrics.calculate_portfolio_heat(positions, equity)
    max_dd = PortfolioMetrics.calculate_max_drawdown()
    volatility = PortfolioMetrics.calculate_volatility()
    sortino = PortfolioMetrics.calculate_sortino_ratio()
    
    # 4. Weekly Performance
    history = load_history()
    weekly_perf = None
    if history and len(history) > 0:
        # Get this week's performance
        accounts = [h['account'] for h in history]
        weekly_perf = calculate_weekly_returns(accounts)
    
    # 5. Recent Trades
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
            'num_positions': len(positions)
        },
        'trade_statistics': {
            'total_trades': trade_stats['total_trades'],
            'win_rate': trade_stats['win_rate'],
            'avg_win_pct': trade_stats['avg_win'],
            'avg_loss_pct': trade_stats['avg_loss'],
            'profit_factor': trade_stats['profit_factor'],
            'wins': trade_stats['wins'],
            'losses': trade_stats['losses']
        },
        'portfolio_risk': {
            'portfolio_heat_pct': heat['total_heat_pct'],
            'max_drawdown_pct': max_dd,
            'volatility_annualized_pct': volatility,
            'sortino_ratio': sortino
        },
        'current_positions': [
            {
                'symbol': p['symbol'],
                'shares': p['qty'],
                'value': float(p.get('market_value', 0)),
                'unrealized_pnl_pct': float(p.get('unrealized_plpc', 0)) if p.get('unrealized_plpc') else 0
            }
            for p in positions
        ],
        'weekly_performance': weekly_perf,
        'recent_trades': [
            {
                'symbol': t['symbol'],
                'pnl_pct': t['pnl_pct'],
                'reason': t['reason_exit'],
                'duration_hours': t['duration_minutes'] / 60 if t['duration_minutes'] else 0
            }
            for t in recent_closed_trades
        ]
    }


def build_enhanced_claude_prompt() -> str:
    """Build rich prompt for Claude with full context"""
    context = build_rich_context()
    
    if not context:
        return ""
    
    account = context['account']
    stats = context['trade_statistics']
    risk = context['portfolio_risk']
    positions = context['current_positions']
    
    prompt = f"""
YOU ARE AN EXPERT QUANTITATIVE TRADING ADVISOR.

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
            prompt += f"- {pos['symbol']}: {pos['shares']} shares (${pos['value']:,.0f}, {pos['unrealized_pnl_pct']:+.2%})\n"
    else:
        prompt += "- (none - fully in cash)\n"
    
    if context['recent_trades']:
        prompt += "\nRECENT TRADE OUTCOMES:\n"
        for trade in context['recent_trades']:
            emoji = "✅" if trade['pnl_pct'] > 0 else "❌"
            prompt += f"- {emoji} {trade['symbol']}: {trade['pnl_pct']:+.2%} ({trade['reason']}, {trade['duration_hours']:.1f}h)\n"
    
    prompt += """
CRITICAL CONSTRAINTS FOR THIS WEEK:
1. GOAL: Generate +1.0% in account equity this week with 70% confidence
   - This means some weeks will miss or be flat (30% of the time is acceptable)
   - Better to be consistent and realistic than chase impossible returns
   
2. RISK MANAGEMENT:
   - Maximum 2% loss per individual trade (stop loss)
   - Maximum 5% of account per single position
   - Maximum 5% of account total portfolio heat
   
3. TRADE QUALITY:
   - Prefer 1 high-conviction trade over 3 mediocre ones
   - Each trade should have clear entry/exit logic
   - Support/resistance, trend following, or mean reversion only
   
4. POSITION SIZING:
   - Use the position sizes I'll calculate based on risk
   - Stock price * shares should not exceed 5% of account
   
5. TIME FRAME:
   - Focus on trades that can complete THIS WEEK
   - Prefer 1-5 day holds over day trades
   
ANALYSIS REQUESTED:
For this week, provide your top 2-3 trade recommendations with:

For each trade:
1. Symbol: Which stock to trade
2. Direction: BUY or SELL
3. Entry Strategy: Where to buy/sell (price level or technical condition)
4. Stop Loss: Price to cut losses (must not exceed 2% loss)
5. Target: Take profit level (realistic 1-2% gain)
6. Conviction: Your confidence this trade hits 1%+ (percentage 0-100%)
7. Rationale: Brief explanation of why this trade makes sense given current market conditions
8. Risk/Reward: Ratio and reasoning

RESPONSE FORMAT - RETURN JSON ONLY:
{
  "analysis": "Brief market overview and strategy summary",
  "confidence_target_pct": 75,
  "trades": [
    {
      "symbol": "AAPL",
      "direction": "BUY",
      "entry_strategy": "Buy on support at $180 or pull back to 20-day MA",
      "entry_price": 180.50,
      "stop_loss": 177.50,
      "target": 182.50,
      "stop_loss_risk_pct": 0.015,
      "target_gain_pct": 0.011,
      "conviction": 72,
      "rationale": "Technical reasons for entry",
      "risk_reward_ratio": 0.73
    }
  ]
}

Remember: Consistency beats greed. Hit 1% four out of six weeks and you've achieved the 70% confidence goal. Missing one week is fine."""
    
    return prompt


STRATEGY_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string", "description": "Brief market overview and strategy summary"},
        "confidence_target_pct": {"type": "integer", "description": "Confidence (0-100) of hitting the 1% weekly target"},
        "trades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "direction": {"type": "string", "enum": ["BUY", "SELL"]},
                    "entry_strategy": {"type": "string"},
                    "entry_price": {"type": "number"},
                    "stop_loss": {"type": "number"},
                    "target": {"type": "number"},
                    "stop_loss_risk_pct": {"type": "number", "description": "Stop loss as a decimal fraction (e.g. 0.02 for 2%). Must be in [0, 0.05]."},
                    "target_gain_pct": {"type": "number", "description": "Target gain as a decimal fraction (e.g. 0.015 for 1.5%). Must be in [0, 0.10]."},
                    "conviction": {"type": "integer"},
                    "rationale": {"type": "string"},
                    "risk_reward_ratio": {"type": "number"},
                },
                "required": [
                    "symbol", "direction", "entry_strategy", "entry_price",
                    "stop_loss", "target", "stop_loss_risk_pct", "target_gain_pct",
                    "conviction", "rationale", "risk_reward_ratio",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["analysis", "confidence_target_pct", "trades"],
    "additionalProperties": False,
}


def get_enhanced_strategy() -> Optional[Dict]:
    """Get Claude's trade recommendations with rich context"""
    from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

    if not ANTHROPIC_API_KEY:
        print("❌ ANTHROPIC_API_KEY not set in .env")
        return None

    prompt = build_enhanced_claude_prompt()

    if not prompt:
        print("❌ Unable to build context")
        return None

    print("🤖 Requesting strategy from Claude...")

    client = anthropic.Anthropic()

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": STRATEGY_SCHEMA},
            },
            messages=[{"role": "user", "content": prompt}],
        )

        text = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(text)

    except anthropic.APIError as e:
        print(f"❌ Claude API error: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ JSON parse error: {e}")
        with open('latest_strategy_raw.txt', 'w', encoding='utf-8') as f:
            f.write(text)
        print("   Raw response saved to latest_strategy_raw.txt for debugging")
        return None


def display_strategy(strategy: Dict):
    """Display Claude's recommendations"""
    print("\n" + "="*70)
    print("🎯 AI STRATEGY RECOMMENDATIONS")
    print("="*70)
    
    print(f"\n📊 Market Analysis:")
    print(strategy.get('analysis', 'N/A'))
    
    print(f"\n🎯 Target Confidence: {strategy.get('confidence_target_pct', 0)}%")
    
    trades = strategy.get('trades', [])
    print(f"\n📋 Trade Ideas ({len(trades)} recommendations):")
    
    for i, trade in enumerate(trades, 1):
        print(f"\n  Trade {i}: {trade['symbol']} - {trade['direction']}")
        print(f"    Entry Strategy: {trade['entry_strategy']}")
        print(f"    Entry: ${trade['entry_price']:.2f}")
        print(f"    Stop Loss: ${trade['stop_loss']:.2f} ({trade['stop_loss_risk_pct']:+.2%})")
        print(f"    Target: ${trade['target']:.2f} ({trade['target_gain_pct']:+.2%})")
        print(f"    Risk/Reward: {trade['risk_reward_ratio']:.2f}")
        print(f"    Conviction: {trade['conviction']}%")
        print(f"    Rationale: {trade['rationale']}")
    
    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    strategy = get_enhanced_strategy()
    
    if strategy:
        display_strategy(strategy)
        
        # Save for reference
        with open('latest_strategy.json', 'w') as f:
            json.dump(strategy, f, indent=2)
        print("✅ Strategy saved to latest_strategy.json")
    else:
        print("❌ Failed to get strategy")
