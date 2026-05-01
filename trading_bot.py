from alpaca_client import AlpacaClient
import time

def simple_trading_strategy():
    """Example: Buy AAPL if price drops below threshold"""
    client = AlpacaClient()

    try:
        # Get current AAPL price
        bars = client.get_barset(['AAPL'], '1Min', limit=1)
        if not bars['AAPL']:
            print("❌ AAPL market data not available.")
            print("💡 This requires a paid Alpaca account for real-time data.")
            return
        
        current_price = float(bars['AAPL'][0]['c'])

        print(f"Current AAPL price: ${current_price}")

        # Simple strategy: Buy if price is below $180
        if current_price < 180:
            print("Price is below $180, submitting buy order...")
            order = client.submit_order('AAPL', 1, 'buy', 'market', 'day')
            print(f"Order submitted: {order}")
        else:
            print("Price is above $180, no action taken")

    except Exception as e:
        if "subscription does not permit" in str(e):
            print("❌ Market data not available with your current Alpaca account.")
            print("💡 Upgrade to a paid plan for trading strategies with real-time data.")
            print("\n📊 You can still:")
            print("   • View your account balance")
            print("   • Check your positions")
            print("   • Submit manual orders")
        else:
            print(f"Error: {e}")

def market_monitor():
    """Monitor multiple stocks in real-time"""
    client = AlpacaClient()
    symbols = ['AAPL', 'GOOGL', 'MSFT', 'TSLA']

    try:
        print("=== Market Monitor ===")
        bars = client.get_barset(symbols, '1Min', limit=1)

        for symbol in symbols:
            if symbol in bars and bars[symbol]:
                price = float(bars[symbol][0]['c'])
                print(f"{symbol}: ${price}")
            else:
                print(f"{symbol}: No data available")

    except Exception as e:
        if "subscription does not permit" in str(e):
            print("❌ Market data not available with your current Alpaca account.")
            print("💡 Upgrade to a paid plan for real-time and historical market data.")
            print("\n📊 Your account information is still available:")
            account = client.get_account()
            print(f"   Equity: ${account['equity']}")
            print(f"   Buying Power: ${account['buying_power']}")
        else:
            print(f"Error: {e}")

def portfolio_analyzer():
    """Analyze portfolio performance"""
    client = AlpacaClient()

    try:
        account = client.get_account()
        positions = client.get_positions()

        print("=== Portfolio Analysis ===")
        print(f"Total Equity: ${account['equity']}")
        print(f"Buying Power: ${account['buying_power']}")

        if positions:
            total_pl = sum(float(pos['unrealized_pl']) for pos in positions)
            print(f"Total Unrealized P&L: ${total_pl:.2f}")

            for pos in positions:
                pl_pct = (float(pos['unrealized_pl']) / (float(pos['qty']) * float(pos['avg_entry_price']))) * 100
                print(f"{pos['symbol']}: {pos['qty']} shares, P&L: ${pos['unrealized_pl']} ({pl_pct:.2f}%)")
        else:
            print("No positions to analyze")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    print("Choose an option:")
    print("1. Simple Trading Strategy")
    print("2. Market Monitor")
    print("3. Portfolio Analyzer")

    choice = input("Enter choice (1-3): ")

    if choice == '1':
        simple_trading_strategy()
    elif choice == '2':
        market_monitor()
    elif choice == '3':
        portfolio_analyzer()
    else:
        print("Invalid choice")
