"""
One-off: bulk-download Sharadar SF1 for the broad US universe.

Time-sensitive — must run while the Sharadar subscription is active. Saves to
a SEPARATE cache (sharadar_sf1_broad_2018_2025.pkl) so the S&P-500 cache is
untouched. Drops Nano/Micro by current scalemarketcap (can't pass the locked
$1B filter anyway) -> ~9,963 tickers incl. delisted (survivorship-safe set).
"""
from pathlib import Path
import pandas as pd
from sharadar_client import fetch_sf1_for_universe, CACHE_DIR

BROAD_SF1_CACHE = CACHE_DIR / "sharadar_sf1_broad_2018_2025.pkl"

tickers_meta = pd.read_pickle(CACHE_DIR / "sharadar_tickers.pkl")
us = tickers_meta[
    tickers_meta.category.isin(["Domestic Common Stock", "Domestic Common Stock Primary Class"])
    & tickers_meta.exchange.isin(["NASDAQ", "NYSE", "NYSEMKT"])
    & (tickers_meta.currency == "USD")
    & tickers_meta.scalemarketcap.isin(["3 - Small", "4 - Mid", "5 - Large", "6 - Mega"])
]
tickers = sorted(us.ticker.dropna().unique().tolist())
print(f"Broad universe candidate tickers: {len(tickers)} (incl. delisted)")

df = fetch_sf1_for_universe(tickers, "2018-01-01", "2025-12-31",
                            cache_path=BROAD_SF1_CACHE)
print(f"Done: {len(df):,} rows, {df['ticker'].nunique()} tickers with SF1 data")
print(f"Cached to {BROAD_SF1_CACHE}")
