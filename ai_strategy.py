import json
import os
import anthropic
from alpaca_client import AlpacaClient
from performance_tracker import get_portfolio_snapshot, load_history, calculate_weekly_returns

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
CLAUDE_MODEL = os.getenv('CLAUDE_MODEL', 'claude-sonnet-4-6')

SYSTEM_PROMPT = """You are an AI portfolio manager for a paper trading account.

CRITICAL CONSTRAINTS:
- Goal: 1% weekly growth with 70% confidence (4-5 hits per 6 weeks, some weeks can miss)
- Avoid chasing unrealistic returns
- Prioritize consistency and risk management over aggressive growth
- It's OK to have flat or slightly negative weeks

IMPORTANT: It's better to suggest 1 high-conviction trade than 3 mediocre ones. Only trade when you see clear opportunity."""


def claude_complete(user_message, max_tokens=1000):
    """Send a message to Claude"""
    if not ANTHROPIC_API_KEY:
        raise ValueError('Missing ANTHROPIC_API_KEY in environment')

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return next((b.text for b in response.content if b.type == "text"), "").strip()


def build_strategy_message(account_snapshot, weekly_performance):
    """Build the user message for Claude with current account state"""

    positions_text = 'None' if not account_snapshot['positions'] else '\n'.join(
        [f"- {p['symbol']}: {p['qty']} shares, entry ${p['avg_entry_price']:.2f}, current ${p['current_price']:.2f}, P&L ${p['unrealized_pl']:.2f}"
         for p in account_snapshot['positions']]
    )

    performance_text = 'No performance data yet' if not weekly_performance else (
        f"Weekly return: {weekly_performance['weekly_return_pct']:.2f}% "
        f"(target: {weekly_performance['target_return_pct']:.2f}%)"
    )

    return f"""Current Account Status:
- Equity: ${account_snapshot['account']['equity']:,.2f}
- Buying Power: ${account_snapshot['account']['buying_power']:,.2f}
- Cash: ${account_snapshot['account']['cash']:,.2f}

Current Positions:
{positions_text}

Weekly Performance:
{performance_text}

Suggest 2-3 specific trades for THIS WEEK to target +1% growth. For each strategy:
1. Specific stocks to buy/sell
2. Position sizing (% of portfolio)
3. Entry/exit levels
4. Stop loss (limit downside to 0.3-0.5% of account)
5. Expected timeframe (day/week/month)
6. Realistic return estimate"""


def get_ai_strategy_recommendation():
    """Get trading strategy recommendations from Claude"""
    snapshot = get_portfolio_snapshot()
    history = load_history()

    weekly_perf = None
    if len(history) >= 2:
        accounts = [h['account'] for h in history[-7:]]
        if len(accounts) >= 2:
            weekly_perf = calculate_weekly_returns(accounts)

    user_message = build_strategy_message(snapshot, weekly_perf)
    print("Consulting Claude AI for strategy recommendations...\n")

    return claude_complete(user_message)


def display_strategy_recommendation():
    """Display Claude's strategy recommendation"""
    try:
        recommendation = get_ai_strategy_recommendation()

        print("\n" + "="*70)
        print("🤖 CLAUDE AI STRATEGY RECOMMENDATION")
        print("="*70)
        print(recommendation)
        print("="*70 + "\n")

        return recommendation
    except Exception as e:
        print(f"Error getting strategy recommendation: {e}")
        if "ANTHROPIC_API_KEY" in str(e):
            print("\nTo enable Claude AI strategies:")
            print("1. Get your API key from https://console.anthropic.com/settings/keys")
            print("2. Add to .env: ANTHROPIC_API_KEY=sk-ant-your_key_here")
        return None


if __name__ == "__main__":
    display_strategy_recommendation()
