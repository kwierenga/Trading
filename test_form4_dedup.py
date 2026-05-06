"""
Unit tests for form4_data.dedup_amendments.

Spec §15.1 / §8.2 mandate: amendments must be deduped before any signal logic.
A single economic transaction often produces an original Form 4 plus 1-2 Form 4/A
amendments; without dedup, cluster sizes inflate (one insider's single trade looks
like 2-3 insiders) and signal counts inflate.

Run: python test_form4_dedup.py
"""

from form4_data import Form4Transaction, dedup_amendments


def make_txn(
    accession: str,
    filing_date: str,
    transaction_date: str = "2026-04-15",
    owner_cik: str = "0000111111",
    issuer_cik: str = "0000222222",
    shares: float = 1000.0,
    code: str = "P",
    is_amendment: bool = False,
    name: str = "Doe John",
    role: str = "CEO",
    price: float = 100.0,
) -> Form4Transaction:
    return Form4Transaction(
        accession=accession,
        is_amendment=is_amendment,
        filing_date=filing_date,
        transaction_date=transaction_date,
        issuer_cik=issuer_cik,
        issuer_name="Test Corp",
        issuer_ticker="TEST",
        reporting_owner_cik=owner_cik,
        reporting_owner_name=name,
        insider_role=role,
        transaction_code=code,
        shares=shares,
        price_per_share=price,
        transaction_value=shares * price,
        rule_10b5_1_flag=False,
    )


def test_original_plus_one_amendment():
    """Original + 1 amendment for same economic transaction → keep latest filing."""
    original = make_txn("0001-original", "2026-04-15", is_amendment=False)
    amend = make_txn("0001-amend", "2026-04-18", is_amendment=True)
    out = dedup_amendments([original, amend])
    assert len(out) == 1, f"expected 1 row, got {len(out)}"
    assert out[0].accession == "0001-amend", "should keep the latest filing"
    assert out[0].is_amendment is True
    print("PASS  test_original_plus_one_amendment")


def test_original_plus_two_amendments():
    """Spec §15.1 explicit case: original + 2 sequential amendments."""
    original = make_txn("0001-original", "2026-04-15", is_amendment=False)
    amend1 = make_txn("0001-amend1", "2026-04-18", is_amendment=True)
    amend2 = make_txn("0001-amend2", "2026-04-20", is_amendment=True)
    # Order intentionally scrambled — dedup must not depend on input order
    out = dedup_amendments([amend1, original, amend2])
    assert len(out) == 1, f"expected 1 row, got {len(out)}"
    assert out[0].accession == "0001-amend2", "should keep the latest filing"
    print("PASS  test_original_plus_two_amendments")


def test_two_distinct_insiders_same_day():
    """Two insiders, same issuer, same day, same shares → both kept (different CIKs)."""
    a = make_txn("a", "2026-04-15", owner_cik="0000111", name="A")
    b = make_txn("b", "2026-04-15", owner_cik="0000222", name="B")
    out = dedup_amendments([a, b])
    assert len(out) == 2, f"expected 2 rows, got {len(out)}"
    print("PASS  test_two_distinct_insiders_same_day")


def test_same_insider_different_days():
    """Same insider, different transaction dates → both kept."""
    a = make_txn("a", "2026-04-15", transaction_date="2026-04-10")
    b = make_txn("b", "2026-04-15", transaction_date="2026-04-12")
    out = dedup_amendments([a, b])
    assert len(out) == 2, f"expected 2 rows, got {len(out)}"
    print("PASS  test_same_insider_different_days")


def test_p_and_s_same_day_same_shares():
    """Same insider, same day, same shares, P vs S → both kept (code distinguishes)."""
    p = make_txn("p", "2026-04-15", code="P")
    s = make_txn("s", "2026-04-15", code="S")
    out = dedup_amendments([p, s])
    assert len(out) == 2, f"expected 2 rows, got {len(out)}"
    codes = sorted(t.transaction_code for t in out)
    assert codes == ["P", "S"], f"expected P+S, got {codes}"
    print("PASS  test_p_and_s_same_day_same_shares")


def test_amendment_with_corrected_shares_treated_as_new():
    """
    Edge: amendment changes share count (e.g. correcting a typo). Per spec §8.2
    group_key includes sharesTransacted, so this is a *different* group — both
    rows survive. Documents the literal-spec behavior; flagged for spec author
    if undesired (would need looser key, e.g. ±5% share tolerance).
    """
    original = make_txn("0001-original", "2026-04-15", shares=1000)
    amend = make_txn("0001-amend", "2026-04-18", shares=1100, is_amendment=True)
    out = dedup_amendments([original, amend])
    assert len(out) == 2, f"expected 2 rows (literal spec), got {len(out)}"
    print("PASS  test_amendment_with_corrected_shares_treated_as_new")


def test_empty_input():
    assert dedup_amendments([]) == []
    print("PASS  test_empty_input")


def test_three_amendments_for_same_insider_two_distinct_transactions():
    """
    Realistic case: same insider made 2 P transactions on different days; each got
    re-filed once via 4/A. Should collapse to 2 rows, both amendments retained.
    """
    txn1_orig = make_txn("t1-o", "2026-04-15", transaction_date="2026-04-10", shares=500)
    txn1_amend = make_txn("t1-a", "2026-04-18", transaction_date="2026-04-10", shares=500, is_amendment=True)
    txn2_orig = make_txn("t2-o", "2026-04-16", transaction_date="2026-04-12", shares=700)
    txn2_amend = make_txn("t2-a", "2026-04-19", transaction_date="2026-04-12", shares=700, is_amendment=True)
    out = dedup_amendments([txn1_orig, txn1_amend, txn2_orig, txn2_amend])
    assert len(out) == 2, f"expected 2 rows, got {len(out)}"
    accessions = sorted(t.accession for t in out)
    assert accessions == ["t1-a", "t2-a"], f"expected both amendments, got {accessions}"
    print("PASS  test_three_amendments_for_same_insider_two_distinct_transactions")


if __name__ == "__main__":
    test_original_plus_one_amendment()
    test_original_plus_two_amendments()
    test_two_distinct_insiders_same_day()
    test_same_insider_different_days()
    test_p_and_s_same_day_same_shares()
    test_amendment_with_corrected_shares_treated_as_new()
    test_empty_input()
    test_three_amendments_for_same_insider_two_distinct_transactions()
    print("\nAll dedup tests passed.")
