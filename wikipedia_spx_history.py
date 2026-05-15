"""
Historical S&P 500 constituent membership from Wikipedia.

Standard quant practice for survivorship-safe backtest universe construction.
Wikipedia maintains two tables on the "List of S&P 500 companies" page:
  1. Current constituents (today's members)
  2. Selected changes (add/remove history, dated)

We reconstruct membership on any past date by starting with today's set and
walking the change log backward: anything ADDED after our target date gets
removed from the set; anything REMOVED after our target date gets added back.

Caches to parquet locally. Refresh once a month or before a backtest run.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import List, Set, Tuple

import pandas as pd
import requests

# Wikipedia rejects pandas' default urllib User-Agent (403). A real UA fixes it.
WIKIPEDIA_UA = "Mozilla/5.0 (compatible; TradingBacktest/1.0; +research)"

CACHE_DIR = Path(os.environ.get("BACKTEST_CACHE_DIR", "backtest_cache"))
CURRENT_CACHE = CACHE_DIR / "spx_current.pkl"
CHANGES_CACHE = CACHE_DIR / "spx_changes.pkl"

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def _flatten_columns(cols) -> List[str]:
    """Wikipedia returns multi-level headers; collapse to single strings."""
    if not isinstance(cols, pd.MultiIndex):
        return [str(c) for c in cols]
    out = []
    for tup in cols:
        parts = [str(c) for c in tup if str(c) != "nan" and not str(c).startswith("Unnamed")]
        out.append("_".join(parts) if parts else "col")
    return out


def refresh_cache() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Scrape Wikipedia, normalize columns, write parquet. Returns (current, changes)."""
    CACHE_DIR.mkdir(exist_ok=True)
    print(f"  Scraping {WIKIPEDIA_URL}")
    resp = requests.get(WIKIPEDIA_URL, headers={"User-Agent": WIKIPEDIA_UA}, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    if len(tables) < 2:
        raise RuntimeError(f"Expected >=2 tables, got {len(tables)} — Wikipedia layout changed?")

    current = tables[0].copy()
    current.columns = _flatten_columns(current.columns)
    current.to_pickle(CURRENT_CACHE)

    changes = tables[1].copy()
    changes.columns = _flatten_columns(changes.columns)
    # Parse the date column — Wikipedia's header is often "Effective Date_..."
    date_col = next((c for c in changes.columns if "date" in c.lower()), None)
    if date_col is None:
        raise RuntimeError(f"No date column found in changes table. Columns: {list(changes.columns)}")
    changes[date_col] = pd.to_datetime(changes[date_col], errors="coerce")
    changes = changes.dropna(subset=[date_col]).rename(columns={date_col: "Date"})
    changes.to_pickle(CHANGES_CACHE)

    print(f"  Cached: {len(current)} current constituents, {len(changes)} historical changes")
    return current, changes


def _load() -> Tuple[pd.DataFrame, pd.DataFrame]:
    if not CURRENT_CACHE.exists() or not CHANGES_CACHE.exists():
        return refresh_cache()
    return pd.read_pickle(CURRENT_CACHE), pd.read_pickle(CHANGES_CACHE)


def _ticker_column(df: pd.DataFrame, kind: str) -> str:
    """Find the column holding `kind` ticker — 'current', 'added', or 'removed'."""
    lc = {c.lower(): c for c in df.columns}
    if kind == "current":
        for k in ("symbol", "ticker", "ticker symbol"):
            if k in lc:
                return lc[k]
    elif kind == "added":
        for k in lc:
            if "added" in k and ("ticker" in k or "symbol" in k):
                return lc[k]
    elif kind == "removed":
        for k in lc:
            if "removed" in k and ("ticker" in k or "symbol" in k):
                return lc[k]
    raise RuntimeError(f"Cannot find {kind} ticker column among: {list(df.columns)}")


def members_on(target_date: str | pd.Timestamp) -> Set[str]:
    """Set of S&P 500 tickers on `target_date` (survivorship-safe).

    Algorithm: start from today's members, walk changes backward.
      - Change rows with Date > target_date represent membership transitions
        that happened AFTER target_date. To reconstruct target_date state:
          - Ticker that was Added after target_date → wasn't a member then → REMOVE
          - Ticker that was Removed after target_date → was a member then → ADD back
    """
    target = pd.to_datetime(target_date)
    current, changes = _load()

    cur_col = _ticker_column(current, "current")
    members: Set[str] = set(current[cur_col].dropna().astype(str).str.strip().tolist())

    add_col = _ticker_column(changes, "added")
    rem_col = _ticker_column(changes, "removed")

    future = changes[changes["Date"] > target].copy()
    for _, row in future.iterrows():
        added = str(row.get(add_col, "")).strip()
        removed = str(row.get(rem_col, "")).strip()
        if added and added.lower() not in ("nan", ""):
            members.discard(added)
        if removed and removed.lower() not in ("nan", ""):
            members.add(removed)

    return members


def was_in_sp500(ticker: str, date: str | pd.Timestamp) -> bool:
    return ticker in members_on(date)


if __name__ == "__main__":
    print("Refreshing Wikipedia SPX history cache...")
    current, changes = refresh_cache()

    print(f"\nCurrent members: {len(current)}")
    print(f"Changes table columns: {list(changes.columns)}")

    test_dates = ["2025-01-01", "2022-01-01", "2019-06-01"]
    for d in test_dates:
        m = members_on(d)
        print(f"\nMembers on {d}: {len(m)} tickers")
        sample = sorted(m)[:10]
        print(f"  Sample (alphabetical): {sample}")

    # Quick sanity: META was FB before 2022-06; check both ways.
    print("\nSanity checks:")
    print(f"  FB in members on 2021-01-01? {was_in_sp500('FB', '2021-01-01')}")
    print(f"  META in members on 2021-01-01? {was_in_sp500('META', '2021-01-01')}")
    print(f"  META in members on 2025-01-01? {was_in_sp500('META', '2025-01-01')}")
