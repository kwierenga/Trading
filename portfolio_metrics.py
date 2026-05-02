"""
Portfolio Metrics Module
Calculate portfolio heat, drawdown, diversification, and risk exposure
"""

import json
import os
import statistics as _statistics_module
from typing import Dict, List, Tuple
from datetime import datetime
from alpaca_client import AlpacaClient


class PortfolioMetrics:
    """Analyze portfolio risk and performance metrics"""
    
    @staticmethod
    def calculate_portfolio_heat(positions: List[Dict], 
                                 account_equity: float) -> Dict:
        """
        Calculate % of account at risk across all open positions
        
        Args:
            positions: List of open positions from Alpaca
            account_equity: Total account equity
        
        Returns:
            Heat analysis with total risk %
        """
        total_risk = 0
        position_risks = []
        
        for pos in positions:
            # Risk = position value * volatility estimate
            position_value = float(pos.get('market_value', 0))
            position_pct = position_value / account_equity if account_equity > 0 else 0
            
            # Estimate risk at 2% stop loss
            estimated_risk = position_value * 0.02
            
            total_risk += estimated_risk
            position_risks.append({
                'symbol': pos['symbol'],
                'value': position_value,
                'risk': estimated_risk
            })
        
        total_heat = total_risk / account_equity if account_equity > 0 else 0
        
        return {
            'total_heat_pct': total_heat,
            'total_risk_dollars': total_risk,
            'positions': position_risks,
            'warning': 'CAUTION' if total_heat > 0.05 else 'OK',
            'max_recommended_heat': 0.05,  # 5% of account
        }
    
    @staticmethod
    def calculate_correlation_exposure(positions: List[Dict]) -> Dict:
        """
        Analyze correlation exposure (simplified)
        Group positions by sector
        """
        sector_groups = {
            'tech': ['AAPL', 'MSFT', 'GOOGL', 'NVDA', 'TSLA', 'META'],
            'finance': ['JPM', 'BAC', 'GS', 'WFC'],
            'healthcare': ['JNJ', 'UNH', 'PFE', 'ABBV'],
            'energy': ['XOM', 'CVX', 'COP'],
            'consumer': ['AMZN', 'WMT', 'MCD', 'NKE']
        }
        
        sector_exposure = {}
        for pos in positions:
            symbol = pos['symbol']
            value = float(pos.get('market_value', 0))
            
            for sector, symbols in sector_groups.items():
                if symbol in symbols:
                    if sector not in sector_exposure:
                        sector_exposure[sector] = {'count': 0, 'value': 0}
                    sector_exposure[sector]['count'] += 1
                    sector_exposure[sector]['value'] += value
        
        return sector_exposure
    
    @staticmethod
    def calculate_max_drawdown(portfolio_history_file: str = 'portfolio_history.json') -> float:
        """
        Calculate maximum drawdown from portfolio history
        """
        if not os.path.exists(portfolio_history_file):
            return 0
        
        try:
            with open(portfolio_history_file, 'r') as f:
                history = json.load(f)
            
            if not history:
                return 0
            
            equities = [h['account']['equity'] for h in history]
            
            max_equity = equities[0]
            max_dd = 0
            
            for equity in equities:
                if equity > max_equity:
                    max_equity = equity
                
                drawdown = (max_equity - equity) / max_equity if max_equity > 0 else 0
                if drawdown > max_dd:
                    max_dd = drawdown
            
            return max_dd
        except (OSError, json.JSONDecodeError, KeyError, ValueError, ZeroDivisionError):
            return 0

    @staticmethod
    def calculate_volatility(portfolio_history_file: str = 'portfolio_history.json') -> float:
        """
        Calculate portfolio volatility (daily returns std dev)
        """
        if not os.path.exists(portfolio_history_file):
            return 0
        
        try:
            with open(portfolio_history_file, 'r') as f:
                history = json.load(f)
            
            if len(history) < 2:
                return 0
            
            equities = [h['account']['equity'] for h in history]
            returns = []
            
            for i in range(1, len(equities)):
                ret = (equities[i] - equities[i-1]) / equities[i-1]
                returns.append(ret)
            
            if not returns:
                return 0
            
            import statistics
            volatility = statistics.stdev(returns) if len(returns) > 1 else 0
            
            return volatility * (252 ** 0.5)  # Annualized
        except (OSError, json.JSONDecodeError, KeyError, ValueError, ZeroDivisionError, _statistics_module.StatisticsError):
            return 0

    @staticmethod
    def calculate_sortino_ratio(portfolio_history_file: str = 'portfolio_history.json',
                               risk_free_rate: float = 0.04) -> float:
        """
        Sortino Ratio: Return / Downside Volatility
        Better than Sharpe for strategies with asymmetric returns
        """
        if not os.path.exists(portfolio_history_file):
            return 0
        
        try:
            with open(portfolio_history_file, 'r') as f:
                history = json.load(f)
            
            if len(history) < 2:
                return 0
            
            equities = [h['account']['equity'] for h in history]
            returns = []
            
            for i in range(1, len(equities)):
                ret = (equities[i] - equities[i-1]) / equities[i-1]
                returns.append(ret)
            
            # Total return
            total_return = (equities[-1] - equities[0]) / equities[0]
            
            # Downside returns only
            downside_returns = [r for r in returns if r < 0]
            
            if not downside_returns:
                return total_return / (risk_free_rate / 252)  # No downside = very high ratio
            
            import statistics
            downside_std = statistics.stdev(downside_returns) if len(downside_returns) > 1 else 0
            
            excess_return = total_return - risk_free_rate
            sortino = excess_return / downside_std if downside_std > 0 else 0

            return sortino
        except (OSError, json.JSONDecodeError, KeyError, ValueError, ZeroDivisionError, _statistics_module.StatisticsError):
            return 0
    
    @staticmethod
    def display_dashboard():
        """Display portfolio metrics dashboard"""
        from config import ALPACA_API_KEY
        
        if not ALPACA_API_KEY:
            print("❌ ALPACA_API_KEY not set")
            return
        
        client = AlpacaClient()
        account = client.get_account()
        positions = client.get_positions()
        
        print("\n" + "="*70)
        print("📊 PORTFOLIO METRICS DASHBOARD")
        print("="*70)
        
        equity = float(account['equity'])
        buying_power = float(account['buying_power'])
        
        print(f"\n💰 Account Summary:")
        print(f"  Equity: ${equity:,.2f}")
        print(f"  Buying Power: ${buying_power:,.2f}")
        print(f"  Cash: ${float(account['cash']):,.2f}")
        print(f"  Positions: {len(positions)}")
        
        # Portfolio Heat
        heat = PortfolioMetrics.calculate_portfolio_heat(positions, equity)
        print(f"\n🔥 Portfolio Heat (Risk Exposure):")
        print(f"  Total Risk: {heat['total_heat_pct']:.2%} of account")
        print(f"  Risk Dollars: ${heat['total_risk_dollars']:,.2f}")
        print(f"  Status: {heat['warning']} (Max: {heat['max_recommended_heat']:.1%})")
        
        # Correlation
        sector_exp = PortfolioMetrics.calculate_correlation_exposure(positions)
        if sector_exp:
            print(f"\n📈 Sector Exposure:")
            for sector, data in sorted(sector_exp.items(), key=lambda x: x[1]['value'], reverse=True):
                pct = data['value'] / equity * 100 if equity > 0 else 0
                print(f"  {sector.capitalize()}: ${data['value']:,.0f} ({pct:.1f}%) - {data['count']} positions")
        
        # Risk Metrics
        max_dd = PortfolioMetrics.calculate_max_drawdown()
        volatility = PortfolioMetrics.calculate_volatility()
        sortino = PortfolioMetrics.calculate_sortino_ratio()
        
        print(f"\n📉 Risk Metrics:")
        print(f"  Max Drawdown: {max_dd:.2%}")
        print(f"  Volatility (Ann.): {volatility:.2%}")
        print(f"  Sortino Ratio: {sortino:.2f}")
        
        print("="*70 + "\n")


def monitor_portfolio_health():
    """
    Continuous monitoring function
    Returns status: OK, WARNING, CRITICAL
    """
    from config import ALPACA_API_KEY
    
    if not ALPACA_API_KEY:
        return 'ERROR'
    
    client = AlpacaClient()
    account = client.get_account()
    positions = client.get_positions()
    
    equity = float(account['equity'])
    heat = PortfolioMetrics.calculate_portfolio_heat(positions, equity)
    max_dd = PortfolioMetrics.calculate_max_drawdown()
    
    status = 'OK'
    if heat['total_heat_pct'] > 0.08:
        status = 'CRITICAL'
    elif heat['total_heat_pct'] > 0.05:
        status = 'WARNING'
    
    if max_dd > 0.10:
        status = 'CRITICAL'
    elif max_dd > 0.05:
        status = 'WARNING'
    
    return {
        'status': status,
        'heat': heat['total_heat_pct'],
        'max_drawdown': max_dd,
        'equity': equity,
        'positions': len(positions)
    }


if __name__ == "__main__":
    PortfolioMetrics.display_dashboard()
