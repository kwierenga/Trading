"""
Position Sizing Module
Calculates optimal position sizes using Kelly Criterion, Fixed Fractional, and Risk-Based methods
"""

import json
import os
from typing import Dict, Tuple
from alpaca_client import AlpacaClient
from performance_tracker import load_history


class PositionSizer:
    """Calculate position sizes based on risk management principles"""
    
    def __init__(self, account_equity: float, win_rate: float = 0.50, 
                 avg_win: float = 0.015, avg_loss: float = 0.010):
        """
        Args:
            account_equity: Total account equity in dollars
            win_rate: Historical win rate (0.0 to 1.0), default 50%
            avg_win: Average winning trade %, default 1.5%
            avg_loss: Average losing trade %, default 1.0%
        """
        self.account_equity = account_equity
        self.win_rate = win_rate
        self.avg_win = avg_win
        self.avg_loss = avg_loss
    
    def kelly_criterion(self, max_risk_pct: float = 0.02) -> Tuple[float, float]:
        """
        Calculate Kelly Criterion optimal position size
        Formula: f* = (p * b - q) / b
        where p = win rate, q = loss rate, b = win/loss ratio
        
        Typically use fractional Kelly (25%) for live trading for safety
        
        Returns:
            (kelly_fraction, suggested_position_size_pct)
        """
        if self.win_rate <= 0 or self.win_rate >= 1:
            return 0.0, 0.0
        
        p = self.win_rate
        q = 1 - self.win_rate
        
        # Win/loss ratio
        b = self.avg_win / self.avg_loss if self.avg_loss > 0 else 1.0
        
        # Full Kelly
        kelly_fraction = (p * b - q) / b
        
        # Clamp to reasonable range and apply fractional Kelly (0.25x)
        kelly_fraction = max(0, min(kelly_fraction, 0.25))  # Max 25% account risk
        
        position_size_pct = kelly_fraction * max_risk_pct
        
        return kelly_fraction, position_size_pct
    
    def fixed_fractional(self, risk_per_trade_pct: float = 0.02) -> float:
        """
        Fixed Fractional: Risk fixed % of account per trade
        Simple and conservative approach
        
        Args:
            risk_per_trade_pct: % of account to risk per trade (default 2%)
        
        Returns:
            position_size_pct: % of account to allocate to this trade
        """
        return min(risk_per_trade_pct, 0.05)  # Max 5% per trade
    
    def volatility_adjusted(self, stock_price: float, atr: float, 
                           risk_per_trade_pct: float = 0.02) -> Tuple[float, float]:
        """
        Volatility-Adjusted: Size based on ATR (Average True Range)
        
        Args:
            stock_price: Current stock price
            atr: Average True Range (volatility measure)
            risk_per_trade_pct: % of account to risk (default 2%)
        
        Returns:
            (position_size_shares, position_size_pct)
        """
        if atr <= 0 or stock_price <= 0:
            return 0, 0
        
        # How much we can afford to lose: account * risk_pct
        max_loss = self.account_equity * risk_per_trade_pct
        
        # Shares = max_loss / ATR (so stop loss ATR shares = max_loss)
        shares = max_loss / atr
        
        # Position value as % of account
        position_value = shares * stock_price
        position_pct = position_value / self.account_equity
        
        # Cap at 10% per position
        if position_pct > 0.10:
            shares = (self.account_equity * 0.10) / stock_price
            position_pct = 0.10
        
        return shares, position_pct
    
    def max_position_size(self, risk_pct: float = 0.02) -> float:
        """
        Maximum safe position size as % of account
        Conservative default: 5% of account max
        """
        return min(risk_pct * 5, 0.05)
    
    def calculate_for_trade(self, symbol: str, current_price: float, 
                           stop_loss_price: float, 
                           method: str = "fixed") -> Dict:
        """
        Calculate recommended position size for a trade
        
        Args:
            symbol: Stock symbol
            current_price: Entry price
            stop_loss_price: Stop loss price
            method: 'kelly', 'fixed', 'volatility'
        
        Returns:
            Dictionary with position sizing details
        """
        risk_per_trade = abs(current_price - stop_loss_price) / current_price
        max_loss_pct = 0.02  # Max 2% loss per trade
        
        if risk_per_trade > max_loss_pct:
            # Stop loss too tight/loose, recommend better risk/reward
            return {
                'symbol': symbol,
                'recommended_shares': 0,
                'position_pct': 0,
                'error': f'Risk {risk_per_trade:.2%} exceeds max {max_loss_pct:.2%}',
                'suggested_stop_loss': current_price * (1 - max_loss_pct)
            }
        
        if method == 'kelly':
            _, position_pct = self.kelly_criterion(max_loss_pct)
        elif method == 'volatility':
            atr = abs(current_price - stop_loss_price) * 1.5  # Estimate ATR
            _, position_pct = self.volatility_adjusted(current_price, atr, max_loss_pct)
        else:  # fixed
            position_pct = self.fixed_fractional(max_loss_pct)
        
        position_value = self.account_equity * position_pct
        shares = int(position_value / current_price)
        
        return {
            'symbol': symbol,
            'recommended_shares': shares,
            'position_value': shares * current_price,
            'position_pct': (shares * current_price) / self.account_equity,
            'entry_price': current_price,
            'stop_loss': stop_loss_price,
            'risk_pct_per_trade': risk_per_trade,
            'method': method
        }


