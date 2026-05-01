#!/usr/bin/env python3
"""
Check the status of the most recently placed TSLA order.
"""
from alpaca_client import AlpacaClient

def main():
    client = AlpacaClient()
    
    try:
        orders = client.get_orders(status='all')
        tsla_orders = [o for o in orders if o.get('symbol') == 'TSLA']
        
        if not tsla_orders:
            print("No TSLA orders found.")
            return
        
        latest = sorted(tsla_orders, key=lambda x: x.get('submitted_at', ''), reverse=True)[0]
        
        print("=== Latest TSLA Order Status ===")
        print(f"Order ID: {latest.get('id')}")
        print(f"Symbol: {latest.get('symbol')}")
        print(f"Side: {latest.get('side')}")
        print(f"Type: {latest.get('type')}")
        print(f"Qty: {latest.get('qty')}")
        print(f"Filled Qty: {latest.get('filled_qty')}")
        print(f"Status: {latest.get('status')}")
        print(f"Submitted At: {latest.get('submitted_at')}")
        print(f"Filled At: {latest.get('filled_at')}")
        print(f"Limit Price: {latest.get('limit_price')}")
        print(f"Stop Price: {latest.get('stop_price')}")
        print(f"Filled Avg Price: {latest.get('filled_avg_price')}")
        print(f"Client Order ID: {latest.get('client_order_id')}")
        
        if latest.get('status') in ['filled', 'partially_filled']:
            print("\nThe order has at least partially filled.")
        elif latest.get('status') in ['pending_new', 'new', 'accepted']: 
            print("\nThe order is still open and waiting to fill.")
        else:
            print("\nThe order is in status:", latest.get('status'))
    except Exception as e:
        print(f"Error checking TSLA order: {e}")

if __name__ == '__main__':
    main()
