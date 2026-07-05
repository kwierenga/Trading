"""
Unit tests for the LTCG holding-period boundary (live-transition checklist #12).

IRS Pub 550: long-term treatment requires holding MORE than one year — the
holding period starts the day after acquisition, so a sale ON the one-year
anniversary (days_held == 365) is still short-term. Found as an off-by-one
(`>= 365`) in both trade_journal and performance_tracker on 2026-07-05, fixed
to strict `>`. On paper this was cosmetic; on real money it's a ~17pp tax-rate
difference on a boundary-day sale. These tests pin the rule.

Run: python test_ltcg_boundary.py
"""

from trade_journal import TradeJournal, LTCG_THRESHOLD_DAYS


def test_threshold_constant_unchanged():
    assert LTCG_THRESHOLD_DAYS == 365, LTCG_THRESHOLD_DAYS
    print("PASS  test_threshold_constant_unchanged")


def test_day_364_is_approaching():
    s = TradeJournal.holding_period_status(364)
    assert s["status"] == "approaching_LTCG", s
    assert s["days_to_LTCG"] == 2, s  # needs day 366 to be long-term
    print("PASS  test_day_364_is_approaching")


def test_day_365_anniversary_is_still_short_term():
    # The bug this file exists for: anniversary-day sale is NOT long-term.
    s = TradeJournal.holding_period_status(365)
    assert s["status"] == "approaching_LTCG", s
    assert s["days_to_LTCG"] == 1, s
    print("PASS  test_day_365_anniversary_is_still_short_term")


def test_day_366_is_ltcg():
    s = TradeJournal.holding_period_status(366)
    assert s["status"] == "LTCG", s
    assert s["days_to_LTCG"] == 0, s
    print("PASS  test_day_366_is_ltcg")


def test_day_335_enters_approaching_window():
    # 30-day approaching window counts toward day 366: 366-335=31 -> not yet;
    # 336 -> 30 remaining -> flagged.
    s335 = TradeJournal.holding_period_status(335)
    s336 = TradeJournal.holding_period_status(336)
    assert s335["status"] == "short_term", s335
    assert s336["status"] == "approaching_LTCG", s336
    assert s336["days_to_LTCG"] == 30, s336
    print("PASS  test_day_335_enters_approaching_window")


def test_fresh_position_short_term():
    s = TradeJournal.holding_period_status(5)
    assert s["status"] == "short_term", s
    print("PASS  test_fresh_position_short_term")


if __name__ == "__main__":
    test_threshold_constant_unchanged()
    test_day_364_is_approaching()
    test_day_365_anniversary_is_still_short_term()
    test_day_366_is_ltcg()
    test_day_335_enters_approaching_window()
    test_fresh_position_short_term()
    print("\nAll LTCG boundary tests passed.")
