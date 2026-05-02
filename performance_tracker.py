import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from alpaca_client import AlpacaClient


# US tax rates (rough estimates for an active trader at moderate income).
# Adjust if Klaas's effective rate differs materially. These produce a rough
# "tax-drag" estimate, not actual tax owed.
TAX_RATE_SHORT_TERM = 0.32   # ordinary income — varies by bracket; 32% is a moderate-to-high active-trader assumption
TAX_RATE_LTCG = 0.15         # most filers fall in the 15% LTCG bracket
LTCG_THRESHOLD_DAYS = 365


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
    except (OSError, json.JSONDecodeError):
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
    except (OSError, json.JSONDecodeError):
        return []


def get_turnover_stats(period_days: int = 30) -> Dict:
    """
    Compute trade turnover + tax-drag estimates for the trailing period.

    "Tax drag" here = how much MORE tax was owed because gains were short-term
    vs the counterfactual where the same gains were long-term (LTCG). It's a
    rough estimate to surface the cost of frequent trading, not actual tax owed.

    Returns: dict with trades, avg_holding_days, short_term_gains, long_term_gains,
    estimated_tax_owed, tax_drag (vs all-LTCG counterfactual).
    """
    from trade_journal import TradeJournal

    closed = [t for t in TradeJournal.load_trades() if t.get('status') == 'closed']

    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
    recent = []
    for t in closed:
        try:
            exit_dt = datetime.fromisoformat(t['exit_time'])
            if exit_dt.tzinfo is None:
                exit_dt = exit_dt.replace(tzinfo=timezone.utc)
            if exit_dt >= cutoff:
                recent.append(t)
        except (ValueError, TypeError):
            continue

    if not recent:
        return {
            'period_days': period_days,
            'trades': 0,
            'avg_holding_days': 0.0,
            'short_term_gains': 0.0,
            'long_term_gains': 0.0,
            'realized_gain': 0.0,
            'estimated_tax_owed': 0.0,
            'tax_drag': 0.0,
        }

    short_term_gains = 0.0
    long_term_gains = 0.0
    total_pnl = 0.0
    holding_days_list = []

    for t in recent:
        pnl = float(t.get('pnl') or 0)
        total_pnl += pnl

        duration_minutes = float(t.get('duration_minutes') or 0)
        days_held = duration_minutes / (60 * 24)
        holding_days_list.append(days_held)

        if pnl > 0:
            if days_held >= LTCG_THRESHOLD_DAYS:
                long_term_gains += pnl
            else:
                short_term_gains += pnl

    avg_days = sum(holding_days_list) / len(holding_days_list) if holding_days_list else 0

    estimated_tax_owed = short_term_gains * TAX_RATE_SHORT_TERM + long_term_gains * TAX_RATE_LTCG
    counterfactual_all_LTCG = (short_term_gains + long_term_gains) * TAX_RATE_LTCG
    tax_drag = estimated_tax_owed - counterfactual_all_LTCG

    return {
        'period_days': period_days,
        'trades': len(recent),
        'avg_holding_days': avg_days,
        'short_term_gains': short_term_gains,
        'long_term_gains': long_term_gains,
        'realized_gain': total_pnl,
        'estimated_tax_owed': estimated_tax_owed,
        'tax_drag': tax_drag,
    }


def display_turnover_report(period_days: int = 30) -> None:
    stats = get_turnover_stats(period_days)
    print(f"\nTurnover (last {period_days} days):")
    print(f"  Trades closed: {stats['trades']}")
    print(f"  Avg holding period: {stats['avg_holding_days']:.1f} days")
    print(f"  Realized gains (gross): ${stats['realized_gain']:+,.2f}")
    print(f"    Short-term: ${stats['short_term_gains']:+,.2f}")
    print(f"    Long-term:  ${stats['long_term_gains']:+,.2f}")
    print(f"  Estimated tax owed: ${stats['estimated_tax_owed']:,.2f}  "
          f"(@ {TAX_RATE_SHORT_TERM:.0%} short / {TAX_RATE_LTCG:.0%} long)")
    if stats['short_term_gains'] > 0:
        print(f"  Tax DRAG (vs counterfactual all-LTCG): ${stats['tax_drag']:,.2f}")
        print(f"    -> short-term turnover cost ~${stats['tax_drag']:,.0f} more in tax this period")


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

    display_turnover_report(period_days=30)

    print("="*60 + "\n")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "track":
        snapshot = get_portfolio_snapshot()
        history = save_snapshot(snapshot)
        print(f"✅ Portfolio snapshot saved. Total snapshots: {len(history)}")
    else:
        display_performance_report()
