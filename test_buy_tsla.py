#!/usr/bin/env python3
"""
Quick test: Buy 1 share of TSLA at market price
"""

from alpaca_client import AlpacaClient

def test_buy_tsla():
    print("🚀 Testing TSLA buy order...")
    print()
    
    try:
        client = AlpacaClient()
        
        # Check account first
        account = client.get_account()
        print(f"Account Status: {account['status']}")
        print(f"Cash Available: ${float(account['cash']):,.2f}")
        print()
        
        # Submit market order to buy 1 share of TSLA
        print("📝 Submitting order: BUY 1 TSLA at market price...")
        order = client.submit_order(
            symbol='TSLA',
            qty=1,
            side='buy',
            order_type='market',
            time_in_force='day'
        )
        
        print(f"✅ Order submitted successfully!")
        print(f"   Order ID: {order['id']}")
        print(f"   Status: {order['status']}")
        print(f"   Symbol: {order['symbol']}")
        print(f"   Side: {order['side']}")
        print(f"   Quantity: {order['qty']}")
        print(f"   Type: {order['type']}")
        print()
        
        # Check account after order
        account_after = client.get_account()
        print(f"Cash After Order: ${float(account_after['cash']):,.2f}")
        print(f"Cash Change: ${float(account['cash']) - float(account_after['cash']):,.2f}")
        print()
        
        print("✅ Test successful! Trading system is working.")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_buy_tsla()
