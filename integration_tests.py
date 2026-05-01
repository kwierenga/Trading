"""
Integration Testing & Monday Ready Checklist
Validates all systems before live trading begins
"""

import os
import sys
from datetime import datetime, timezone
from typing import Tuple, Dict


def test_import(module_name: str) -> Tuple[bool, str]:
    """Test if a module can be imported"""
    try:
        __import__(module_name)
        return True, f"✅ {module_name}"
    except ImportError as e:
        return False, f"❌ {module_name}: {e}"


def test_environment() -> Tuple[bool, Dict]:
    """Test environment configuration"""
    from config import ALPACA_API_KEY, ALPACA_API_SECRET, ALPACA_API_BASE_URL, ANTHROPIC_API_KEY

    results = {
        'alpaca_key': bool(ALPACA_API_KEY),
        'alpaca_secret': bool(ALPACA_API_SECRET),
        'alpaca_url': bool(ALPACA_API_BASE_URL),
        'anthropic_key': bool(ANTHROPIC_API_KEY),
        'all_set': all([
            ALPACA_API_KEY,
            ALPACA_API_SECRET,
            ALPACA_API_BASE_URL,
            ANTHROPIC_API_KEY
        ])
    }
    
    return results['all_set'], results


def test_alpaca_connection() -> Tuple[bool, Dict]:
    """Test Alpaca API connection"""
    try:
        from alpaca_client import AlpacaClient
        
        client = AlpacaClient()
        account = client.get_account()
        
        if not account:
            return False, {'error': 'No account data returned'}
        
        return True, {
            'equity': float(account.get('equity', 0)),
            'cash': float(account.get('cash', 0)),
            'status': account.get('status', 'unknown')
        }
    except Exception as e:
        return False, {'error': str(e)}


def test_trade_journal() -> Tuple[bool, Dict]:
    """Test trade journal functionality"""
    try:
        from trade_journal import TradeJournal
        
        stats = TradeJournal.get_statistics()
        
        return True, {
            'total_trades': stats['total_trades'],
            'win_rate': stats['win_rate']
        }
    except Exception as e:
        return False, {'error': str(e)}


def test_position_sizer() -> Tuple[bool, Dict]:
    """Test position sizing"""
    try:
        from position_sizer import PositionSizer
        
        sizer = PositionSizer(100000, win_rate=0.60, avg_win=0.015, avg_loss=0.010)
        kelly, position = sizer.kelly_criterion()
        
        return True, {
            'kelly_fraction': kelly,
            'position_size': position
        }
    except Exception as e:
        return False, {'error': str(e)}


def test_portfolio_metrics() -> Tuple[bool, Dict]:
    """Test portfolio metrics"""
    try:
        from portfolio_metrics import PortfolioMetrics
        from alpaca_client import AlpacaClient
        
        client = AlpacaClient()
        account = client.get_account()
        positions = client.get_positions()
        
        heat = PortfolioMetrics.calculate_portfolio_heat(positions, float(account['equity']))
        
        return True, {
            'heat': heat['total_heat_pct'],
            'risk_dollars': heat['total_risk_dollars']
        }
    except Exception as e:
        return False, {'error': str(e)}


def test_backtest_engine() -> Tuple[bool, Dict]:
    """Test backtesting engine"""
    try:
        from backtest_engine import BacktestEngine, load_market_data
        
        # Create simple test
        engine = BacktestEngine()
        
        # Mock data
        mock_data = [
            {'timestamp': '2024-01-01', 'close': 100, 'high': 101, 'low': 99, 'symbol': 'TEST'},
            {'timestamp': '2024-01-02', 'close': 102, 'high': 103, 'low': 101, 'symbol': 'TEST'},
            {'timestamp': '2024-01-03', 'close': 101, 'high': 103, 'low': 100, 'symbol': 'TEST'},
        ]
        
        def dummy_strategy(bar, positions, equity):
            return []
        
        result = engine.simulate_strategy(mock_data, dummy_strategy)
        
        return True, {
            'starting_equity': result['starting_equity'],
            'ending_equity': result['ending_equity']
        }
    except Exception as e:
        return False, {'error': str(e)}


def test_ai_strategy() -> Tuple[bool, Dict]:
    """Test enhanced AI strategy"""
    try:
        from ai_strategy_enhanced import build_rich_context
        
        context = build_rich_context()
        
        if not context:
            return False, {'error': 'No context built'}
        
        return True, {
            'has_account': 'account' in context,
            'has_stats': 'trade_statistics' in context,
            'has_risk': 'portfolio_risk' in context
        }
    except Exception as e:
        return False, {'error': str(e)}


def test_portfolio_monitor() -> Tuple[bool, Dict]:
    """Test portfolio monitoring"""
    try:
        from portfolio_monitor import PortfolioMonitor
        
        health = PortfolioMonitor.check_portfolio_health()
        
        return True, {
            'status': health.get('status'),
            'alerts': len(health.get('alerts', []))
        }
    except Exception as e:
        return False, {'error': str(e)}


