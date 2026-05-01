import json
import os
import anthropic
from alpaca_client import AlpacaClient
from config import TRADING_CONFIG

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
CLAUDE_MODEL = os.getenv('CLAUDE_MODEL', 'claude-sonnet-4-6')

SYSTEM_PROMPT = """You are an AI assistant for Alpaca paper trading. The user wants advice on trading in their paper account.

Return only valid JSON with these keys:
- action: buy, sell or hold
- symbol: ticker symbol or empty string if hold
- qty: integer quantity
- side: buy or sell
- order_type: market or limit
- time_in_force: day or gtc
- reason: short explanation for the trade

If no trade should be made, return:
{"action":"hold","symbol":"","qty":0,"side":"","order_type":"","time_in_force":"","reason":"Your explanation"}"""


def claude_complete(user_message, max_tokens=500):
    """Send a message to Claude and return the generated text."""
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


def parse_json_response(raw_text):
    """Extract JSON from the Claude response."""
    raw = raw_text.strip()
    start = raw.find('{')
    end = raw.rfind('}')
    if start == -1 or end == -1:
        raise ValueError('No JSON object found in Claude response')
    raw_json = raw[start:end + 1]
    return json.loads(raw_json)


def build_user_message(account, positions, user_instructions):
    """Build the user message with account state and instructions."""
    positions_text = 'None' if not positions else '\n'.join(
        [f"- {p['symbol']}: qty={p['qty']}, avg_entry={p['avg_entry_price']}, current={p.get('current_price', 'N/A')}, unrealized_pl={p['unrealized_pl']}" for p in positions]
    )

    return f"""Account summary:
- Equity: ${account['equity']}
- Buying Power: ${account['buying_power']}
- Cash: ${account['cash']}
- Account status: {account['status']}
- Positions:
{positions_text}

The user instruction is: {user_instructions}"""


def get_trade_signal(user_instructions):
    """Ask Claude for a trade signal and parse the response."""
    client = AlpacaClient()
    account = client.get_account()
    positions = client.get_positions()

    user_message = build_user_message(account, positions, user_instructions)
    print('Sending prompt to Claude...')
    raw_response = claude_complete(user_message)
    print('Claude response:')
    print(raw_response)

    return parse_json_response(raw_response)


def execute_trade(signal):
    """Submit the trade to Alpaca if the signal indicates a buy or sell."""
    client = AlpacaClient()

    if signal['action'] == 'hold':
        print('No trade executed. Reason:', signal.get('reason', 'No reason provided'))
        return None

    if signal['side'] not in ('buy', 'sell'):
        raise ValueError('Invalid side in trade signal')

    if signal['qty'] <= 0:
        raise ValueError('Quantity must be greater than zero')

    print(f"Placing {signal['side']} order: {signal['qty']} shares of {signal['symbol']} ({signal['order_type']}, {signal['time_in_force']})")
    order = client.submit_order(
        symbol=signal['symbol'],
        qty=signal['qty'],
        side=signal['side'],
        order_type=signal['order_type'] or 'market',
        time_in_force=signal['time_in_force'] or 'day'
    )
    return order


def run_claude_trader():
    print('=== Claude Paper Trading Assistant ===')
    if not ANTHROPIC_API_KEY:
        print('Missing ANTHROPIC_API_KEY in environment.')
        print('Set ANTHROPIC_API_KEY in .env or your shell before running this tool.')
        return

    user_instructions = input('Describe the trade or strategy you want Claude to propose: ').strip()
    if not user_instructions:
        print('No instructions provided. Exiting.')
        return

    try:
        signal = get_trade_signal(user_instructions)
    except Exception as e:
        print('Error generating trade signal:', e)
        return

    print('\n=== Proposed Trade Signal ===')
    print(json.dumps(signal, indent=2))

    if signal['action'] == 'hold':
        print('Claude recommends holding. No trade will be executed.')
        return

    confirm = input('Execute this order? (yes/no): ').strip().lower()
    if confirm not in ('yes', 'y'):
        print('Order not submitted.')
        return

    try:
        order = execute_trade(signal)
        print('Order submitted successfully:')
        print(json.dumps(order, indent=2))
    except Exception as e:
        print('Error executing order:', e)


if __name__ == '__main__':
    run_claude_trader()
