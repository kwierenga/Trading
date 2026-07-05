"""
Trade Journal Module
Log and analyze every trade with entry/exit, P&L, win rate, and performance metrics.

Includes US LTCG awareness: tracks days held per open trade and flags positions
approaching the 1-year long-term capital gains threshold (so we don't sell at day 364).
"""

import json
import os
from datetime import datetime, timezone
from typing import List, Dict, Optional
import statistics


# US tax: gains on positions held >= 365 days are taxed at LTCG rates
# (much lower than short-term gains, which are taxed as ordinary income up to ~37%).
LTCG_THRESHOLD_DAYS = 365
LTCG_APPROACHING_WINDOW_DAYS = 30  # flag positions within 30 days of LTCG


class TradeJournal:
    """Maintain detailed trade log with analytics"""

    FILE = 'trade_journal.json'
    
    @staticmethod
    def load_trades() -> List[Dict]:
        """Load all trades from journal"""
        if not os.path.exists(TradeJournal.FILE):
            return []

        try:
            with open(TradeJournal.FILE, 'r') as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return []

    # ----- LTCG / holding-period helpers (US tax) -----

    @staticmethod
    def days_held(entry_time_iso: str) -> int:
        """Days elapsed since entry. UTC-aware."""
        entry = datetime.fromisoformat(entry_time_iso)
        if entry.tzinfo is None:
            entry = entry.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - entry).days

    @staticmethod
    def holding_period_status(days: int) -> Dict:
        """
        Classify a holding period for US LTCG purposes.
        Returns dict with status, days_held, days_to_LTCG, and a short message.

        IRS rule (Pub 550): long-term requires holding MORE than one year —
        the period starts the day after acquisition, so a sale on the one-year
        anniversary (days == 365) is still short-term. Hence strict >.
        """
        days_remaining = max(0, (LTCG_THRESHOLD_DAYS + 1) - days)

        if days > LTCG_THRESHOLD_DAYS:
            return {
                'status': 'LTCG',
                'days_held': days,
                'days_to_LTCG': 0,
                'message': 'LTCG eligible — long-term capital gains rate applies',
            }

        if days_remaining <= LTCG_APPROACHING_WINDOW_DAYS:
            return {
                'status': 'approaching_LTCG',
                'days_held': days,
                'days_to_LTCG': days_remaining,
                'message': f'{days_remaining} days to LTCG — consider holding through threshold to save tax',
            }

        return {
            'status': 'short_term',
            'days_held': days,
            'days_to_LTCG': days_remaining,
            'message': f'short-term gains ({days_remaining} days to LTCG)',
        }

    @staticmethod
    def get_open_trades_with_holding_status() -> List[Dict]:
        """Open trades enriched with days_held and LTCG status. Used by morning routine."""
        trades = TradeJournal.load_trades()
        open_trades = [t for t in trades if t.get('status') == 'open']

        for t in open_trades:
            days = TradeJournal.days_held(t['entry_time'])
            t['holding_status'] = TradeJournal.holding_period_status(days)

        return open_trades
    
    @staticmethod
    def save_trades(trades: List[Dict]):
        """Save trades to journal"""
        with open(TradeJournal.FILE, 'w') as f:
            json.dump(trades, f, indent=2, default=str)
    
    @staticmethod
    def log_entry(symbol: str, shares: int, entry_price: float,
                  stop_loss: float, target_price: float = None,
                  rationale: str = "", source: str = "manual") -> Dict:
        """
        Log a trade entry

        Args:
            symbol: Stock symbol
            shares: Number of shares
            entry_price: Entry price
            stop_loss: Stop loss price (initial — preserved as `initial_stop`
                even when position_manager raises the live stop later)
            target_price: Take profit target (optional)
            rationale: Why this trade (source: AI, support/resistance, etc)
            source: 'claude', 'manual', 'scheduled', 'strategy'

        Returns:
            Trade record
        """
        trades = TradeJournal.load_trades()

        # R = entry - initial stop. The position manager scales out at +1R
        # and +2R unrealized, then trails. R is computed once at entry time
        # and frozen so subsequent stop raises don't change the +1R/+2R levels.
        r_value = max(0.0, entry_price - stop_loss) if (stop_loss and entry_price) else 0.0

        trade = {
            'id': len(trades) + 1,
            'symbol': symbol,
            'shares': shares,
            'original_shares': shares,
            'entry_price': entry_price,
            'entry_time': datetime.now(timezone.utc).isoformat(),
            'stop_loss': stop_loss,
            'initial_stop': stop_loss,
            'R': r_value,
            'current_stop': stop_loss,
            'target_price': target_price,
            'rationale': rationale,
            'source': source,
            'status': 'open',
            'scaled_25_at_1r': False,
            'scaled_25_at_2r': False,
            'exit_price': None,
            'exit_time': None,
            'pnl': None,
            'pnl_pct': None,
            'duration_minutes': None,
            'reason_exit': None
        }

        trades.append(trade)
        TradeJournal.save_trades(trades)

        return trade
    
    @staticmethod
    def log_exit(trade_id: int, exit_price: float, reason: str = "manual") -> Optional[Dict]:
        """
        Log a trade exit
        
        Args:
            trade_id: Trade ID to close
            exit_price: Exit price
            reason: 'target_hit', 'stop_loss', 'manual', 'timeout'
        
        Returns:
            Updated trade record
        """
        trades = TradeJournal.load_trades()
        
        for trade in trades:
            if trade['id'] == trade_id:
                trade['exit_price'] = exit_price
                trade['exit_time'] = datetime.now(timezone.utc).isoformat()
                trade['status'] = 'closed'
                trade['reason_exit'] = reason
                
                # Calculate P&L
                trade['pnl'] = (exit_price - trade['entry_price']) * trade['shares']
                trade['pnl_pct'] = (exit_price - trade['entry_price']) / trade['entry_price']
                
                # Duration
                entry = datetime.fromisoformat(trade['entry_time'])
                exit_dt = datetime.fromisoformat(trade['exit_time'])
                trade['duration_minutes'] = (exit_dt - entry).total_seconds() / 60
                
                TradeJournal.save_trades(trades)
                return trade
        
        return None
    
    @staticmethod
    def get_statistics() -> Dict:
        """Calculate trade statistics"""
        trades = TradeJournal.load_trades()
        closed_trades = [t for t in trades if t['status'] == 'closed']
        open_trades = [t for t in trades if t['status'] == 'open']
        
        if not closed_trades:
            return {
                'total_trades': len(trades),
                'open_trades': len(open_trades),
                'closed_trades': 0,
                'win_rate': 0,
                'wins': 0,
                'losses': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'total_pnl': 0,
                'total_pnl_pct': 0,
                'sharpe_ratio': 0,
                'max_drawdown': 0
            }
        
        winning_trades = [t for t in closed_trades if t['pnl_pct'] > 0]
        losing_trades = [t for t in closed_trades if t['pnl_pct'] <= 0]
        
        wins = len(winning_trades)
        losses = len(losing_trades)
        win_rate = wins / len(closed_trades) if closed_trades else 0
        
        total_pnl = sum(t['pnl'] for t in closed_trades)
        total_pnl_pct = sum(t['pnl_pct'] for t in closed_trades)
        
        avg_win = sum(t['pnl_pct'] for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t['pnl_pct'] for t in losing_trades) / len(losing_trades) if losing_trades else 0
        
        # Profit Factor: gross profit / gross loss
        gross_profit = sum(t['pnl'] for t in winning_trades) if winning_trades else 0
        gross_loss = abs(sum(t['pnl'] for t in losing_trades)) if losing_trades else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Sharpe Ratio (simplified: using returns)
        returns = [t['pnl_pct'] for t in closed_trades]
        if len(returns) > 1:
            mean_return = statistics.mean(returns)
            std_dev = statistics.stdev(returns) if len(returns) > 1 else 0
            sharpe = (mean_return / std_dev * ((252 * 5) ** 0.5)) if std_dev > 0 else 0
        else:
            sharpe = 0
        
        # Max Drawdown (simplified)
        cumulative_pnl = 0
        peak_pnl = 0
        max_dd = 0
        for t in closed_trades:
            cumulative_pnl += t['pnl_pct']
            if cumulative_pnl > peak_pnl:
                peak_pnl = cumulative_pnl
            drawdown = peak_pnl - cumulative_pnl
            if drawdown > max_dd:
                max_dd = drawdown
        
        return {
            'total_trades': len(trades),
            'open_trades': len(open_trades),
            'closed_trades': len(closed_trades),
            'win_rate': win_rate,
            'wins': wins,
            'losses': losses,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'total_pnl': total_pnl,
            'total_pnl_pct': total_pnl_pct,
            'sharpe_ratio': sharpe,
            'max_drawdown': max_dd
        }
    
    @staticmethod
    def display_summary():
        """Display trade journal summary"""
        stats = TradeJournal.get_statistics()
        trades = TradeJournal.load_trades()
        recent_trades = sorted([t for t in trades if t['status'] == 'closed'], 
                              key=lambda x: x['exit_time'])[-5:]
        
        print("\n" + "="*70)
        print("📊 TRADE JOURNAL SUMMARY")
        print("="*70)
        
        print(f"\n📈 Performance:")
        print(f"  Total Trades: {stats['total_trades']} (Open: {stats['open_trades']}, Closed: {stats['closed_trades']})")
        print(f"  Win Rate: {stats['win_rate']:.1%} ({stats['wins']}W / {stats['losses']}L)")
        print(f"  Avg Win: {stats['avg_win']:+.2%} | Avg Loss: {stats['avg_loss']:+.2%}")
        print(f"  Profit Factor: {stats['profit_factor']:.2f}x")
        print(f"  Total P&L: {stats['total_pnl_pct']:+.2%} (${stats['total_pnl']:+,.2f})")
        print(f"  Sharpe Ratio: {stats['sharpe_ratio']:.2f}")
        print(f"  Max Drawdown: {stats['max_drawdown']:.2%}")
        
        if recent_trades:
            print(f"\n📋 Recent Trades (Last 5):")
            for t in recent_trades:
                emoji = "✅" if t['pnl_pct'] > 0 else "❌"
                duration_h = t['duration_minutes'] / 60 if t['duration_minutes'] else 0
                print(f"  {emoji} {t['symbol']}: {t['pnl_pct']:+.2%} ({t['reason_exit']}) - {duration_h:.1f}h")
        
        open_trades = [t for t in trades if t['status'] == 'open']
        if open_trades:
            print(f"\nOpen Positions: {len(open_trades)}")
            for t in open_trades:
                days = TradeJournal.days_held(t['entry_time'])
                hold = TradeJournal.holding_period_status(days)
                marker = ""
                if hold['status'] == 'LTCG':
                    marker = "  [LTCG eligible]"
                elif hold['status'] == 'approaching_LTCG':
                    marker = f"  [approaching LTCG: {hold['days_to_LTCG']}d]"
                print(f"  {t['symbol']}: {t['shares']} @ ${t['entry_price']:.2f} (SL: ${t['stop_loss']:.2f})  held {days}d{marker}")

        print("="*70 + "\n")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        TradeJournal.display_summary()
        sys.exit(0)
    
    command = sys.argv[1]
    
    if command == "entry":
        # python trade_journal.py entry AAPL 10 180.00 175.00 190.00 "Support bounce" claude
        if len(sys.argv) < 8:
            print("Usage: trade_journal.py entry <symbol> <shares> <entry> <stop> <target> <rationale> <source>")
            sys.exit(1)
        
        trade = TradeJournal.log_entry(
            sys.argv[2],
            int(sys.argv[3]),
            float(sys.argv[4]),
            float(sys.argv[5]),
            float(sys.argv[6]),
            sys.argv[7],
            sys.argv[8] if len(sys.argv) > 8 else "manual"
        )
        print(f"✅ Trade logged: #{trade['id']} {trade['symbol']}")
    
    elif command == "exit":
        # python trade_journal.py exit 1 185.00 target_hit
        if len(sys.argv) < 4:
            print("Usage: trade_journal.py exit <trade_id> <exit_price> <reason>")
            sys.exit(1)
        
        result = TradeJournal.log_exit(
            int(sys.argv[2]),
            float(sys.argv[3]),
            sys.argv[4] if len(sys.argv) > 4 else "manual"
        )
        
        if result:
            print(f"✅ Trade closed: #{result['id']} {result['symbol']} - P&L: {result['pnl_pct']:+.2%}")
        else:
            print("❌ Trade not found")
    
    elif command == "stats":
        TradeJournal.display_summary()
