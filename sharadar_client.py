"""
Sharadar SF1 client (REST, no library — Python 3.14 lacks nasdaqdatalink wheels).

Fetches point-in-time fundamentals + ticker metadata, caches to local parquet
so the bulk download runs once. Used by phase0_backtest_llm.py to build
historical AM-prompt snapshots without lookahead bias.

Sharadar SF1 ARQ dimension = as-reported quarterly. The `datekey` column is the
SEC filing date — the only field safe to use for point-in-time queries (do NOT
use `calendardate`, that's the period end and the filing comes weeks later).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional

import pandas as pd
import requests

CACHE_DIR = Path(os.environ.get("BACKTEST_CACHE_DIR", "backtest_cache"))
SF1_CACHE = CACHE_DIR / "sharadar_sf1_2018_2025.pkl"
TICKERS_CACHE = CACHE_DIR / "sharadar_tickers.pkl"

BASE_URL = "https://data.nasdaq.com/api/v3/datatables/SHARADAR"

# Sharadar caps per-page at 10000 rows. Pagination via cursor.
PAGE_SIZE = 10000
REQUEST_TIMEOUT_S = 60
RATE_LIMIT_SLEEP_S = 0.25


def _get_api_key() -> str:
    from dotenv import load_dotenv
    load_dotenv()
    key = os.getenv("NASDAQ_DATA_LINK_API_KEY")
    if not key:
        raise RuntimeError(
            "NASDAQ_DATA_LINK_API_KEY not set in .env. "
            "Sign up at data.nasdaq.com, subscribe to SF1, add the key."
        )
    return key


def _fetch_table(endpoint: str, **params) -> pd.DataFrame:
    """Fetch all rows from a Sharadar datatable, handling cursor pagination."""
    api_key = _get_api_key()
    url = f"{BASE_URL}/{endpoint}.json"

    all_rows: list = []
    cols: Optional[List[str]] = None
    cursor: Optional[str] = None

    while True:
        p = {"api_key": api_key, "qopts.per_page": PAGE_SIZE, **params}
        if cursor:
            p["qopts.cursor_id"] = cursor

        r = requests.get(url, params=p, timeout=REQUEST_TIMEOUT_S)
        r.raise_for_status()
        body = r.json()

        if cols is None:
            cols = [c["name"] for c in body["datatable"]["columns"]]
        all_rows.extend(body["datatable"]["data"])

        cursor = body.get("meta", {}).get("next_cursor_id")
        if not cursor:
            break
        time.sleep(RATE_LIMIT_SLEEP_S)

    return pd.DataFrame(all_rows, columns=cols)


def fetch_sf1_for_universe(tickers: List[str], start_date: str, end_date: str,
                            refresh: bool = False,
                            cache_path: "Optional[Path]" = None) -> pd.DataFrame:
    """Bulk-fetch SF1 ARQ rows for `tickers` with datekey in [start, end].

    `cache_path` lets callers keep separate caches (e.g. S&P-500 vs broad
    universe) so one download doesn't overwrite the other.
    """
    cache = cache_path if cache_path is not None else SF1_CACHE
    CACHE_DIR.mkdir(exist_ok=True)
    if cache.exists() and not refresh:
        df = pd.read_pickle(cache)
        print(f"  Loaded SF1 from cache: {len(df):,} rows, {df['ticker'].nunique()} tickers ({cache.name})")
        return df

    print(f"  Fetching SF1 for {len(tickers)} tickers, {start_date}..{end_date}")
    # URL length limits ~100 tickers per request comfortably.
    chunk_size = 100
    chunks = [tickers[i:i + chunk_size] for i in range(0, len(tickers), chunk_size)]
    pieces: List[pd.DataFrame] = []
    for i, chunk in enumerate(chunks, 1):
        ticker_csv = ",".join(chunk)
        df = _fetch_table(
            "SF1",
            dimension="ARQ",
            ticker=ticker_csv,
            **{"datekey.gte": start_date, "datekey.lte": end_date},
        )
        pieces.append(df)
        print(f"    chunk {i}/{len(chunks)}: +{len(df):,} rows")

    full = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    if not full.empty:
        full["datekey"] = pd.to_datetime(full["datekey"])
        full["calendardate"] = pd.to_datetime(full["calendardate"])
    full.to_pickle(cache)
    print(f"  Cached {len(full):,} rows to {cache.name}")
    return full


def fetch_tickers_metadata(refresh: bool = False) -> pd.DataFrame:
    """SHARADAR/TICKERS: name, sector, industry, listing status, etc."""
    CACHE_DIR.mkdir(exist_ok=True)
    if TICKERS_CACHE.exists() and not refresh:
        return pd.read_pickle(TICKERS_CACHE)
    df = _fetch_table("TICKERS", table="SF1")
    df.to_pickle(TICKERS_CACHE)
    print(f"  Cached {len(df):,} tickers metadata to {TICKERS_CACHE.name}")
    return df


def point_in_time_fundamentals(sf1: pd.DataFrame, ticker: str,
                                as_of: pd.Timestamp) -> Optional[pd.Series]:
    """Most-recent SF1 row for `ticker` filed strictly before `as_of`."""
    sub = sf1[(sf1["ticker"] == ticker) & (sf1["datekey"] < as_of)]
    if sub.empty:
        return None
    return sub.sort_values("datekey").iloc[-1]


if __name__ == "__main__":
    # Sanity test: fetch 3 tickers, 1 year, confirm point-in-time works.
    print("Sanity check: fetching AAPL + MSFT + GOOGL for 2024...")
    df = _fetch_table(
        "SF1",
        dimension="ARQ",
        ticker="AAPL,MSFT,GOOGL",
        **{"datekey.gte": "2024-01-01", "datekey.lte": "2024-12-31"},
    )
    print(f"  Got {len(df)} rows across {df['ticker'].nunique()} tickers")
    print(f"  Columns: {len(df.columns)} ({list(df.columns[:8])}...)")
    if len(df) > 0:
        sample = df.sort_values("datekey").iloc[0]
        print(f"  Earliest: {sample['ticker']} datekey={sample['datekey']} "
              f"calendardate={sample['calendardate']} marketcap=${sample.get('marketcap', 'n/a'):,.0f}")
