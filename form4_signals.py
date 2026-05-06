"""
Form 4 cluster signal detection.

Applies spec §3 cluster filter to deduped Form 4 transactions and computes the
spec §3.1 diagnostic score for each qualifying signal. The score is for logging
only — sizing in v1.2 is driven by cluster size tiers, not score.

Signal generation requires (spec §3):
  1. ≥2 distinct insiders filed P transactions within 30 days for same issuer
  2. ≥1 insider holds a key role (CEO/CFO/COO/President/Chairman)
  3. Aggregate cluster value ≥ $100,000
  4. No individual transaction < $25,000
  5. No 10b5-1 Form 4 from issuer within window
  6. No S transactions by same insiders within 7 days of any P
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from form4_data import Form4Transaction, KEY_ROLES


# Spec §3 thresholds — single source of truth per spec §15.4.
CLUSTER_MIN_INSIDERS = 2
CLUSTER_MIN_AGGREGATE_USD = 100_000
CLUSTER_MIN_INDIVIDUAL_USD = 25_000
S_PROXIMITY_DAYS = 7
WINDOW_DAYS = 30


@dataclass
class ClusterSignal:
    issuer_ticker: str
    issuer_cik: str
    issuer_name: str
    n_insiders: int
    n_transactions: int
    aggregate_value: float
    has_key_role: bool
    insider_roles: List[str]
    insider_names: List[str]
    first_purchase_date: str
    last_purchase_date: str
    days_clustered: int
    diagnostic_score: int
    diagnostic_score_breakdown: Dict[str, int]
    transactions: List[Form4Transaction] = field(default_factory=list)


def _diagnostic_score(
    cluster_p_txns: List[Form4Transaction],
    aggregate_value: float,
    market_cap: Optional[float],
) -> Dict[str, int]:
    """Spec §3.1 — logged only, does not drive sizing in v1.2."""
    breakdown: Dict[str, int] = {}

    role_score = 0
    for t in cluster_p_txns:
        r = t.insider_role
        if r in ("CEO", "CFO", "Chairman"):
            role_score += 3
        elif r in ("COO", "President"):
            role_score += 2
        elif r in ("Director", "VP"):
            role_score += 1
    breakdown["role"] = role_score

    n_insiders = len({t.reporting_owner_cik for t in cluster_p_txns})
    if n_insiders >= 4:
        breakdown["cluster_size"] = 5
    elif n_insiders == 3:
        breakdown["cluster_size"] = 3
    elif n_insiders == 2:
        breakdown["cluster_size"] = 1
    else:
        breakdown["cluster_size"] = 0

    if market_cap and market_cap > 0:
        ratio = aggregate_value / market_cap
        if ratio >= 0.0010:
            breakdown["dollar_significance"] = 5
        elif ratio >= 0.0005:
            breakdown["dollar_significance"] = 3
        elif ratio >= 0.0001:
            breakdown["dollar_significance"] = 2
        else:
            breakdown["dollar_significance"] = 1
    else:
        breakdown["dollar_significance"] = 0

    dates = sorted({t.transaction_date for t in cluster_p_txns})
    if len(dates) >= 2:
        try:
            first = datetime.strptime(dates[0], "%Y-%m-%d").date()
            last = datetime.strptime(dates[-1], "%Y-%m-%d").date()
            span = (last - first).days
            if span <= 7:
                breakdown["temporal"] = 2
            elif span <= 14:
                breakdown["temporal"] = 1
            else:
                breakdown["temporal"] = 0
        except ValueError:
            breakdown["temporal"] = 0
    else:
        breakdown["temporal"] = 0

    return breakdown


def _has_recent_sale(
    p_txns: List[Form4Transaction],
    s_txns: List[Form4Transaction],
    window_days: int = S_PROXIMITY_DAYS,
) -> bool:
    """Spec §3 bullet 6: S by same insider within ±window days of any of their P."""
    p_by_owner = defaultdict(list)
    for t in p_txns:
        p_by_owner[t.reporting_owner_cik].append(t.transaction_date)

    for s in s_txns:
        owner_ps = p_by_owner.get(s.reporting_owner_cik)
        if not owner_ps:
            continue
        try:
            s_date = datetime.strptime(s.transaction_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        for p_date_str in owner_ps:
            try:
                p_date = datetime.strptime(p_date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if abs((s_date - p_date).days) <= window_days:
                return True
    return False


def detect_clusters(
    transactions: List[Form4Transaction],
    market_caps: Optional[Dict[str, float]] = None,
) -> List[ClusterSignal]:
    """
    Apply spec §3 filter to deduped P+S transactions; return qualifying clusters.

    `market_caps` is optional {ticker: market_cap_usd}. If absent, the dollar-
    significance score component is set to 0; the cluster still qualifies on the
    spec §3 hard rules (which don't depend on market cap).
    """
    market_caps = market_caps or {}

    by_issuer: Dict[str, List[Form4Transaction]] = defaultdict(list)
    for t in transactions:
        by_issuer[t.issuer_cik].append(t)

    signals: List[ClusterSignal] = []
    for issuer_cik, txns in by_issuer.items():
        # Bullet 5: any 10b5-1 Form 4 from issuer in window disqualifies entire cluster
        if any(t.rule_10b5_1_flag for t in txns):
            continue

        p_txns = [t for t in txns if t.transaction_code == "P"]
        s_txns = [t for t in txns if t.transaction_code == "S"]
        if not p_txns:
            continue

        # Bullet 4: no individual P below $25k
        if any(t.transaction_value < CLUSTER_MIN_INDIVIDUAL_USD for t in p_txns):
            continue

        # Bullet 1: ≥2 distinct insiders
        unique_insiders = {t.reporting_owner_cik for t in p_txns}
        if len(unique_insiders) < CLUSTER_MIN_INSIDERS:
            continue

        # Bullet 2: ≥1 key role
        roles_in_cluster = {t.insider_role for t in p_txns}
        if not (KEY_ROLES & roles_in_cluster):
            continue

        # Bullet 3: aggregate ≥ $100k
        aggregate = sum(t.transaction_value for t in p_txns)
        if aggregate < CLUSTER_MIN_AGGREGATE_USD:
            continue

        # Bullet 6: no S by same insider within 7 days of any P
        if _has_recent_sale(p_txns, s_txns):
            continue

        ticker = p_txns[0].issuer_ticker
        market_cap = market_caps.get(ticker)
        breakdown = _diagnostic_score(p_txns, aggregate, market_cap)
        score = sum(breakdown.values())

        # First-seen row per insider for display
        owner_seen: Dict[str, Form4Transaction] = {}
        for t in p_txns:
            owner_seen.setdefault(t.reporting_owner_cik, t)
        owners_ordered = list(owner_seen.values())

        dates = sorted(t.transaction_date for t in p_txns)
        first, last = dates[0], dates[-1]
        try:
            span = (datetime.strptime(last, "%Y-%m-%d").date()
                    - datetime.strptime(first, "%Y-%m-%d").date()).days
        except ValueError:
            span = 0

        signals.append(ClusterSignal(
            issuer_ticker=ticker,
            issuer_cik=issuer_cik,
            issuer_name=p_txns[0].issuer_name,
            n_insiders=len(unique_insiders),
            n_transactions=len(p_txns),
            aggregate_value=aggregate,
            has_key_role=True,
            insider_roles=[t.insider_role for t in owners_ordered],
            insider_names=[t.reporting_owner_name for t in owners_ordered],
            first_purchase_date=first,
            last_purchase_date=last,
            days_clustered=span,
            diagnostic_score=score,
            diagnostic_score_breakdown=breakdown,
            transactions=p_txns,
        ))

    signals.sort(key=lambda s: (-s.n_insiders, -s.aggregate_value))
    return signals


def format_cluster(s: ClusterSignal) -> str:
    return "\n".join([
        f"  {s.issuer_ticker:6s}  ({s.issuer_name[:50]})",
        f"    {s.n_insiders} insiders, {s.n_transactions} P-txns, ${s.aggregate_value:,.0f} aggregate",
        f"    Roles: {', '.join(s.insider_roles)}",
        f"    Window: {s.first_purchase_date} → {s.last_purchase_date} ({s.days_clustered}d span)",
        f"    Diagnostic score: {s.diagnostic_score}  {s.diagnostic_score_breakdown}",
    ])


if __name__ == "__main__":
    import sys
    from form4_data import fetch_form4_for_universe

    if len(sys.argv) < 2:
        print("Usage: python form4_signals.py <TICKER> [<TICKER> ...]")
        sys.exit(1)

    tickers = [a.upper() for a in sys.argv[1:]]
    print(f"Detecting clusters for {len(tickers)} ticker(s)...")
    txns = fetch_form4_for_universe(tickers, since_days=WINDOW_DAYS)
    sigs = detect_clusters(txns)
    if not sigs:
        print("\nNo qualifying clusters in window.")
    else:
        print(f"\n{len(sigs)} cluster signal(s):\n")
        for s in sigs:
            print(format_cluster(s))
            print()
