import argparse
import time
from datetime import datetime, timedelta
import pytz
from alpaca_client import AlpacaClient

# Market hours in ET
MARKET_OPEN = "09:30"
MARKET_CLOSE = "16:00"
ET = pytz.timezone("US/Eastern")


def parse_time(time_str):
    """Parse HH:MM format time string"""
    try:
        parts = time_str.split(":")
        if len(parts) != 2:
            raise ValueError("Time must be in HH:MM format")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23) or not (0 <= minute <= 59):
            raise ValueError("Invalid hour or minute")
        return hour, minute
    except Exception as e:
        raise ValueError(f"Invalid time format: {e}")


def get_next_execution_time(target_hour, target_minute):
    """Calculate when to execute the trade"""
    now_et = datetime.now(ET)
    target_time = now_et.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    
    # If target time is in the past today, schedule for tomorrow
    if target_time <= now_et:
        target_time += timedelta(days=1)
    
    return target_time


def wait_until(target_dt):
    """Wait until target datetime"""
    while True:
        now = datetime.now(ET)
        if now >= target_dt:
            break
        
        seconds_left = (target_dt - now).total_seconds()
        if seconds_left > 60:
            print(f"Waiting... {int(seconds_left / 60)} minutes until {target_dt.strftime('%H:%M:%S ET')}")
            time.sleep(30)
        elif seconds_left > 0:
            print(f"Waiting {int(seconds_left)} seconds...")
            time.sleep(1)
        else:
            break


def schedule_trade(symbol, qty, side, target_time_str):
    """Schedule and execute a trade at a specific time"""
    print(f"Scheduling {side.upper()} order: {qty} shares of {symbol}")
    
    target_hour, target_minute = parse_time(target_time_str)
    target_dt = get_next_execution_time(target_hour, target_minute)
    
    print(f"Current time: {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Target time: {target_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Waiting for execution...")
    
    wait_until(target_dt)
    
    print(f"\n⏰ Executing order at {datetime.now(ET).strftime('%H:%M:%S ET')}")
    
    client = AlpacaClient()
    
    try:
        order = client.submit_order(
            symbol=symbol,
            qty=int(qty),
            side=side.lower(),
            order_type='market',
            time_in_force='day'
        )
        print(f"✅ Order submitted successfully!")
        print(f"Order ID: {order.get('id')}")
        print(f"Status: {order.get('status')}")
        print(f"Filled qty: {order.get('filled_qty')}")
        return order
    except Exception as e:
        print(f"❌ Error submitting order: {e}")
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Schedule a paper trading order for a specific time")
    parser.add_argument("--symbol", required=True, help="Stock ticker (e.g., TSLA, AAPL)")
    parser.add_argument("--qty", required=True, type=int, help="Number of shares")
    parser.add_argument("--side", required=True, choices=["buy", "sell"], help="Order side")
    parser.add_argument("--time", required=True, help="Execution time in HH:MM format (ET)")
    
    args = parser.parse_args()
    
    schedule_trade(
        symbol=args.symbol.upper(),
        qty=args.qty,
        side=args.side,
        target_time_str=args.time
    )
