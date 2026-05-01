"""
Portfolio Monitoring & Alert System
Real-time monitoring with stop loss enforcement, heat alerts, and daily status reports
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from alpaca_client import AlpacaClient
from portfolio_metrics import PortfolioMetrics, monitor_portfolio_health
from trade_journal import TradeJournal
from position_sizer import get_trade_statistics


class PortfolioMonitor:
    """Real-time portfolio monitoring with alerts"""
    
    ALERT_FILE = 'portfolio_alerts.json'
    STATUS_FILE = 'monitor_status.json'
    
    @staticmethod
    def load_alerts() -> List[Dict]:
        """Load alert history"""
        if not os.path.exists(PortfolioMonitor.ALERT_FILE):
            return []
        
        try:
            with open(PortfolioMonitor.ALERT_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    
    @staticmethod
    def save_alerts(alerts: List[Dict]):
        """Save alerts to history"""
        with open(PortfolioMonitor.ALERT_FILE, 'w') as f:
            json.dump(alerts, f, indent=2, default=str)
    
    @staticmethod
    def add_alert(alert_type: str, severity: str, message: str, data: Dict = None):
        """Add alert to history"""
        alerts = PortfolioMonitor.load_alerts()
        
        alert = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'type': alert_type,
            'severity': severity,
            'message': message,
            'data': data or {},
            'acknowledged': False
        }
        
        alerts.append(alert)
        PortfolioMonitor.save_alerts(alerts)
        
        return alert
    
    @staticmethod
    def check_portfolio_health() -> Dict:
        """
        Comprehensive portfolio health check
        Returns: Dict with status, alerts, recommendations
        """
        from config import ALPACA_API_KEY
        
        if not ALPACA_API_KEY:
            return {'error': 'ALPACA_API_KEY not set'}
        
        client = AlpacaClient()
        account = client.get_account()
        positions = client.get_positions()
        
        equity = float(account['equity'])
        alerts_list = []
        recommendations = []
        
        # 1. Check portfolio heat
        heat = PortfolioMetrics.calculate_portfolio_heat(positions, equity)
        if heat['total_heat_pct'] > 0.08:
            alerts_list.append({
                'type': 'HIGH_HEAT',
                'severity': 'CRITICAL',
                'message': f"Portfolio heat is {heat['total_heat_pct']:.1%} (max 5%)",
                'heat': heat['total_heat_pct']
            })
            PortfolioMonitor.add_alert('HIGH_HEAT', 'CRITICAL', 
                                      f"Heat {heat['total_heat_pct']:.1%}", heat)
            recommendations.append("REDUCE POSITION SIZES or close trades")
        
        elif heat['total_heat_pct'] > 0.05:
            alerts_list.append({
                'type': 'ELEVATED_HEAT',
                'severity': 'WARNING',
                'message': f"Portfolio heat is {heat['total_heat_pct']:.1%} (caution zone)",
                'heat': heat['total_heat_pct']
            })
        
        # 2. Check maximum drawdown
        max_dd = PortfolioMetrics.calculate_max_drawdown()
        if max_dd > 0.10:
            alerts_list.append({
                'type': 'MAX_DRAWDOWN',
                'severity': 'CRITICAL',
                'message': f"Max drawdown is {max_dd:.2%}",
                'drawdown': max_dd
            })
            PortfolioMonitor.add_alert('MAX_DRAWDOWN', 'CRITICAL', 
                                      f"Drawdown {max_dd:.2%}", {'drawdown': max_dd})
            recommendations.append("Reduce exposure and review risk management")
        
        # 3. Check losing streak
        trades = TradeJournal.load_trades()
        closed_trades = [t for t in trades if t['status'] == 'closed']
        
        if len(closed_trades) >= 3:
            recent_3 = closed_trades[-3:]
            losses = sum(1 for t in recent_3 if t['pnl'] <= 0)
            
            if losses == 3:
                alerts_list.append({
                    'type': 'LOSING_STREAK',
                    'severity': 'WARNING',
                    'message': "3 consecutive losing trades",
                    'losses': 3
                })
                PortfolioMonitor.add_alert('LOSING_STREAK', 'WARNING',
                                          "3 consecutive losses", {'losses': 3})
                recommendations.append("Review trade thesis - may need strategy adjustment")
        
        # 4. Check cash position
        cash = float(account['cash'])
        cash_pct = cash / equity if equity > 0 else 0
        
        if cash_pct < 0.10:
            alerts_list.append({
                'type': 'LOW_CASH',
                'severity': 'INFO',
                'message': f"Only {cash_pct:.1%} cash available",
                'cash_pct': cash_pct
            })
        
        # 5. Weekly performance check
        from performance_tracker import load_history, calculate_weekly_returns
        history = load_history()
        if history:
            accounts = [h['account'] for h in history]
            weekly_perf = calculate_weekly_returns(accounts)
            weekly_return = weekly_perf.get('weekly_return_pct', 0)
            
            if weekly_return < -0.02:  # Down 2% this week
                alerts_list.append({
                    'type': 'WEEKLY_LOSS',
                    'severity': 'WARNING',
                    'message': f"Weekly loss of {weekly_return:.2%}",
                    'weekly_return': weekly_return
                })
                PortfolioMonitor.add_alert('WEEKLY_LOSS', 'WARNING',
                                          f"Week down {weekly_return:.2%}", 
                                          {'return': weekly_return})
        
        return {
            'status': 'OK' if not alerts_list else ('WARNING' if any(a['severity'] == 'WARNING' for a in alerts_list) else 'CRITICAL'),
            'alerts': alerts_list,
            'recommendations': recommendations,
            'equity': equity,
            'positions': len(positions),
            'portfolio_heat': heat['total_heat_pct'],
            'max_drawdown': max_dd,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    
    @staticmethod
    def check_stop_losses() -> Dict:
        """Monitor open positions for stop loss hits (requires live data)"""
        from config import ALPACA_API_KEY
        
        if not ALPACA_API_KEY:
            return {'error': 'ALPACA_API_KEY not set'}
        
        client = AlpacaClient()
        positions = client.get_positions()
        trades = TradeJournal.load_trades()
        
        open_trades = {t['symbol']: t for t in trades if t['status'] == 'open'}
        stops_hit = []
        
        for pos in positions:
            symbol = pos['symbol']
            
            if symbol in open_trades:
                trade = open_trades[symbol]
                current_price = float(pos.get('current_price', 0))
                stop_loss = trade['stop_loss']
                
                # Check if stop loss hit
                if current_price <= stop_loss:
                    stops_hit.append({
                        'symbol': symbol,
                        'current_price': current_price,
                        'stop_loss': stop_loss,
                        'loss_pct': (current_price - trade['entry_price']) / trade['entry_price']
                    })
                    
                    PortfolioMonitor.add_alert('STOP_LOSS_HIT', 'INFO',
                                              f"{symbol} hit stop loss at ${current_price:.2f}",
                                              {'symbol': symbol, 'price': current_price})
        
        return {
            'stops_hit': stops_hit,
            'count': len(stops_hit)
        }
    
    @staticmethod
    def generate_daily_report() -> str:
        """Generate comprehensive daily status report"""
        from config import ALPACA_API_KEY
        
        if not ALPACA_API_KEY:
            return "❌ ALPACA_API_KEY not set"
        
        health = PortfolioMonitor.check_portfolio_health()
        stops = PortfolioMonitor.check_stop_losses()
        
        client = AlpacaClient()
        account = client.get_account()
        
        report = "\n" + "="*70 + "\n"
        report += "📋 DAILY PORTFOLIO MONITORING REPORT\n"
        report += f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        report += "="*70 + "\n"
        
        # Status
        status_emoji = "✅" if health['status'] == 'OK' else ("⚠️" if health['status'] == 'WARNING' else "🚨")
        report += f"\n{status_emoji} Status: {health['status']}\n"
        
        # Account
        report += f"\n💰 Account:\n"
        report += f"  Equity: ${float(account['equity']):,.2f}\n"
        report += f"  Buying Power: ${float(account['buying_power']):,.2f}\n"
        report += f"  Cash: ${float(account['cash']):,.2f}\n"
        report += f"  Positions: {health['positions']}\n"
        
        # Risk Metrics
        report += f"\n📊 Risk Metrics:\n"
        report += f"  Portfolio Heat: {health['portfolio_heat']:.2%}\n"
        report += f"  Max Drawdown: {health['max_drawdown']:.2%}\n"
        
        # Alerts
        if health['alerts']:
            report += f"\n🚨 Alerts ({len(health['alerts'])}):\n"
            for alert in health['alerts']:
                report += f"  [{alert['severity']}] {alert['message']}\n"
        
        # Stop Losses
        if stops.get('stops_hit'):
            report += f"\n⛔ Stop Losses Hit ({len(stops['stops_hit'])}):\n"
            for stop in stops['stops_hit']:
                report += f"  {stop['symbol']}: ${stop['current_price']:.2f} ({stop['loss_pct']:+.2%})\n"
        
        # Recommendations
        if health['recommendations']:
            report += f"\n💡 Recommendations:\n"
            for rec in health['recommendations']:
                report += f"  • {rec}\n"
        
        report += "\n" + "="*70 + "\n"
        
        return report
    
    @staticmethod
    def continuous_monitoring(check_interval_seconds: int = 300, duration_hours: int = 1):
        """
        Monitor portfolio continuously (useful for paper trading during market hours)
        
        Args:
            check_interval_seconds: How often to check (default 5 min)
            duration_hours: How long to monitor (default 1 hour)
        """
        import sys
        
        print(f"\n📡 Starting continuous monitoring for {duration_hours} hour(s)")
        print(f"    Check interval: {check_interval_seconds} seconds")
        print(f"    Press Ctrl+C to stop\n")
        
        start_time = time.time()
        end_time = start_time + (duration_hours * 3600)
        check_count = 0
        
        try:
            while time.time() < end_time:
                check_count += 1
                health = PortfolioMonitor.check_portfolio_health()
                
                print(f"\n[Check #{check_count}] {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
                print(f"  Status: {health['status']} | Heat: {health['portfolio_heat']:.2%} | Positions: {health['positions']}")
                
                if health['alerts']:
                    for alert in health['alerts']:
                        print(f"  🚨 [{alert['severity']}] {alert['message']}")
                
                time.sleep(check_interval_seconds)
        
        except KeyboardInterrupt:
            print("\n\n✋ Monitoring stopped by user")


def display_alert_history(limit: int = 10):
    """Display recent alerts"""
    alerts = PortfolioMonitor.load_alerts()
    
    if not alerts:
        print("✅ No alerts on record")
        return
    
    recent = sorted(alerts, key=lambda x: x['timestamp'], reverse=True)[:limit]
    
    print("\n" + "="*70)
    print("📜 ALERT HISTORY")
    print("="*70 + "\n")
    
    for alert in recent:
        ts = alert['timestamp']
        severity_emoji = "🚨" if alert['severity'] == 'CRITICAL' else ("⚠️" if alert['severity'] == 'WARNING' else "ℹ️")
        print(f"{severity_emoji} {ts}: {alert['message']}")
    
    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "check":
            report = PortfolioMonitor.generate_daily_report()
            print(report)
        
        elif command == "alerts":
            display_alert_history()
        
        elif command == "monitor":
            # Monitor for duration (default 1 hour)
            duration = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            PortfolioMonitor.continuous_monitoring(duration_hours=duration)
    else:
        report = PortfolioMonitor.generate_daily_report()
        print(report)
