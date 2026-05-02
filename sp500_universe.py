"""
S&P 500 Universe Loader
Maintains a cached list of S&P 500 tickers. Fetches the live list from a public
GitHub-hosted CSV on first run; falls back to a hardcoded snapshot if the fetch
fails. Cache is refreshed weekly.
"""

import csv
import io
import json
import os
import time
from typing import List

import requests

CACHE_FILE = "sp500_constituents_cache.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600  # weekly refresh
SOURCE_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"

# Hardcoded fallback — top ~150 S&P 500 names by market cap (stable, liquid).
# Used only if the live fetch fails on first run with no existing cache.
FALLBACK_TICKERS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "BRK-B", "LLY", "AVGO",
    "TSLA", "JPM", "WMT", "XOM", "UNH", "V", "MA", "PG", "JNJ", "HD",
    "ORCL", "COST", "ABBV", "BAC", "KO", "MRK", "CVX", "ADBE", "NFLX", "PEP",
    "CRM", "TMO", "AMD", "LIN", "ACN", "MCD", "CSCO", "ABT", "DHR", "WFC",
    "TXN", "DIS", "VZ", "INTU", "PM", "QCOM", "GE", "NOW", "IBM", "UNP",
    "AMGN", "CAT", "AXP", "ISRG", "PFE", "MS", "RTX", "GS", "T", "SPGI",
    "NEE", "BKNG", "PGR", "LOW", "BLK", "HON", "ETN", "TJX", "C", "ELV",
    "VRTX", "BSX", "SYK", "MDLZ", "DE", "ADP", "PLD", "MMC", "GILD", "ADI",
    "LMT", "REGN", "CB", "BMY", "TMUS", "MO", "AMT", "FI", "SCHW", "PANW",
    "ZTS", "SO", "DUK", "BDX", "SHW", "CI", "ICE", "USB", "EQIX", "CME",
    "AON", "PNC", "MU", "ITW", "KLAC", "WM", "EOG", "NOC", "CL", "TGT",
    "CSX", "GD", "MCK", "FCX", "FDX", "MAR", "APD", "EMR", "PYPL", "ORLY",
    "PSX", "CDNS", "TFC", "ROP", "MMM", "NSC", "PH", "MPC", "MNST", "AJG",
    "CMG", "OXY", "TT", "AZO", "FTNT", "MSI", "SLB", "AFL", "ECL", "ADSK",
    "CARR", "PCAR", "HCA", "TDG", "WELL", "PSA", "ALL", "NXPI", "TRV", "NEM",
]


def _load_cache() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except OSError as e:
        print(f"  Warning: could not write {CACHE_FILE}: {e}")


def _fetch_live() -> List[str]:
    """Fetch current S&P 500 constituents from the public GitHub dataset."""
    resp = requests.get(SOURCE_URL, timeout=10)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    tickers = []
    for row in reader:
        sym = (row.get("Symbol") or "").strip().upper()
        if sym:
            # yfinance uses '-' for share classes (BRK-B), not '.' (BRK.B)
            tickers.append(sym.replace(".", "-"))
    return tickers


def get_sp500_tickers(force_refresh: bool = False) -> List[str]:
    """
    Returns the current S&P 500 ticker list. Cached weekly.
    Falls back to a hardcoded subset if the live fetch fails on first run.
    """
    cache = _load_cache()
    age = time.time() - cache.get("fetched_at", 0)

    if not force_refresh and cache.get("tickers") and age < CACHE_TTL_SECONDS:
        return cache["tickers"]

    try:
        tickers = _fetch_live()
        if len(tickers) < 400:
            raise ValueError(f"suspicious ticker count from source: {len(tickers)}")
        _save_cache({"fetched_at": time.time(), "tickers": tickers, "source": SOURCE_URL})
        return tickers
    except (requests.RequestException, ValueError) as e:
        print(f"  S&P 500 live fetch failed ({e}); using cached or fallback list.")
        if cache.get("tickers"):
            return cache["tickers"]
        return list(FALLBACK_TICKERS)


if __name__ == "__main__":
    import sys
    force = "--refresh" in sys.argv
    tickers = get_sp500_tickers(force_refresh=force)
    print(f"Loaded {len(tickers)} S&P 500 tickers")
    print(f"First 10: {tickers[:10]}")
    print(f"Last 10:  {tickers[-10:]}")
