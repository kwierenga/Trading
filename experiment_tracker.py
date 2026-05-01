import json
from datetime import datetime, timedelta, timezone
from performance_tracker import load_history, calculate_weekly_returns

def get_experiment_progress():
    """Get progress on the 6-week 1% growth experiment"""
    history = load_history()
    
    if not history:
        print("No experiment data yet. Start tracking with: python performance_tracker.py track")
        return None
    
    # Get data by week
    weeks_data = {}
    
    for snapshot in history:
        ts = datetime.fromisoformat(snapshot['timestamp'])
        week_key = ts.strftime("%Y-W%U")  # Year-Week format
        
        if week_key not in weeks_data:
            weeks_data[week_key] = []
        weeks_data[week_key].append(snapshot)
    
    # Calculate weekly returns
    results = []
    for week_key in sorted(weeks_data.keys()):
        snapshots = weeks_data[week_key]
        if len(snapshots) < 2:
            continue
        
        accounts = [s['account'] for s in snapshots]
        perf = calculate_weekly_returns(accounts)
        
        results.append({
            'week': week_key,
            'performance': perf,
            'snapshots': len(snapshots)
        })
    
    return results


def display_experiment_report():
    """Display 6-week experiment progress"""
    results = get_experiment_progress()
    
    if not results:
        print("No weekly data yet.")
        return
    
    print("\n" + "="*70)
    print("📈 6-WEEK AI TRADING EXPERIMENT")
    print("Target: 1% weekly growth with 70% confidence")
    print("="*70)
    
    hits = 0
    target_hits = 0
    total_return = 0
    
    for i, result in enumerate(results[:6], 1):
        perf = result['performance']
        is_hit = perf['weekly_return_pct'] >= 1.0
        
        if is_hit:
            hits += 1
            status = "✅ HIT"
        else:
            status = "❌ MISS"
        
        target_hits = max(1, int(i * 0.70))  # Expected hits at 70% confidence
        total_return += perf['weekly_return_pct']
        
        print(f"\nWeek {i} ({result['week']}):")
        print(f"  Return: {perf['weekly_return_pct']:+.2f}% {status}")
        print(f"  Starting Equity: ${perf['starting_equity']:,.2f}")
        print(f"  Ending Equity: ${perf['ending_equity']:,.2f}")
        print(f"  Progress: {hits}/{target_hits} weeks hitting target (need {target_hits} for 70%)")
    
    weeks_completed = min(len(results), 6)
    
    print("\n" + "-"*70)
    print(f"📊 SUMMARY ({weeks_completed} weeks):")
    print(f"  Weeks at/above 1%: {hits}/{weeks_completed}")
    print(f"  Weeks needed for 70%: {max(1, int(weeks_completed * 0.70))}")
    print(f"  Total Return: {total_return:+.2f}%")
    print(f"  Average Weekly: {total_return/weeks_completed:+.2f}%")
    
    if weeks_completed >= 6:
        if hits >= 4:
            print(f"\n✅ EXPERIMENT SUCCESS: Achieved {hits}/6 weeks hitting 1% target!")
        else:
            print(f"\n⚠️  EXPERIMENT RESULT: {hits}/6 weeks hit target (needed 4+ for 70% success)")
    else:
        print(f"\n⏳ Experiment in progress... {6 - weeks_completed} weeks remaining")
    
    print("="*70 + "\n")


def save_experiment_metadata():
    """Save experiment metadata"""
    metadata = {
        'start_date': datetime.now(timezone.utc).isoformat(),
        'duration_weeks': 6,
        'target_weekly_growth': 1.0,
        'confidence_target': 0.70,
        'goal': '1% weekly with 70% confidence (4-5 hits per 6 weeks)'
    }
    
    with open('experiment_metadata.json', 'w') as f:
        json.dump(metadata, f, indent=2)
    
    print("✅ 6-week experiment started!")
    print("Track progress with: python experiment_tracker.py")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "start":
        save_experiment_metadata()
    else:
        display_experiment_report()
