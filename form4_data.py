"""
SEC EDGAR Form 4 ingestion — fetches recent open-market insider purchases and
applies the mandatory amendment-dedup pass (Form 4 spec §8.2 / §15.1).

Pipeline:
  1. Fetch (and cache) the ticker→CIK map from sec.gov.
  2. For each ticker, fetch submissions JSON; filter for Form 4/4A in window.
  3. Fetch each filing's XML; parse non-derivative P-transactions, role, 10b5-1.
  4. Apply dedup_amendments per spec §8.2.

Returns deduped transactions; cluster filter (≥2 insiders, key role, $ floor,
no 10b5-1, etc.) lives in form4_signals.py.

Rate limit: SEC fair use is 10 req/sec. We sleep ~120ms between requests.
A descriptive User-Agent is mandatory per SEC policy.
"""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests


SEC_USER_AGENT = "form4-cluster-trader research klaaswierenga@gmail.com"
SEC_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
SEC_REQUEST_DELAY_S = 0.12   # ~8 req/sec, comfortable margin under 10/s
SEC_TIMEOUT_S = 15

CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
CIK_MAP_CACHE = "sec_company_tickers_cache.json"
CIK_MAP_TTL_S = 7 * 24 * 3600   # weekly refresh

SUBMISSIONS_URL_TPL = "https://data.sec.gov/submissions/CIK{cik10}.json"
FILING_DOC_URL_TPL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{primary}"

# Per-filing XML cache. Form 4 documents are immutable once filed (accession
# numbers are permanent), so cached files never need invalidation — only
# eviction by age via prune_form4_cache().
FORM4_XML_CACHE_DIR = "form4_xml_cache"

KEY_ROLES = {"CEO", "CFO", "COO", "President", "Chairman"}


@dataclass
class Form4Transaction:
    accession: str
    is_amendment: bool
    filing_date: str             # YYYY-MM-DD
    transaction_date: str        # YYYY-MM-DD
    issuer_cik: str
    issuer_name: str
    issuer_ticker: str
    reporting_owner_cik: str
    reporting_owner_name: str
    insider_role: str            # CEO/CFO/COO/President/Chairman/VP/Officer/Director/10%Owner/Other
    transaction_code: str        # "P" or "S" — others filtered out at parse
    shares: float
    price_per_share: float
    transaction_value: float
    rule_10b5_1_flag: bool


# ---------------------------------------------------------------------------
# Throttled HTTP
# ---------------------------------------------------------------------------

_last_request_at = 0.0


def _throttled_get(url: str) -> requests.Response:
    global _last_request_at
    elapsed = time.time() - _last_request_at
    if elapsed < SEC_REQUEST_DELAY_S:
        time.sleep(SEC_REQUEST_DELAY_S - elapsed)
    resp = requests.get(url, headers=SEC_HEADERS, timeout=SEC_TIMEOUT_S)
    _last_request_at = time.time()
    return resp


# ---------------------------------------------------------------------------
# Ticker → CIK map
# ---------------------------------------------------------------------------

def fetch_cik_map(force_refresh: bool = False) -> Dict[str, str]:
    """
    Returns {TICKER: CIK_zero_padded_to_10}. Cached weekly.
    SEC's company_tickers.json normalizes share classes by removing separators
    (e.g., 'BRKB', not 'BRK-B'), so callers should try a few variants.
    """
    if not force_refresh and os.path.exists(CIK_MAP_CACHE):
        try:
            with open(CIK_MAP_CACHE, "r") as f:
                cached = json.load(f)
            if time.time() - cached.get("fetched_at", 0) < CIK_MAP_TTL_S:
                return cached["map"]
        except (OSError, json.JSONDecodeError):
            pass

    resp = _throttled_get(CIK_MAP_URL)
    resp.raise_for_status()
    data = resp.json()

    out: Dict[str, str] = {}
    for v in data.values():
        ticker = (v.get("ticker") or "").upper().strip()
        cik = v.get("cik_str")
        if ticker and cik is not None:
            out[ticker] = f"{int(cik):010d}"

    try:
        with open(CIK_MAP_CACHE, "w") as f:
            json.dump({"fetched_at": time.time(), "map": out}, f)
    except OSError as e:
        print(f"  Warning: could not write {CIK_MAP_CACHE}: {e}")

    return out


