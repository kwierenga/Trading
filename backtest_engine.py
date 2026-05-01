"""
Backtesting Engine
Test trading strategies on historical market data with realistic performance metrics
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Callable, Tuple
import statistics


class BacktestEngine:
    """Backtest trading strategies on historical data"""
    
    def __init__(self, starting_equity: float = 100000, 
                 slippage_pct: float = 0.001, commission_pct: float = 0.001):
        """
        Args:
            starting_equity: Starting account equity
            slippage_pct: % slippage on entries/exits (default 0.1%)
            commission_pct: % commission per trade (default 0.1%)
        """
        self.starting_equity = starting_equity
        self.slippage_pct = slippage_pct
        self.commission_pct = commission_pct
    
    def simulate_strategy(self, historical_data: List[Dict], 
                         strategy_func: Callable) -> Dict:
        """
        Simulate strategy on historical data
        
        Args:
            historical_data: List of OHLCV bars with timestamp
            strategy_func: Function(bar, positions, equity) -> list of trade signals
        
        Returns:
            Backtest results with P&L, stats, trade list
        """
        equity = self.starting_equity
        positions = {}  # symbol -> {entry_price, shares, entry_bar}
        trades_closed = []
        portfolio_values = [equity]
        dates = []
        
        for bar_idx, bar in enumerate(historical_data):
            # Get signals from strategy
            signals = strategy_func(bar, positions, equity)
            
            # Process BUY signals
            for signal in signals:
                if signal.get('type') == 'buy':
                    symbol = signal['symbol']
                    
                    if symbol in positions:
                        continue  # Already in position
                    
                    entry_price = bar['close'] * (1 + self.slippage_pct)
                    target_price = signal.get('target', entry_price * 1.02)
                    stop_price = signal.get('stop', entry_price * 0.98)
                    shares = signal.get('shares', 1)
                    
                    position_value = shares * entry_price
                    commission = position_value * self.commission_pct
                    
                    if position_value + commission <= equity:
                        equity -= (position_value + commission)
                        positions[symbol] = {
                            'entry_price': entry_price,
                            'entry_bar': bar_idx,
                            'shares': shares,
                            'target': target_price,
                            'stop': stop_price,
                            'entry_time': bar.get('timestamp')
                        }
            
            # Check for exits (stop loss, target hit, or signals)
            symbols_to_close = []
            for symbol, pos in positions.items():
                exit_price = None
                reason = None
                
                # Check if any data for this symbol in this bar
                if 'high' in bar and 'low' in bar and 'close' in bar:
                    # Stop loss hit
                    if bar['low'] <= pos['stop']:
                        exit_price = pos['stop'] * (1 - self.slippage_pct)
                        reason = 'stop_loss'
                    
                    # Target hit
                    elif bar['high'] >= pos['target']:
                        exit_price = pos['target'] * (1 - self.slippage_pct)
                        reason = 'target_hit'
                
                # Manual exit signal
                exit_signal = next((s for s in signals if s.get('type') == 'sell' 
                                   and s['symbol'] == symbol), None)
                if exit_signal:
                    exit_price = bar['close'] * (1 - self.slippage_pct)
                    reason = 'manual'
                
                if exit_price:
                    position_value = pos['shares'] * exit_price
                    commission = position_value * self.commission_pct
                    equity += (position_value - commission)
                    
                    pnl = (exit_price - pos['entry_price']) * pos['shares']
                    pnl_pct = (exit_price - pos['entry_price']) / pos['entry_price']
                    
                    trades_closed.append({
                        'symbol': symbol,
                        'entry_price': pos['entry_price'],
                        'exit_price': exit_price,
                        'shares': pos['shares'],
                        'entry_bar': pos['entry_bar'],
                        'exit_bar': bar_idx,
                        'pnl': pnl,
                        'pnl_pct': pnl_pct,
                        'reason': reason,
                        'duration_bars': bar_idx - pos['entry_bar']
                    })
                    
                    symbols_to_close.append(symbol)
            
            for symbol in symbols_to_close:
                del positions[symbol]
            
            portfolio_values.append(equity)
            dates.append(bar.get('timestamp', str(bar_idx)))
        
        # Close any remaining open positions at last price
        if positions:
            last_close = historical_data[-1]['close']
            for symbol, pos in positions.items():
                position_value = pos['shares'] * last_close
                equity += position_value
                
                pnl = (last_close - pos['entry_price']) * pos['shares']
                pnl_pct = (last_close - pos['entry_price']) / pos['entry_price']
                
                trades_closed.append({
                    'symbol': symbol,
                    'entry_price': pos['entry_price'],
                    'exit_price': last_close,
                    'shares': pos['shares'],
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'reason': 'end_of_backtest'
                })
        
        # Calculate statistics
        stats = self._calculate_stats(trades_closed, portfolio_values)
        
        return {
            'starting_equity': self.starting_equity,
            'ending_equity': equity,
            'total_return': (equity - self.starting_equity) / self.starting_equity,
            'trades': trades_closed,
            'portfolio_values': portfolio_values,
            'stats': stats
        }
    
    def _calculate_stats(self, trades: List[Dict], portfolio_values: List[float]) -> Dict:
        """Calculate backtest statistics"""
        if not trades:
            return {'total_trades': 0}
        
        winners = [t for t in trades if t['pnl'] > 0]
        losers = [t for t in trades if t['pnl'] <= 0]
        
        win_rate = len(winners) / len(trades) if trades else 0
        avg_win = sum(t['pnl_pct'] for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t['pnl_pct'] for t in losers) / len(losers) if losers else 0
        
        # Profit Factor
        gross_profit = sum(t['pnl'] for t in winners) if winners else 0
        gross_loss = abs(sum(t['pnl'] for t in losers)) if losers else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Drawdown
        max_equity = portfolio_values[0]
        max_dd = 0
        for val in portfolio_values:
            if val > max_equity:
                max_equity = val
            dd = (max_equity - val) / max_equity if max_equity > 0 else 0
            if dd > max_dd:
                max_dd = dd
        
        # Volatility
        returns = []
        for i in range(1, len(portfolio_values)):
            if portfolio_values[i-1] > 0:
                ret = (portfolio_values[i] - portfolio_values[i-1]) / portfolio_values[i-1]
                returns.append(ret)
        
        volatility = statistics.stdev(returns) if len(returns) > 1 else 0
        sharpe = (sum(returns) / len(returns) / volatility * (252 ** 0.5)) if volatility > 0 and returns else 0
        
        return {
            'total_trades': len(trades),
            'wins': len(winners),
            'losses': len(losers),
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_drawdown': max_dd,
            'sharpe_ratio': sharpe,
            'total_return': (portfolio_values[-1] - portfolio_values[0]) / portfolio_values[0] if portfolio_values else 0
        }


def load_market_data(filename: str = 'market_data.json') -> List[Dict]:
    """Load historical market data from file"""
    if not os.path.exists(filename):
        print(f"❌ Market data file not found: {filename}")
        return []
    
    try:
        with open(filename, 'r') as f:
            data = json.load(f)
        
        # Flatten if needed (from Alpaca format)
        bars = []
        if isinstance(data, dict):
            for symbol, ohlcv in data.items():
                for bar in ohlcv:
                    bar['symbol'] = symbol
                    bars.append(bar)
        else:
            bars = data
        
        return sorted(bars, key=lambda x: x.get('timestamp', ''))
    except Exception as e:
        print(f"❌ Error loading data: {e}")
        return []


def display_backtest_results(results: Dict):
    """Display backtest results"""
    print("\n" + "="*70)
    print("🔍 BACKTEST RESULTS")
    print("="*70)
    
    print(f"\n💰 Returns:")
    print(f"  Starting Equity: ${results['starting_equity']:,.2f}")
    print(f"  Ending Equity: ${results['ending_equity']:,.2f}")
    print(f"  Total Return: {results['total_return']:+.2%}")
    
    stats = results['stats']
    print(f"\n📊 Trade Statistics:")
    print(f"  Total Trades: {stats['total_trades']}")
    print(f"  Winners: {stats['wins']} | Losers: {stats['losses']}")
    print(f"  Win Rate: {stats['win_rate']:.1%}")
    print(f"  Avg Winner: {stats['avg_win']:+.2%} | Avg Loser: {stats['avg_loss']:+.2%}")
    print(f"  Profit Factor: {stats['profit_factor']:.2f}x")
    
    print(f"\n📈 Risk Metrics:")
    print(f"  Max Drawdown: {stats['max_drawdown']:.2%}")
    print(f"  Sharpe Ratio: {stats['sharpe_ratio']:.2f}")
    
    if results['trades']:
        print(f"\n📋 Last 5 Trades:")
        for trade in results['trades'][-5:]:
            emoji = "✅" if trade['pnl'] > 0 else "❌"
            print(f"  {emoji} {trade['symbol']}: {trade['pnl_pct']:+.2%} ({trade['reason']})")
    
    print("="*70 + "\n")


if __name__ == "__main__":
    # Example backtesting
    print("💡 Backtesting engine loaded. Use with custom strategy functions.")
    print("   Example: engine.simulate_strategy(historical_data, my_strategy_func)")