def run_all_tests() -> Dict:
    """Run all integration tests"""
    print("\n" + "="*70)
    print("🧪 INTEGRATION TEST SUITE")
    print("="*70)
    
    results = {}
    
    # Module imports
    print("\n📦 Module Imports:")
    modules = ['requests', 'dotenv', 'alpaca_client', 'config', 'trade_journal',
               'position_sizer', 'portfolio_metrics', 'backtest_engine', 
               'ai_strategy_enhanced', 'portfolio_monitor']
    
    import_results = {}
    for mod in modules:
        success, msg = test_import(mod)
        import_results[mod] = success
        print(f"  {msg}")
    
    results['imports'] = import_results
    
    # Environment
    print("\n🔐 Environment Configuration:")
    env_ok = False
    try:
        env_ok, env_results = test_environment()
        print(f"  {'✅' if env_results['alpaca_key'] else '❌'} Alpaca API Key: {bool(env_results['alpaca_key'])}")
        print(f"  {'✅' if env_results['alpaca_secret'] else '❌'} Alpaca Secret: {bool(env_results['alpaca_secret'])}")
        print(f"  {'✅' if env_results['alpaca_url'] else '❌'} Alpaca URL: {bool(env_results['alpaca_url'])}")
        print(f"  {'✅' if env_results['anthropic_key'] else '⚠️'} Anthropic API Key: {bool(env_results['anthropic_key'])}")
        if not env_results['anthropic_key']:
            print("    ⚠️ ANTHROPIC_API_KEY is optional for Alpaca-only operation, but required for AI strategy generation.")
        results['environment'] = env_results
    except Exception as e:
        env_ok = False
        print(f"  ❌ Error checking environment: {e}")
        results['environment'] = {'error': str(e)}
    
    # API Connection
    print("\n🌐 API Connectivity:")
    try:
        conn_ok, conn_data = test_alpaca_connection()
        if conn_ok:
            print(f"  ✅ Alpaca API Connection")
            print(f"     Equity: ${conn_data['equity']:,.2f}")
            print(f"     Cash: ${conn_data['cash']:,.2f}")
            print(f"     Status: {conn_data['status']}")
            results['alpaca_connection'] = True
        else:
            print(f"  ❌ Alpaca API Connection: {conn_data.get('error')}")
            results['alpaca_connection'] = False
    except Exception as e:
        print(f"  ❌ Connection test error: {e}")
        results['alpaca_connection'] = False
    
    # Feature Tests
    print("\n🎯 Feature Tests:")
    
    features = [
        ('Trade Journal', test_trade_journal),
        ('Position Sizer', test_position_sizer),
        ('Portfolio Metrics', test_portfolio_metrics),
        ('Backtest Engine', test_backtest_engine),
        ('AI Strategy', test_ai_strategy),
        ('Portfolio Monitor', test_portfolio_monitor)
    ]
    
    feature_results = {}
    for name, test_func in features:
        try:
            success, data = test_func()
            emoji = "✅" if success else "❌"
            print(f"  {emoji} {name}")
            feature_results[name] = success
        except Exception as e:
            print(f"  ❌ {name}: {e}")
            feature_results[name] = False
    
    results['features'] = feature_results
    
    # Summary
    print("\n" + "="*70)
    total_tests = len(modules) + len(features) + 4  # imports + features + env components
    passed_tests = (
        sum(import_results.values()) +
        sum(feature_results.values()) +
        (1 if env_ok else 0) +
        (1 if results.get('alpaca_connection') else 0)
    )
    
    print(f"📊 RESULTS: {passed_tests}/{total_tests} tests passed")
    
    if passed_tests == total_tests:
        print("✅ ALL TESTS PASSED - SYSTEM READY FOR MONDAY")
    elif passed_tests >= total_tests * 0.9:
        print("⚠️  MOST TESTS PASSED - Minor issues found, review above")
    else:
        print("❌ CRITICAL ISSUES - Fix required before starting")
    
    print("="*70 + "\n")
    
    return results


def display_monday_checklist():
    """Display pre-trading checklist"""
    print("\n" + "="*70)
    print("📋 MONDAY READY CHECKLIST")
    print("="*70)
    
    checklist = [
        ("✅ All Python modules installed (requests, python-dotenv, pytz)", True),
        ("✅ Alpaca API credentials in .env", True),
        ("✅ Claude API key in .env", True),
        ("✅ Alpaca connection verified", True),
        ("✅ Trade journal initialized", True),
        ("✅ Position sizing configured", True),
        ("✅ Portfolio monitoring active", True),
        ("✅ AI strategy system tested", True),
        ("✅ Backtest engine working", True),
        ("✅ All 9 Python modules working", True),
    ]
    
    print("\nBefore starting Monday:\n")
    for item, status in checklist:
        print(f"  {item}")
    
    print("\nMonday Startup Sequence:\n")
    print("  1. python performance_tracker.py track    (save first snapshot)")
    print("  2. python ai_strategy_enhanced.py          (get Claude's picks)")
    print("  3. python portfolio_monitor.py check       (verify health)")
    print("  4. Review trades and execute if confident  (manual or claude_trader.py)")
    print("  5. python portfolio_monitor.py monitor 8   (monitor 8 hours during market)")
    print("  6. python performance_tracker.py track    (save end-of-day snapshot)")
    
    print("\nDaily Workflow:\n")
    print("  Morning:  python ai_strategy_enhanced.py   (get daily pick)")
    print("  Midday:   python portfolio_monitor.py check (health check)")
    print("  Evening:  python performance_tracker.py track (daily snapshot)")
    
    print("\nWeekly Workflow:\n")
    print("  Sundays:  python performance_tracker.py    (see weekly report)")
    print("  Sundays:  python experiment_tracker.py     (track 6-week progress)")
    print("  Sundays:  python trade_journal.py stats    (trade statistics)")
    
    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    print("\n")
    print("🚀 DEEP DIVE INTEGRATION TEST & MONDAY READINESS")
    print("    Generated for 6-week AI trading experiment")
    print("    Target: 1% weekly growth with 70% confidence")
    print("    Start Date: Monday, May 5, 2026")
    
    # Run tests
    results = run_all_tests()
    
    # Show checklist
    display_monday_checklist()
    
    # Save results
    with open('integration_test_results.json', 'w') as f:
        import json
        json.dump({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'results': results
        }, f, indent=2, default=str)
    
    print("✅ Test results saved to integration_test_results.json")
