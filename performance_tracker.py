import json
from datetime import datetime, timedelta, timezone
from alpaca_client import AlpacaClient


def calculate_weekly_returns(account_history):
    """Calculate returns over the past week"""
    if len(account_history) < 2:
        return None
    
    oldest = account_history[0]
    newest = account_history[-1]
    
    starting_equity = float(oldest['equity'])
    ending_equity = float(newest['equity'])
    
    weekly_return = ((ending_equity - starting_equity) / starting_equity) * 100
    return {
        'starting_equity': starting_equity,
        'ending_equity': ending_equity,
        'weekly_return_pct': weekly_return,
        'target_return_pct': 1.0,
        'on_track': weekly_return >= 1.0
    }


def get_portfolio_snapshot():
    """Get current account and position data"""
    client = AlpacaClient()
    account = client.get_account()
    positions = client.get_positions()
    
    snapshot = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'account': {
            'equity': float(account['equity']),
            'buying_power': float(account['buying_power']),
            'cash': float(account['cash']),
            'status': account['status']
        },
        'positions': [
            {
                'symbol': p['symbol'],
                'qty': float(p['qty']),
                'avg_entry_price': float(p['avg_entry_price']),
                'current_price': float(p.get('current_price', 0)),
                'unrealized_pl': float(p['unrealized_pl'])
            }
            for p in positions
        ]
    }
    return snapshot


def save_snapshot(snapshot, filename='portfolio_history.json'):
    """Save portfolio snapshot to file for tracking"""
    try:
        with open(filename, 'r') as f:
            history = json.load(f)
    except:
        history = []
    
    history.append(snapshot)
    
    with open(filename, 'w') as f:
        json.dump(history, f, indent=2)
    
    return history


def load_history(filename='portfolio_history.json'):
    """Load portfolio history"""
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except:
        return []


def get_weekly_performance():
    """Get this week's performance"""
    history = load_history()
    
    if not history:
        print("No performance history yet. Run this script daily to track performance.")
        return None
    
    # Filter to last 7 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    weekly_history = [h for h in history if h['timestamp'] >= cutoff]
    
    if len(weekly_history) < 2:
        print(f"Not enough data yet. Have {len(weekly_history)} snapshots, need at least 2.")
        return None
    
    accounts = [h['account'] for h in weekly_history]
    performance = calculate_weekly_returns(accounts)
    
    return {
        'snapshots': len(weekly_history),
        'performance': performance,
        'current_snapshot': weekly_history[-1]
    }


def display_performance_report():
    """Display a formatted performance report"""
    perf = get_weekly_performance()
    
    if not perf:
        print("Not enough data to generate performance report.")
        return
    
    performance = perf['performance']
    snapshot = perf['current_snapshot']
    
    print("\n" + "="*60)
    print("📊 WEEKLY PERFORMANCE REPORT")
    print("="*60)
    print(f"Time: {snapshot['timestamp']}")
    print(f"Snapshots collected: {perf['snapshots']}")
    print()
    
    print("Account Summary:")
    print(f"  Current Equity: ${snapshot['account']['equity']:,.2f}")
    print(f"  Buying Power: ${snapshot['account']['buying_power']:,.2f}")
    print(f"  Cash: ${snapshot['account']['cash']:,.2f}")
    print()
    
    print("Weekly Performance:")
    print(f"  Starting Equity: ${performance['starting_equity']:,.2f}")
    print(f"  Ending Equity: ${performance['ending_equity']:,.2f}")
    print(f"  Weekly Return: {performance['weekly_return_pct']:.2f}%")
    print(f"  Target Return: {performance['target_return_pct']:.2f}%")
    
    if performance['on_track']:
        print(f"  Status: ✅ ON TRACK (beating 1% target)")
    else:
        shortfall = performance['target_return_pct'] - performance['weekly_return_pct']
        print(f"  Status: ⚠️  BELOW TARGET (need {shortfall:.2f}% more)")
    
    print()
    
    positions = snapshot['positions']
    if positions:
        print("Current Positions:")
        total_pl = sum(p['unrealized_pl'] for p in positions)
        for p in positions:
            pl_pct = (p['unrealized_pl'] / (p['qty'] * p['avg_entry_price'])) * 100
            print(f"  {p['symbol']}: {p['qty']} shares @ ${p['avg_entry_price']:.2f}")
            print(f"    Current: ${p['current_price']:.2f}, P&L: ${p['unrealized_pl']:,.2f} ({pl_pct:.2f}%)")
        print(f"  Total Unrealized P&L: ${total_pl:,.2f}")
    else:
        print("No open positions")
    
    print("="*60 + "\n")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "track":
        snapshot = get_portfolio_snapshot()
        history = save_snapshot(snapshot)
        print(f"✅ Portfolio snapshot saved. Total snapshots: {len(history)}")
    else:
        display_performance_report()
