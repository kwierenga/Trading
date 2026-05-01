from alpaca_client import AlpacaClient
import json
from datetime import datetime, timedelta

def save_market_data():
    """Save historical market data to JSON file"""
    client = AlpacaClient()
    symbols = ['AAPL', 'GOOGL', 'MSFT', 'TSLA']

    try:
        print("Fetching historical data...")
        # Get 100 days of daily bars
        bars = client.get_barset(symbols, '1D', limit=100)

        data = {
            'timestamp': datetime.now().isoformat(),
            'symbols': {}
        }

        for symbol in symbols:
            if symbol in bars and bars[symbol]:
                data['symbols'][symbol] = bars[symbol]
            else:
                data['symbols'][symbol] = []

        # Save to file
        filename = f"market_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)

        print(f"Data saved to {filename}")
        return filename

    except Exception as e:
        print(f"Error: {e}")
        return None

def analyze_price_movements():
    """Analyze recent price movements"""
    client = AlpacaClient()
    symbols = ['AAPL', 'GOOGL', 'MSFT']

    try:
        print("=== Price Movement Analysis ===")
        bars = client.get_barset(symbols, '1D', limit=5)  # Last 5 days

        for symbol in symbols:
            if symbol in bars and len(bars[symbol]) >= 2:
                prices = [float(bar['c']) for bar in bars[symbol]]
                current_price = prices[0]
                previous_price = prices[1]

                change = current_price - previous_price
                change_pct = (change / previous_price) * 100

                trend = "📈 UP" if change > 0 else "📉 DOWN" if change < 0 else "➡️ FLAT"

                print(f"{symbol}: ${current_price:.2f} ({change_pct:+.2f}%) {trend}")
            else:
                print(f"{symbol}: Insufficient data")

    except Exception as e:
        if "subscription does not permit" in str(e):
            print("❌ Historical market data not available with your current Alpaca account.")
            print("💡 Upgrade to a paid plan for historical price analysis.")
            print("\n📊 You can still analyze your portfolio:")
            portfolio_analyzer()
        else:
            print(f"Error: {e}")

def export_portfolio_to_csv():
    """Export portfolio data to CSV"""
    client = AlpacaClient()

    try:
        account = client.get_account()
        positions = client.get_positions()

        # Create CSV content
        csv_content = "Symbol,Quantity,Entry Price,Current Price,Market Value,P&L,P&L %\n"

        total_value = float(account['equity'])
        total_pl = 0

        for pos in positions:
            qty = float(pos['qty'])
            entry_price = float(pos['avg_entry_price'])
            current_price = float(pos['current_price'])
            market_value = qty * current_price
            pl = float(pos['unrealized_pl'])
            pl_pct = (pl / (qty * entry_price)) * 100

            total_pl += pl

            csv_content += f"{pos['symbol']},{qty},{entry_price:.2f},{current_price:.2f},{market_value:.2f},{pl:.2f},{pl_pct:.2f}%\n"

        # Add summary
        csv_content += f"\nTotal Equity,{total_value:.2f}\n"
        csv_content += f"Total P&L,{total_pl:.2f}\n"

        # Save to file
        filename = f"portfolio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(filename, 'w') as f:
            f.write(csv_content)

        print(f"Portfolio data exported to {filename}")
        return filename

    except Exception as e:
        print(f"Error: {e}")
        return None

if __name__ == "__main__":
    print("Choose an option:")
    print("1. Save Market Data to JSON")
    print("2. Analyze Price Movements")
    print("3. Export Portfolio to CSV")

    choice = input("Enter choice (1-3): ")

    if choice == '1':
        save_market_data()
    elif choice == '2':
        analyze_price_movements()
    elif choice == '3':
        export_portfolio_to_csv()
    else:
        print("Invalid choice")