def lookup_cik(ticker: str, cik_map: Dict[str, str]) -> Optional[str]:
    """Try ticker as-is, then with separators stripped/dotted (BRK-B variants)."""
    candidates = [
        ticker.upper(),
        ticker.replace("-", "").upper(),
        ticker.replace("-", ".").upper(),
        ticker.replace(".", "").upper(),
    ]
    for c in candidates:
        if c in cik_map:
            return cik_map[c]
    return None


# ---------------------------------------------------------------------------
# Filings discovery
# ---------------------------------------------------------------------------

def fetch_recent_form4_filings(cik10: str, since_date: str) -> List[Tuple[str, str, str, bool]]:
    """
    Returns [(accession, filing_date, primary_document, is_amendment)] for
    Form 4 / 4/A filings dated >= since_date (YYYY-MM-DD, lex compare works).
    """
    url = SUBMISSIONS_URL_TPL.format(cik10=cik10)
    try:
        resp = _throttled_get(url)
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primaries = recent.get("primaryDocument", [])

    out = []
    for f, a, d, p in zip(forms, accessions, dates, primaries):
        if f not in ("4", "4/A"):
            continue
        if d < since_date:
            continue
        out.append((a, d, p, f == "4/A"))
    return out


# ---------------------------------------------------------------------------
# Form 4 XML parsing
# ---------------------------------------------------------------------------

def _xml_cache_path(accession: str) -> str:
    safe = accession.replace("/", "_").replace("\\", "_")
    return os.path.join(FORM4_XML_CACHE_DIR, f"{safe}.xml")


def _read_cached_xml(accession: str) -> Optional[str]:
    path = _xml_cache_path(accession)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return content or None
    except OSError:
        return None


def _write_cached_xml(accession: str, xml_text: str) -> None:
    if not xml_text:
        return
    try:
        os.makedirs(FORM4_XML_CACHE_DIR, exist_ok=True)
        with open(_xml_cache_path(accession), "w", encoding="utf-8") as f:
            f.write(xml_text)
    except OSError as e:
        # A cache write failure must not break ingestion.
        print(f"  Warning: could not cache XML for {accession}: {e}")


def fetch_form4_xml(cik10: str, accession: str, primary_doc: str) -> Optional[str]:
    cached = _read_cached_xml(accession)
    if cached is not None:
        return cached

    cik_no_pad = str(int(cik10))
    acc_nodash = accession.replace("-", "")
    url = FILING_DOC_URL_TPL.format(cik=cik_no_pad, acc_nodash=acc_nodash, primary=primary_doc)
    try:
        resp = _throttled_get(url)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None

    _write_cached_xml(accession, resp.text)
    return resp.text


def prune_form4_cache(max_age_days: int = 60) -> int:
    """
    Drop cached XML files older than max_age_days. Returns count removed.

    The 30-day signal window means anything older than ~60 days is dead weight.
    Run periodically (manually or wired into the orchestrator later) to keep
    the cache from growing unbounded.
    """
    if not os.path.isdir(FORM4_XML_CACHE_DIR):
        return 0
    cutoff = time.time() - (max_age_days * 24 * 3600)
    removed = 0
    for fname in os.listdir(FORM4_XML_CACHE_DIR):
        path = os.path.join(FORM4_XML_CACHE_DIR, fname)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed


def _xml_text(elem: Optional[ET.Element], path: Optional[str] = None, default: str = "") -> str:
    if elem is None:
        return default
    target = elem if path is None else elem.find(path)
    if target is None or target.text is None:
        return default
    return target.text.strip()


def _xml_value(elem: Optional[ET.Element], path: str, default: str = "") -> str:
    """Form 4 wraps many fields in an inner <value> element."""
    if elem is None:
        return default
    target = elem.find(path)
    if target is None:
        return default
    val = target.find("value")
    if val is not None and val.text is not None:
        return val.text.strip()
    return target.text.strip() if target.text else default


