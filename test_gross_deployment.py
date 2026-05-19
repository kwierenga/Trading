"""
Unit tests for position_sizer.validate_gross_deployment.

The aggregate gross-deployment cap (locked 2026-05-18) is the hard rule that
stops independently-sized per-name positions from stacking past 100% of equity
into accidental margin (the 1.38x / -$38,966 incident). NO margin on the
automated path. This pins the behaviour: under cap OK, at cap OK, over cap
blocked, the 5% buffer, headroom math, override, and defensive parsing.

Run: python test_gross_deployment.py
"""

from position_sizer import validate_gross_deployment, MAX_GROSS_PCT


def pos(symbol: str, mv) -> dict:
    return {"symbol": symbol, "market_value": mv}


def test_default_cap_is_95pct():
    assert MAX_GROSS_PCT == 0.95, MAX_GROSS_PCT
    print("PASS  test_default_cap_is_95pct")


def test_empty_book_small_buy_ok():
    r = validate_gross_deployment("AAA", 10_000, 100_000, [])
    assert r["ok"] is True, r
    assert abs(r["would_be_pct"] - 0.10) < 1e-9, r
    assert abs(r["headroom"] - 95_000) < 1e-6, r
    print("PASS  test_empty_book_small_buy_ok")


def test_exactly_at_cap_is_ok():
    # gross 70k + 25k proposed = 95k = exactly 95% of 100k -> not > cap -> OK
    r = validate_gross_deployment("AAA", 25_000, 100_000, [pos("BBB", 70_000)])
    assert r["ok"] is True, r
    assert abs(r["would_be_pct"] - 0.95) < 1e-9, r
    print("PASS  test_exactly_at_cap_is_ok")


def test_one_cent_over_cap_blocked():
    r = validate_gross_deployment("AAA", 25_000.01, 100_000, [pos("BBB", 70_000)])
    assert r["ok"] is False, r
    assert r["would_be_pct"] > 0.95, r
    print("PASS  test_one_cent_over_cap_blocked")


def test_buffer_band():
    # book already at 90% of equity
    book = [pos("BBB", 90_000)]
    ok = validate_gross_deployment("AAA", 4_000, 100_000, book)   # -> 94%
    blocked = validate_gross_deployment("AAA", 6_000, 100_000, book)  # -> 96%
    assert ok["ok"] is True, ok
    assert blocked["ok"] is False, blocked
    print("PASS  test_buffer_band")


def test_headroom_value():
    r = validate_gross_deployment("AAA", 1, 100_000, [pos("BBB", 80_000)])
    # headroom = 0.95*100k - 80k = 15k
    assert abs(r["headroom"] - 15_000) < 1e-6, r
    print("PASS  test_headroom_value")


def test_cap_override():
    # allow a deliberate 1.25x with an explicit override
    r = validate_gross_deployment("AAA", 30_000, 100_000,
                                  [pos("BBB", 95_000)], cap_pct=1.25)
    assert r["ok"] is True, r           # 125% -> exactly at the overridden cap
    assert r["cap_pct"] == 1.25, r
    print("PASS  test_cap_override")


def test_defensive_bad_market_values():
    book = [pos("BBB", None), pos("CCC", "not-a-number"), pos("DDD", 50_000)]
    r = validate_gross_deployment("AAA", 10_000, 100_000, book)
    # only DDD's 50k counts; 50k+10k=60k <= 95k -> OK, no crash
    assert r["ok"] is True, r
    assert abs(r["current_gross"] - 50_000) < 1e-6, r
    print("PASS  test_defensive_bad_market_values")


def test_zero_equity_blocked():
    r = validate_gross_deployment("AAA", 1, 0, [])
    assert r["ok"] is False, r
    assert r["would_be_pct"] == 1.0, r
    print("PASS  test_zero_equity_blocked")


def test_incident_scenario_blocks_any_new_buy():
    # The actual 2026-05-18 book: ~138% gross on ~$101.6k equity. Any new buy
    # must be blocked until it de-levers.
    book = [
        pos("V", 25_500), pos("MSFT", 49_900), pos("APP", 16_500),
        pos("UBER", 24_225), pos("MCO", 24_100),
    ]
    r = validate_gross_deployment("NEW", 1_000, 101_600, book)
    assert r["ok"] is False, r
    assert r["would_be_pct"] > 1.3, r
    assert r["headroom"] < 0, r
    print("PASS  test_incident_scenario_blocks_any_new_buy")


if __name__ == "__main__":
    test_default_cap_is_95pct()
    test_empty_book_small_buy_ok()
    test_exactly_at_cap_is_ok()
    test_one_cent_over_cap_blocked()
    test_buffer_band()
    test_headroom_value()
    test_cap_override()
    test_defensive_bad_market_values()
    test_zero_equity_blocked()
    test_incident_scenario_blocks_any_new_buy()
    print("\nAll gross-deployment tests passed.")
