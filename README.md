# Alpaca Trading Application

A Python-based trading application for Alpaca Markets API.

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure API credentials:**
   - Edit `.env` file with your Alpaca API key and secret
   - Make sure `.env` is in your `.gitignore`

3. **Run the application:**
   ```bash
   python main.py
   ```

## Features

- View account information (equity, buying power, cash)
- Display current positions
- Show recent orders
- Submit new orders
- Cancel orders
- Close positions
- Get historical market data

## Scripts Available

### `main.py` - Basic Account Dashboard
Shows your account status, positions, and recent orders.

### `trading_bot.py` - Automated Trading
- Simple price-based trading strategy
- Real-time market monitoring
- Portfolio analysis

### `data_analyzer.py` - Data Analysis & Export
- Save historical market data to JSON
- Analyze price movements
- Export portfolio data to CSV

## API Credentials

**⚠️ IMPORTANT:** Make sure your API credentials in `.env` are valid and current.

## File Structure

- `config.py` - Configuration management
- `alpaca_client.py` - Alpaca API client wrapper
- `main.py` - Basic account dashboard
- `trading_bot.py` - Automated trading features
- `data_analyzer.py` - Data analysis and export tools
- `requirements.txt` - Python dependencies
- `.env` - Environment variables (API credentials)
- `.gitignore` - Git ignore rules

## Usage Examples

```python
from alpaca_client import AlpacaClient

client = AlpacaClient()

# Get account info
account = client.get_account()

# Get positions
positions = client.get_positions()

# Submit an order
order = client.submit_order('AAPL', 10, 'buy')

# Get historical data
bars = client.get_barset(['AAPL'], '1D', limit=100)
```

## Next Steps

- **Backtesting:** Add historical data analysis for strategy testing
- **Risk Management:** Implement stop-loss and position sizing
- **Real-time Alerts:** Add notifications for price movements
- **Web Dashboard:** Create a web interface with Flask/Django
- **Advanced Strategies:** Implement technical indicators and ML models