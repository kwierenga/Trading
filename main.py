from alpaca_client import AlpacaClient

def main():
    """Main trading application"""

    try:
        # Initialize Alpaca client
        client = AlpacaClient()

        # Get account information
        account = client.get_account()
        print("=== Account Information ===")
        print(f"Account ID: {account['id']}")
        print(f"Account Status: {account['status']}")
        print(f"Equity: ${account['equity']}")
        print(f"Buying Power: ${account['buying_power']}")
        print(f"Cash: ${account['cash']}")
        print()

        # Get current positions
        positions = client.get_positions()
        print("=== Current Positions ===")
        if positions:
            for position in positions:
                print(f"Symbol: {position['symbol']}")
                print(f"Quantity: {position['qty']}")
                print(f"Entry Price: ${position['avg_entry_price']}")
                print(f"Current Price: ${position['current_price']}")
                print(f"P&L: ${position['unrealized_pl']}")
                print()
        else:
            print("No open positions")
            print()

        # Get recent orders
        orders = client.get_orders(status='all')
        print("=== Recent Orders ===")
        if orders:
            for order in orders[:5]:  # Show last 5 orders
                print(f"Order ID: {order['id']}")
                print(f"Symbol: {order['symbol']}")
                print(f"Side: {order['side']}")
                print(f"Status: {order['status']}")
                print(f"Filled Qty: {order['filled_qty']}")
                print()
        else:
            print("No orders found")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