def get_trade_statistics() -> Dict:
    """Load trade history and calculate win rate"""
    try:
        if os.path.exists('trade_journal.json'):
            with open('trade_journal.json', 'r') as f:
                trades = json.load(f)
            
            if not trades:
                return {'win_rate': 0.50, 'avg_win': 0.015, 'avg_loss': 0.010, 'trades': 0}
            
            wins = sum(1 for t in trades if t.get('pnl_pct', 0) > 0)
            total = len([t for t in trades if t.get('status') == 'closed'])
            
            if total == 0:
                return {'win_rate': 0.50, 'avg_win': 0.015, 'avg_loss': 0.010, 'trades': 0}
            
            win_rate = wins / total
            
            winning_trades = [t for t in trades if t.get('pnl_pct', 0) > 0 and t.get('status') == 'closed']
            losing_trades = [t for t in trades if t.get('pnl_pct', 0) <= 0 and t.get('status') == 'closed']
            
            avg_win = sum(t.get('pnl_pct', 0) for t in winning_trades) / len(winning_trades) if winning_trades else 0.015
            avg_loss = abs(sum(t.get('pnl_pct', 0) for t in losing_trades) / len(losing_trades)) if losing_trades else 0.010
            
            return {
                'win_rate': win_rate,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'trades': total,
                'wins': wins,
                'losses': total - wins
            }
    except:
        pass
    
    return {'win_rate': 0.50, 'avg_win': 0.015, 'avg_loss': 0.010, 'trades': 0}


def calculate_position_size(symbol: str, current_price: float, 
                           stop_loss_price: float) -> Dict:
    """
    Main function to calculate position size
    Uses historical trade stats if available
    """
    from config import ALPACA_API_KEY
    
    if not ALPACA_API_KEY:
        print("❌ ALPACA_API_KEY not set in .env")
        return {}
    
    client = AlpacaClient()
    account = client.get_account()
    
    stats = get_trade_statistics()
    
    sizer = PositionSizer(
        account_equity=float(account['equity']),
        win_rate=stats['win_rate'],
        avg_win=stats['avg_win'],
        avg_loss=stats['avg_loss']
    )
    
    recommendation = sizer.calculate_for_trade(symbol, current_price, stop_loss_price)
    
    return recommendation


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 4:
        print("Usage: python position_sizer.py <symbol> <entry_price> <stop_loss_price>")
        sys.exit(1)
    
    symbol = sys.argv[1]
    entry = float(sys.argv[2])
    stop = float(sys.argv[3])
    
    rec = calculate_position_size(symbol, entry, stop)
    
    if 'error' in rec:
        print(f"❌ {rec['error']}")
        print(f"   Suggested stop loss: ${rec['suggested_stop_loss']:.2f}")
    else:
        print(f"\n📊 Position Sizing for {rec['symbol']}")
        print(f"   Entry: ${rec['entry_price']:.2f}")
        print(f"   Stop Loss: ${rec['stop_loss']:.2f}")
        print(f"   Risk: {rec['risk_pct_per_trade']:.2%}")
        print(f"   ---")
        print(f"   Recommended Shares: {rec['recommended_shares']}")
        print(f"   Position Value: ${rec['position_value']:,.2f}")
        print(f"   % of Account: {rec['position_pct']:.2%}")
        print(f"   Method: {rec['method']}")
