import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Alpaca API Configuration
ALPACA_API_KEY = os.getenv('ALPACA_API_KEY')
ALPACA_API_SECRET = os.getenv('ALPACA_API_SECRET')
ALPACA_API_BASE_URL = os.getenv('ALPACA_API_BASE_URL')
ALPACA_ENVIRONMENT = os.getenv('ALPACA_ENVIRONMENT', 'paper')

# Claude AI Configuration
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
CLAUDE_MODEL = os.getenv('CLAUDE_MODEL', 'claude-sonnet-4-6')

# Email Configuration (Gmail SMTP by default — used by morning_routine.py / eod_routine.py)
# Generate an app password at https://myaccount.google.com/apppasswords
# (requires 2-Step Verification enabled). For Outlook.com, set EMAIL_SMTP_HOST=smtp-mail.outlook.com.
EMAIL_USER = os.getenv('EMAIL_USER')
EMAIL_APP_PASSWORD = os.getenv('EMAIL_APP_PASSWORD')
EMAIL_TO = os.getenv('EMAIL_TO', EMAIL_USER)
EMAIL_SMTP_HOST = os.getenv('EMAIL_SMTP_HOST', 'smtp.gmail.com')
EMAIL_SMTP_PORT = int(os.getenv('EMAIL_SMTP_PORT', '587'))

# Validate required credentials
if not ALPACA_API_KEY or not ALPACA_API_SECRET:
    raise ValueError("Missing required Alpaca API credentials in .env file")

# Trading Configuration
TRADING_CONFIG = {
    'api_key': ALPACA_API_KEY,
    'api_secret': ALPACA_API_SECRET,
    'base_url': ALPACA_API_BASE_URL,
    'environment': ALPACA_ENVIRONMENT,
}
