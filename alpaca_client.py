import requests
from config import TRADING_CONFIG

class AlpacaClient:
    """Direct Alpaca API client using requests"""

    def __init__(self):
        self.api_key = TRADING_CONFIG['api_key']
        self.api_secret = TRADING_CONFIG['api_secret']
        self.base_url = TRADING_CONFIG['base_url']
        self.headers = {
            'APCA-API-KEY-ID': self.api_key,
            'APCA-API-SECRET-KEY': self.api_secret
        }

    def _get(self, endpoint):
        """Make GET request to Alpaca API"""
        url = f"{self.base_url}{endpoint}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint, data=None):
        """Make POST request to Alpaca API"""
        url = f"{self.base_url}{endpoint}"
        response = requests.post(url, headers=self.headers, json=data)
        response.raise_for_status()
        return response.json()

    def _delete(self, endpoint):
        """Make DELETE request to Alpaca API"""
        url = f"{self.base_url}{endpoint}"
        response = requests.delete(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def get_account(self):
        """Get account information"""
        return self._get('/account')

    def get_positions(self):
        """Get current positions"""
        return self._get('/positions')

    def get_orders(self, status='all'):
        """Get orders with specified status"""
        return self._get(f'/orders?status={status}')

    def submit_order(
        self,
        symbol,
        qty,
        side,
        order_type='market',
        time_in_force='day',
        limit_price=None,
        stop_price=None,
        take_profit=None,
        stop_loss_price=None,
    ):
        """
        Submit an order. Supports plain market/limit/stop and bracket orders.

        Bracket order: pass BOTH take_profit (limit price for the take-profit leg)
        AND stop_loss_price (stop trigger for the stop-loss leg). Bracket orders
        require time_in_force='gtc' or 'day' (gtc recommended so the protective
        legs persist across days after the parent fills).
        """
        data = {
            'symbol': symbol,
            'qty': str(qty),
            'side': side,
            'type': order_type,
            'time_in_force': time_in_force,
        }
        if limit_price is not None:
            data['limit_price'] = str(limit_price)
        if stop_price is not None:
            data['stop_price'] = str(stop_price)

        if take_profit is not None and stop_loss_price is not None:
            data['order_class'] = 'bracket'
            data['take_profit'] = {'limit_price': str(take_profit)}
            data['stop_loss'] = {'stop_price': str(stop_loss_price)}

        return self._post('/orders', data)

    def get_barset(self, symbols, timeframe='1D', limit=100):
        """Get historical bars for symbols"""
        if isinstance(symbols, list):
            # For multiple symbols, get bars for each individually
            result = {}
            for symbol in symbols:
                try:
                    bars = self._get(f'/stocks/{symbol}/bars?timeframe={timeframe}&limit={limit}')
                    result[symbol] = bars['bars'] if 'bars' in bars else []
                except (requests.RequestException, KeyError, ValueError):
                    result[symbol] = []
            return result
        else:
            # Single symbol
            bars = self._get(f'/stocks/{symbols}/bars?timeframe={timeframe}&limit={limit}')
            return {symbols: bars['bars'] if 'bars' in bars else []}

    def cancel_order(self, order_id):
        """Cancel an order by ID"""
        return self._delete(f'/orders/{order_id}')

    def close_position(self, symbol):
        """Close a position for a symbol"""
        return self._delete(f'/positions/{symbol}')
