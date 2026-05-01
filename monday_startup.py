#!/usr/bin/env python3
"""
Monday Startup Script
Automated sequence to launch the 6-week trading experiment
"""

import os
import sys
import time
from datetime import datetime

def run_command(cmd, description):
    """Run a command and report result"""
    print(f"\n🔄 {description}...")
    result = os.system(cmd)
    if result == 0:
        print(f"✅ {description} - SUCCESS")
        return True
    else:
        print(f"❌ {description} - FAILED")
        return False

def monday_startup():
    """Execute Monday startup sequence"""
    print("\n" + "="*70)
    print("🚀 MONDAY STARTUP SEQUENCE")
    print("6-Week AI Trading Experiment")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    success_count = 0
    total_steps = 6

    # Step 1: Save initial portfolio snapshot
    if run_command("python performance_tracker.py track", "Step 1: Save initial portfolio snapshot"):
        success_count += 1

    # Step 2: Get Claude's strategy recommendations
    if run_command("python ai_strategy_enhanced.py", "Step 2: Get AI strategy recommendations"):
        success_count += 1

    # Step 3: Check portfolio health
    if run_command("python portfolio_monitor.py check", "Step 3: Verify portfolio health"):
        success_count += 1

    # Step 4: Display trade journal status
    if run_command("python trade_journal.py stats", "Step 4: Review trade journal"):
        success_count += 1

    # Step 5: Show current positions
    if run_command("python main.py", "Step 5: Display account status"):
        success_count += 1

    # Step 6: Start monitoring (optional - runs in background)
    print("\n🔄 Step 6: Portfolio monitoring (optional - runs in background)")
    print("   To start monitoring: python portfolio_monitor.py monitor 8")
    print("   This will monitor for 8 hours during market hours")
    success_count += 1  # Count as success since it's optional

    print("\n" + "="*70)
    print(f"📊 STARTUP COMPLETE: {success_count}/{total_steps} steps successful")

    if success_count >= 5:
        print("✅ SYSTEM READY FOR TRADING")
        print("\nNext steps:")
        print("1. Review Claude's recommendations in latest_strategy.json")
        print("2. Execute trades manually or use claude_trader.py")
        print("3. Start monitoring: python portfolio_monitor.py monitor 8")
        print("4. Save EOD snapshot: python performance_tracker.py track")
    else:
        print("⚠️  ISSUES DETECTED - Review output above")

    print("="*70 + "\n")

if __name__ == "__main__":
    monday_startup()
