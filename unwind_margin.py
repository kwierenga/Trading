"""
One-off: unwind the accidental 1.38x margin (Klaas-approved Proposal A,
2026-05-18). Fully exits MCO and UBER to bring gross deployment under the
locked 95% cap. NOT wired into any workflow — manual, single use.

Safety:
  - DRY-RUN by default. Live submission ONLY with --submit.
  - Refuses to submit while the market is CLOSED (close_position is a market
    order; equity market orders are rejected outside RTH). Override only with
    an explicit --force-closed (not recommended).
  - Reads LIVE positions/orders (never trusts the journal's approximations).
  - Refuses to act on a symbol that isn't actually held.
  - Cancels the open bracket child orders for each target FIRST, then
    close_position — otherwise the orphaned OCO legs could double-sell.
  - UTF-8 stdout + ASCII-only output so a console-encoding error can never
    crash the run between cancel and close.
"""
import sys

from alpaca_client import AlpacaClient

TARGETS = ["MCO", "UBER"]          # approved trim set; full position each
GROSS_CAP = 0.95


def _req_exc():
    import requests
    return (requests.RequestException, KeyError, ValueError, OSError)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    submit = "--submit" in sys.argv
    force_closed = "--force-closed" in sys.argv
    mode = "LIVE SUBMIT" if submit else "DRY RUN (no orders)"
    print(f"=== unwind_margin.py - {mode} ===")

    c = AlpacaClient()
    acct = c.get_account()
    equity = float(acct["equity"])
    cash = float(acct["cash"])
    positions = c.get_positions()
    by_sym = {(p.get("symbol") or "").upper(): p for p in positions}
    gross_before = sum(float(p.get("market_value", 0)) for p in positions)

    try:
        clock = c._get("/clock")
        is_open = bool(clock.get("is_open"))
        mkt = f"open={is_open}, next_open={clock.get('next_open')}"
    except (KeyError, ValueError, OSError) as e:
        clock, is_open, mkt = {}, False, f"(clock unavailable: {e})"

    print(f"\nEquity ${equity:,.2f}  Cash ${cash:,.2f}  "
          f"Gross ${gross_before:,.2f}  ({gross_before/equity:.2%} of equity)")
    print(f"95% cap = ${GROSS_CAP*equity:,.2f}   Market: {mkt}\n")

    plan = []
    for sym in TARGETS:
        p = by_sym.get(sym)
        if not p:
            print(f"  {sym}: NOT HELD - skipping (safety).")
            continue
        qty = float(p["qty"])
        mv = float(p.get("market_value", 0))
        upl = float(p.get("unrealized_pl", 0))
        open_orders = c.get_open_orders_for_symbol(sym)
        print(f"  {sym}: {qty:g} sh, market value ${mv:,.2f}, "
              f"unrealized ${upl:+,.2f}")
        for o in open_orders:
            print(f"     cancel-first: {o.get('id')} {o.get('side')} "
                  f"{o.get('type')} qty={o.get('qty')} status={o.get('status')}")
        plan.append((sym, qty, mv, [o.get("id") for o in open_orders]))

    proceeds = sum(mv for _, _, mv, _ in plan)
    gross_after = gross_before - proceeds
    print(f"\nProjected: free ~ ${proceeds:,.2f} -> gross ~ ${gross_after:,.2f} "
          f"({(gross_after/equity) if equity else 0:.2%} of equity), "
          f"cash ~ ${cash+proceeds:,.2f}")
    print(f"Under {GROSS_CAP:.0%} cap after unwind? "
          f"{'YES' if gross_after <= GROSS_CAP*equity else 'NO'}")

    if not submit:
        print("\nDRY RUN - nothing submitted. Re-run with --submit during "
              "market hours to execute.")
        return 0

    if not plan:
        print("\nNothing to do (no target positions held).")
        return 0

    if not is_open and not force_closed:
        print("\nREFUSING TO SUBMIT: market is CLOSED. close_position is a "
              "market order and would be rejected outside RTH. Re-run with "
              "--submit during RTH (override: --force-closed, not advised).")
        return 2

    print("\n--- LIVE: cancel child orders, then close positions ---")
    rc = 0
    for sym, qty, mv, order_ids in plan:
        for oid in order_ids:
            try:
                c.cancel_order(oid)
                print(f"  {sym}: cancelled order {oid}")
            except _req_exc() as e:
                print(f"  {sym}: cancel {oid} FAILED: {e}")
                rc = 1
        try:
            r = c.close_position(sym)
            print(f"  {sym}: close_position OK -> id {r.get('id', '?')}, "
                  f"status {r.get('status', '?')}")
        except _req_exc() as e:
            print(f"  {sym}: close_position FAILED: {e}")
            rc = 1
    print("\nDone. Verify fills at next EOD / in Alpaca; "
          "journal_reconcile will pick them up.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
