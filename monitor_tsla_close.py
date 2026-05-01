#!/usr/bin/env python3
"""Monitor the latest TSLA sell order until it is filled or times out."""
import time
from datetime import datetime, timezone
from alpaca_client import AlpacaClient

POLL_INTERVAL = 10
MAX_ATTEMPTS = 30


def get_latest_tsla_order(client):
    orders = client.get_orders(status='all')
    tsla_orders = [o for o in orders if o.get('symbol') == 'TSLA']
    if not tsla_orders:
        return None
    return sorted(tsla_orders, key=lambda x: x.get('submitted_at', ''), reverse=True)[0]


def display_order(order):
    if not order:
        print('No TSLA orders found.')
        return

    print('--- TSLA Order ---')
    print(f"ID: {order.get('id')}")
    print(f"Side: {order.get('side')}")
    print(f"Qty: {order.get('qty')}")
    print(f"Filled Qty: {order.get('filled_qty')}")
    print(f"Status: {order.get('status')}")
    print(f"Price: {order.get('filled_avg_price')}")
    print(f"Submitted At: {order.get('submitted_at')}")
    print(f"Filled At: {order.get('filled_at')}")


def display_positions(client):
    positions = client.get_positions()
    if not positions:
        print('No current positions.')
        return

    print('--- Current Positions ---')
    for pos in positions:
        print(f"{pos.get('symbol')}: {pos.get('qty')} shares, avg entry {pos.get('avg_entry_price')}")


def monitor_close():
    client = AlpacaClient()
    print(f"Starting TSLA close monitor at {datetime.now(timezone.utc).isoformat()} UTC")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        order = get_latest_tsla_order(client)
        if not order:
            print('No TSLA orders found. Nothing to monitor.')
            return

        print(f"\nAttempt {attempt}/{MAX_ATTEMPTS} - status: {order.get('status')} | filled {order.get('filled_qty')}/{order.get('qty')}")
        display_order(order)
        display_positions(client)

        if order.get('status') == 'filled' or order.get('filled_qty') == order.get('qty'):
            print('\n✅ TSLA sell order is filled. Position should be closed.')
            return

        if order.get('status') in ['canceled', 'rejected', 'failed']:
            print(f"\n⚠️ Order is in terminal state: {order.get('status')}")
            return

        time.sleep(POLL_INTERVAL)

    print('\n⚠️ Timeout reached. The TSLA sell order has not filled yet.')
    print('Run check_tsla_order.py again to inspect the latest order state.')


if __name__ == '__main__':
    monitor_close()
