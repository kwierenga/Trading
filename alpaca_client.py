import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config import TRADING_CONFIG


# Alpaca splits its API into a trading host (paper-api.alpaca.markets / api.alpaca.markets)
# and a market-data host. /account, /positions, /orders live on the trading host (configured
# via ALPACA_API_BASE_URL); /stocks/{symbol}/quotes/latest etc. live on the data host below.
ALPACA_DATA_URL = "https://data.alpaca.markets/v2"

# Default per-request timeout (connect, read). Without this a hung socket blocks
# forever and the retry adapter never engages.
DEFAULT_TIMEOUT = 15

# Retry policy for transient failures (flaky runner mornings, brief Alpaca 5xx).
# CRITICAL: allowed_methods EXCLUDES POST. urllib3 only replays POST on *connection*
# errors (request never left the box → safe), never on read-timeout or 5xx status —
# so a submit_order that reached Alpaca but timed out on the response is NOT retried
# and cannot double-fire an order. GET/DELETE are idempotent and retry on everything.
# raise_on_status=False preserves existing semantics: the response is returned and the
# caller's raise_for_status() raises requests.HTTPError as before.
_RETRY = Retry(
    total=3,
    connect=3,
    read=2,
    status=2,
    backoff_factor=0.5,  # sleeps 0.5s, 1.0s, 2.0s between attempts
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET", "DELETE"}),
    raise_on_status=False,
)


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
        # Single Session reused across calls so the retry adapter (and connection
        # pooling) applies to every request on both the trading and data hosts.
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        adapter = HTTPAdapter(max_retries=_RETRY)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _get(self, endpoint):
        """Make GET request to Alpaca API"""
        url = f"{self.base_url}{endpoint}"
        response = self.session.get(url, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def _get_data(self, endpoint):
        """Make GET request to the Alpaca market-data API host."""
        url = f"{ALPACA_DATA_URL}{endpoint}"
        response = self.session.get(url, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint, data=None):
        """Make POST request to Alpaca API"""
        url = f"{self.base_url}{endpoint}"
        response = self.session.post(url, json=data, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def _delete(self, endpoint):
        """Make DELETE request to Alpaca API.

        Alpaca returns 204 No Content on successful cancel/close — no JSON body.
        Calling .json() on an empty body raises JSONDecodeError, which made
        successful cancellations look like failures. Treat empty bodies as
        an empty dict so callers can rely on a dict return.
        """
        url = f"{self.base_url}{endpoint}"
        response = self.session.delete(url, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return {}
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

    def get_open_orders_for_symbol(self, symbol):
        """
        Return the list of open orders for `symbol`. Used by position_manager
        to find the live take-profit and stop-loss legs of a filled bracket
        so they can be cancelled / re-submitted on scale-out and stop raises.
        """
        orders = self.get_orders(status='open')
        return [o for o in orders if (o.get('symbol') or '').upper() == symbol.upper()]

    def get_latest_quote(self, symbol):
        """
        Real-time bid/ask quote from the Alpaca market-data API.

        Returns a dict like {'bp': 195.32, 'ap': 195.35, 'bs': 100, 'as': 200,
        't': ISO-timestamp, ...} or None on any failure (caller falls back to
        yfinance). Used by re_evaluate.py at 09:35 ET, where yfinance prints
        can be 15-90 minutes stale.
        """
        try:
            data = self._get_data(f'/stocks/{symbol}/quotes/latest')
            return data.get('quote')
        except (requests.RequestException, ValueError, KeyError):
            return None