def _classify_role(rel: ET.Element) -> str:
    """Map officerTitle + boolean relationship flags to a role bucket."""
    title = _xml_text(rel, "officerTitle", "").upper()
    is_director = _xml_text(rel, "isDirector", "0") in ("1", "true", "True")
    is_officer = _xml_text(rel, "isOfficer", "0") in ("1", "true", "True")
    is_10pct = _xml_text(rel, "isTenPercentOwner", "0") in ("1", "true", "True")

    if "CEO" in title or "CHIEF EXECUTIVE" in title:
        return "CEO"
    if "CFO" in title or "CHIEF FINANCIAL" in title:
        return "CFO"
    if "COO" in title or "CHIEF OPERATING" in title:
        return "COO"
    if "PRESIDENT" in title and "VICE" not in title:
        return "President"
    if "CHAIR" in title:
        return "Chairman"
    if "VP" in title or "VICE PRESIDENT" in title:
        return "VP"
    if is_officer:
        return "Officer"
    if is_director:
        return "Director"
    if is_10pct:
        return "10%Owner"
    return "Other"


def parse_form4_xml(
    xml_text: str,
    accession: str,
    filing_date: str,
    is_amendment: bool,
) -> List[Form4Transaction]:
    """
    Parse one Form 4 filing's XML. Returns one Form4Transaction per non-derivative
    P (purchase) or S (sale) transaction. P generates signals (spec §3); S is kept
    only for the penalty filter "no S by same insider within 7 days of P" (spec §3
    bullet 6). Other transaction codes (M/G/I/F/derivatives) are dropped per §8.3.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # Spec §3 / Feb 2023 SEC rule: detect 10b5-1 plan flag. Filers serialize this
    # inconsistently across schema versions, so we scan the raw XML for any 10b5
    # mention. Conservative: false positives mean we drop a few valid signals,
    # never the reverse.
    rule_10b5_1 = bool(re.search(r"10b5[\-_]1", xml_text, re.IGNORECASE))

    issuer_el = root.find("issuer")
    issuer_cik = _xml_text(issuer_el, "issuerCik")
    issuer_name = _xml_text(issuer_el, "issuerName")
    issuer_ticker = _xml_text(issuer_el, "issuerTradingSymbol").upper()

    owners = root.findall("reportingOwner")
    if not owners:
        return []

    nd_table = root.find("nonDerivativeTable")
    if nd_table is None:
        return []

    out: List[Form4Transaction] = []
    for owner_el in owners:
        owner_id = owner_el.find("reportingOwnerId")
        rel_el = owner_el.find("reportingOwnerRelationship")
        owner_cik = _xml_text(owner_id, "rptOwnerCik")
        owner_name = _xml_text(owner_id, "rptOwnerName")
        role = _classify_role(rel_el) if rel_el is not None else "Other"

        for txn in nd_table.findall("nonDerivativeTransaction"):
            coding = txn.find("transactionCoding")
            code = _xml_text(coding, "transactionCode")
            if code not in ("P", "S"):
                continue

            tdate = _xml_value(txn, "transactionDate")
            amounts = txn.find("transactionAmounts")
            try:
                shares = float(_xml_value(amounts, "transactionShares") or "0")
                price = float(_xml_value(amounts, "transactionPricePerShare") or "0")
            except ValueError:
                continue
            if shares <= 0 or price <= 0:
                continue

            out.append(Form4Transaction(
                accession=accession,
                is_amendment=is_amendment,
                filing_date=filing_date,
                transaction_date=tdate,
                issuer_cik=issuer_cik,
                issuer_name=issuer_name,
                issuer_ticker=issuer_ticker,
                reporting_owner_cik=owner_cik,
                reporting_owner_name=owner_name,
                insider_role=role,
                transaction_code=code,
                shares=shares,
                price_per_share=price,
                transaction_value=shares * price,
                rule_10b5_1_flag=rule_10b5_1,
            ))
    return out


# ---------------------------------------------------------------------------
# Mandatory amendment dedup (spec §8.2 / §15.1)
# ---------------------------------------------------------------------------

def dedup_amendments(transactions: List[Form4Transaction]) -> List[Form4Transaction]:
    """
    Spec §8.2: group on (owner CIK, issuer CIK, transaction date, shares,
    transaction code), keep only the row with the latest filing date in each
    group. Code is included in the key so an insider's same-day P and S of
    equal share count don't collide (rare but possible).

    Form 4/A amendments routinely re-file the same economic transaction. Without
    this pass cluster sizes inflate (one insider's single trade looks like 2-3
    insiders), signal counts inflate, and backtest returns become unreplicable
    in live trading. Mandatory before any signal logic.
    """
    groups: Dict[Tuple[str, str, str, float, str], Form4Transaction] = {}
    for t in transactions:
        key = (
            t.reporting_owner_cik, t.issuer_cik,
            t.transaction_date, t.shares, t.transaction_code,
        )
        existing = groups.get(key)
        if existing is None or t.filing_date > existing.filing_date:
            groups[key] = t
    return list(groups.values())


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def fetch_form4_for_universe(
    tickers: List[str],
    since_days: int = 30,
    verbose: bool = True,
) -> List[Form4Transaction]:
    """
    Fetch Form 4 P+S transactions for a list of tickers over the last `since_days`.
    Returns deduped transactions (both purchases and sales — S kept for the
    proximity penalty filter in signals). Does NOT apply cluster filter — caller
    does that via form4_signals.detect_clusters.
    """
    cik_map = fetch_cik_map()
    since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%d")

    raw: List[Form4Transaction] = []
    cache_hits = 0
    network_fetches = 0
    n = len(tickers)
    progress_step = max(1, n // 20)

    for i, ticker in enumerate(tickers):
        if verbose and i % progress_step == 0:
            print(
                f"  Form 4 fetch {i}/{n}  |  raw P+S txns: {len(raw)}  |  "
                f"cache hit: {cache_hits}  net: {network_fetches}"
            )

        cik10 = lookup_cik(ticker, cik_map)
        if cik10 is None:
            continue

        filings = fetch_recent_form4_filings(cik10, since_date)
        for accession, filing_date, primary, is_amend in filings:
            was_cached = _read_cached_xml(accession) is not None
            xml_text = fetch_form4_xml(cik10, accession, primary)
            if xml_text is None:
                continue
            if was_cached:
                cache_hits += 1
            else:
                network_fetches += 1
            raw.extend(parse_form4_xml(xml_text, accession, filing_date, is_amend))

    deduped = dedup_amendments(raw)
    if verbose:
        n_p = sum(1 for t in deduped if t.transaction_code == "P")
        n_s = sum(1 for t in deduped if t.transaction_code == "S")
        print(
            f"\n  Form 4 ingest complete: {len(raw)} raw → {len(deduped)} deduped "
            f"({n_p} P, {n_s} S)  |  cache hits: {cache_hits}, network fetches: {network_fetches}"
        )
    return deduped


def format_transaction(t: Form4Transaction) -> str:
    flag = " [10b5-1]" if t.rule_10b5_1_flag else ""
    amend = " [AMEND]" if t.is_amendment else ""
    return (
        f"  {t.transaction_date}  {t.issuer_ticker:6s}  [{t.transaction_code}] "
        f"{t.insider_role:10s}  {t.reporting_owner_name[:30]:30s}  "
        f"{t.shares:>10,.0f} sh @ ${t.price_per_share:>8.2f}  "
        f"= ${t.transaction_value:>12,.0f}{flag}{amend}"
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python form4_data.py <TICKER> [<TICKER> ...]   # fetch + dedup last 30 days")
        print("  python form4_data.py --days 14 <TICKER>        # custom window")
        sys.exit(1)

    days = 30
    args = sys.argv[1:]
    if "--days" in args:
        i = args.index("--days")
        days = int(args[i + 1])
        args = args[:i] + args[i + 2:]

    tickers = [a.upper() for a in args]
    print(f"Fetching Form 4 P+S transactions for {len(tickers)} ticker(s), last {days} days...")
    txns = fetch_form4_for_universe(tickers, since_days=days)
    if not txns:
        print("\nNo P/S transactions found in window.")
    else:
        print(f"\n{len(txns)} deduped transaction(s):\n")
        for t in sorted(txns, key=lambda x: (x.issuer_ticker, x.transaction_date)):
            print(format_transaction(t))
