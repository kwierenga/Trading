"""
Unit tests for position_sizer.validate_daily_loss_halt.

The daily dollar-loss kill switch (live-transition checklist #9, built
2026-07-04) refuses ALL new entries when the account is down >= 3% versus the
prior close (Alpaca `last_equity`). It must fail CLOSED on missing/invalid
account data (2026-05-02 lesson: risk-path defaults refuse, they don't guess).
This pins: flat day OK, small red day OK, breach halts, exact-threshold halts,
missing/zero/garbage fields halt, and the override arg.

Run: python test_kill_switch.py
"""

from position_sizer import validate_daily_loss_halt, DAILY_LOSS_HALT_PCT


def acct(equity, last_equity) -> dict:
    return {"equity": equity, "last_equity": last_equity}


def test_default_halt_is_3pct():
    assert DAILY_LOSS_HALT_PCT == 0.03, DAILY_LOSS_HALT_PCT
    print("PASS  test_default_halt_is_3pct")


def test_flat_day_ok():
    r = validate_daily_loss_halt(acct(100_000, 100_000))
    assert r["ok"] is True, r
    assert abs(r["day_pnl_pct"]) < 1e-9, r
    print("PASS  test_flat_day_ok")


def test_up_day_ok():
    r = validate_daily_loss_halt(acct(102_000, 100_000))
    assert r["ok"] is True, r
    print("PASS  test_up_day_ok")


def test_small_red_day_ok():
    # -2.9% is inside the -3% halt
    r = validate_daily_loss_halt(acct(97_100, 100_000))
    assert r["ok"] is True, r
    print("PASS  test_small_red_day_ok")


def test_breach_halts():
    r = validate_daily_loss_halt(acct(96_000, 100_000))
    assert r["ok"] is False, r
    assert "halt" in r["reason"], r
    assert abs(r["day_pnl_pct"] - (-0.04)) < 1e-9, r
    print("PASS  test_breach_halts")


def test_exactly_at_threshold_halts():
    # -3.000% counts as breached (<= -halt), matching "two bad stops = stand down"
    r = validate_daily_loss_halt(acct(97_000, 100_000))
    assert r["ok"] is False, r
    print("PASS  test_exactly_at_threshold_halts")


def test_missing_last_equity_fails_closed():
    r = validate_daily_loss_halt({"equity": 100_000})
    assert r["ok"] is False, r
    assert r["day_pnl_pct"] is None, r
    print("PASS  test_missing_last_equity_fails_closed")


def test_zero_equity_fails_closed():
    r = validate_daily_loss_halt(acct(0, 100_000))
    assert r["ok"] is False, r
    print("PASS  test_zero_equity_fails_closed")


def test_garbage_fields_fail_closed():
    r = validate_daily_loss_halt(acct("not-a-number", "also-bad"))
    assert r["ok"] is False, r
    print("PASS  test_garbage_fields_fail_closed")


def test_override_halt_pct():
    # -4% day passes a 5% override but fails the default
    r = validate_daily_loss_halt(acct(96_000, 100_000), halt_pct=0.05)
    assert r["ok"] is True, r
    assert r["halt_pct"] == 0.05, r
    print("PASS  test_override_halt_pct")


if __name__ == "__main__":
    test_default_halt_is_3pct()
    test_flat_day_ok()
    test_up_day_ok()
    test_small_red_day_ok()
    test_breach_halts()
    test_exactly_at_threshold_halts()
    test_missing_last_equity_fails_closed()
    test_zero_equity_fails_closed()
    test_garbage_fields_fail_closed()
    test_override_halt_pct()
    print("\nAll kill-switch tests passed.")
