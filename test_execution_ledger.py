"""
Unit tests for execution_ledger.RunLedger.commit merge semantics.

On 2026-06-11 a manual re-dispatch of execute.yml ran 8 minutes after the
backup trigger had already submitted three orders. The old commit() replaced
ALL of the date's rows, so the second run's `skipped` verdicts erased the
three `placed` rows — losing the Alpaca order ids the EOD reconcile needed.
This pins the fix: placed rows are immutable facts and survive same-day
re-runs; skipped rows are per-run verdicts and get replaced; other dates are
untouched.

Run: python test_execution_ledger.py
"""

import json
import os
import tempfile

from execution_ledger import RunLedger, load, rows_for_date

PATH = os.path.join(tempfile.gettempdir(), "test_execution_ledger.json")


def fresh(rows=None):
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(rows or [], f)


def test_first_run_writes_rows():
    fresh()
    run = RunLedger(date_utc="2026-06-11", path=PATH)
    run.placed("IQV", 69, 180.81, "order-1")
    run.skipped("XYZ", "gross", "over the 95% cap")
    run.commit()
    rows = rows_for_date("2026-06-11", path=PATH)
    assert len(rows) == 2, rows
    print("PASS  test_first_run_writes_rows")


def test_rerun_preserves_placed_rows():
    fresh()
    run1 = RunLedger(date_utc="2026-06-11", path=PATH)
    run1.placed("IQV", 69, 180.81, "order-1")
    run1.placed("A", 143, 131.10, "order-2")
    run1.commit()

    # second same-day run skips both (positions now exist)
    run2 = RunLedger(date_utc="2026-06-11", path=PATH)
    run2.skipped("IQV", "sector", "Healthcare over 35%")
    run2.skipped("A", "concentration", "over 25%")
    run2.commit()

    rows = rows_for_date("2026-06-11", path=PATH)
    placed = [r for r in rows if r["decision"] == "placed"]
    skipped = [r for r in rows if r["decision"] == "skipped"]
    assert {r["order_id"] for r in placed} == {"order-1", "order-2"}, rows
    assert len(skipped) == 2, rows
    print("PASS  test_rerun_preserves_placed_rows")


def test_rerun_replaces_stale_skipped_rows():
    fresh()
    run1 = RunLedger(date_utc="2026-06-11", path=PATH)
    run1.skipped("ADP", "gross", "over the 95% cap")
    run1.commit()

    run2 = RunLedger(date_utc="2026-06-11", path=PATH)
    run2.skipped("ADP", "concentration", "over 25%")
    run2.commit()

    rows = rows_for_date("2026-06-11", path=PATH)
    assert len(rows) == 1, rows
    assert rows[0]["gate"] == "concentration", rows
    print("PASS  test_rerun_replaces_stale_skipped_rows")


def test_other_dates_untouched():
    fresh([{"date": "2026-06-10", "symbol": "OLD", "decision": "placed",
            "gate": "submitted", "detail": "", "order_id": "order-old"}])
    run = RunLedger(date_utc="2026-06-11", path=PATH)
    run.skipped("NEW", "conviction", "below threshold")
    run.commit()
    assert len(rows_for_date("2026-06-10", path=PATH)) == 1, load(PATH)
    assert len(rows_for_date("2026-06-11", path=PATH)) == 1, load(PATH)
    print("PASS  test_other_dates_untouched")


if __name__ == "__main__":
    test_first_run_writes_rows()
    test_rerun_preserves_placed_rows()
    test_rerun_replaces_stale_skipped_rows()
    test_other_dates_untouched()
    os.remove(PATH)
    print("\nAll execution_ledger tests passed.")
