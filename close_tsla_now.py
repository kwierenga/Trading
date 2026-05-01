#!/usr/bin/env python3
from alpaca_client import AlpacaClient

client = AlpacaClient()
try:
    positions = client.get_positions()
    tsla_positions = [p for p in positions if p.get('symbol') == 'TSLA']
    if not tsla_positions:
        print('No TSLA position currently open.')
    else:
        pos = tsla_positions[0]
        print(f"TSLA position found: {pos.get('qty')} shares at entry {pos.get('avg_entry_price')}")
        result = client.close_position('TSLA')
        print('Close order result:')
        for key, value in result.items():
            print(f'  {key}: {value}')
except Exception as e:
    print('Error closing TSLA position:', e)
