"""
Flatten the ENTIRE book to cash. One-off restart tool (Klaas-approved
2026-05-24): the live paper book is pinned at 138.7% gross (~1.39x margin,
MSFT ~49% of equity), which sits above the locked 95% gross cap, so the
automated path makes zero new buys -> zero new trades -> nothing to learn
from. Selling everything returns the book to ~100% cash so the refined
strategy can re-deploy fresh under the now-wired 25%/35%/95% caps.

Generalizes unwind_margin.py (which targeted only MCO/UBER) to ALL held
positions. Driven from liquidate.yml (workflow_dispatch) or run locally.

Safety (identical posture to unwind_margin.py):
  - DRY-RUN by default. Live submission ONLY with --submit.
  - Refuses to submit while the market is CLOSED (close_position is a market
    order; equity market orders are rejected outside RTH). Override only with
    an explicit --force-closed (not recommended).
  - Reads LIVE positions/orders (never trusts the journal's approximations).
  - Cancels each position's open bracket child orders FIRST, then
    close_position -- otherwise the orphaned OCO legs could double-sell.
  - UTF-8 stdout + ASCII-only output so a console-encoding error can never
    crash the run between cancel and close.
  - --email sends a plain-text summary (used by the workflow's live run).

Exit codes: 0 = clean (incl. dry run and "nothing held"), 1 = one or more
cancel/close calls failed, 2 = refused because market closed.
"""
import sys


def _req_exc():
    import requests
    return (requests.RequestException, KeyError, ValueError, OSError)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    submit = "--submit" in sys.argv
    force_closed = "--force-closed" in sys.argv
    want_email = "--email" in sys.argv
    mode = "LIVE SUBMIT" if submit else "DRY RUN (no orders)"
    print(f"=== liquidate_all.py - {mode} ===")

    from alpaca_client import AlpacaClient
    c = AlpacaClient()
    acct = c.get_account()
    equity = float(acct["equity"])
    cash = float(acct["cash"])
    positions = c.get_positions()
    gross_before = sum(float(p.get("market_value", 0)) for p in positions)

    # Gather ALL cancelable orders up front, grouped by symbol. We deliberately
    # do NOT use get_open_orders_for_symbol() here: it filters to status='open',
    # which returns the take-profit (limit) leg but MISSES the stop-loss leg of
    # a filled bracket — that sibling sits in 'held' status. Cancelling only the
    # limit leg would orphan the held stop, leaving a sell order for shares we
    # no longer own after close_position. So we cancel every non-terminal leg.
    CANCELABLE = {
        "new", "accepted", "held", "partially_filled", "pending_new",
        "pending_cancel", "pending_replace", "accepted_for_bidding",
        "calculated", "suspended", "done_for_day",
    }
    orders_by_sym = {}
    try:
        for o in c.get_orders(status="all"):
            if (o.get("status") or "").lower() in CANCELABLE:
                sym_o = (o.get("symbol") or "").upper()
                orders_by_sym.setdefault(sym_o, []).append(o)
    except (KeyError, ValueError, OSError) as e:
        print(f"  WARNING: could not list orders ({e}); "
              f"will close positions without cancelling legs.")

    try:
        clock = c._get("/clock")
        is_open = bool(clock.get("is_open"))
        mkt = f"open={is_open}, next_open={clock.get('next_open')}"
    except (KeyError, ValueError, OSError) as e:
        clock, is_open, mkt = {}, False, f"(clock unavailable: {e})"

    print(f"\nEquity ${equity:,.2f}  Cash ${cash:,.2f}  "
          f"Gross ${gross_before:,.2f}  ({(gross_before/equity) if equity else 0:.2%} of equity)")
    print(f"Market: {mkt}\n")

    # Plan: every held position is a target. No allow-list -- this tool's whole
    # job is "flatten everything". Sort largest-first for a readable summary.
    plan = []
    for p in sorted(positions, key=lambda x: -float(x.get("market_value", 0))):
        sym = (p.get("symbol") or "").upper()
        if not sym:
            continue
        qty = float(p["qty"])
        mv = float(p.get("market_value", 0))
        upl = float(p.get("unrealized_pl", 0))
        legs = orders_by_sym.get(sym, [])
        print(f"  {sym}: {qty:g} sh, market value ${mv:,.2f}, "
              f"unrealized ${upl:+,.2f}")
        for o in legs:
            print(f"     cancel-first: {o.get('id')} {o.get('side')} "
                  f"{o.get('type')} qty={o.get('qty')} status={o.get('status')}")
        plan.append((sym, qty, mv, upl, [o.get("id") for o in legs]))

    proceeds = sum(mv for _, _, mv, _, _ in plan)
    total_upl = sum(upl for _, _, _, upl, _ in plan)
    gross_after = gross_before - proceeds
    print(f"\nProjected: free ~ ${proceeds:,.2f} -> gross ~ ${gross_after:,.2f} "
          f"({(gross_after/equity) if equity else 0:.2%} of equity), "
          f"cash ~ ${cash+proceeds:,.2f}")
    print(f"Realized P&L on exit (approx): ${total_upl:+,.2f}")
    print(f"Positions to flatten: {len(plan)}")

    summary_lines = [
        f"{sym}: {qty:g} sh, ${mv:,.2f} (unrealized ${upl:+,.2f})"
        for sym, qty, mv, upl, _ in plan
    ]

    def _email(subject, extra=""):
        if not want_email:
            return
        try:
            from email_notifier import send_email
            body = (
                f"{subject}\n\n"
                f"Equity ${equity:,.2f}  Cash ${cash:,.2f}\n"
                f"Gross before ${gross_before:,.2f} "
                f"({(gross_before/equity) if equity else 0:.1%} of equity)\n"
                f"Gross after  ${gross_after:,.2f} "
                f"({(gross_after/equity) if equity else 0:.1%} of equity)\n"
                f"Approx realized P&L: ${total_upl:+,.2f}\n\n"
                f"Positions ({len(plan)}):\n  " + "\n  ".join(summary_lines)
                + (f"\n\n{extra}" if extra else "")
            )
            send_email(subject, body)
        except (OSError, ImportError) as e:
            print(f"  Email step failed (non-fatal): {e}")

    if not submit:
        print("\nDRY RUN - nothing submitted. Re-run with --submit during "
              "market hours to execute.")
        return 0

    if not plan:
        print("\nNothing to do (book already flat).")
        _email("[Trading LIQUIDATE] Already flat - nothing to do")
        return 0

    if not is_open and not force_closed:
        print("\nREFUSING TO SUBMIT: market is CLOSED. close_position is a "
              "market order and would be rejected outside RTH. Re-run with "
              "--submit during RTH (override: --force-closed, not advised).")
        _email("[Trading LIQUIDATE] REFUSED - market closed",
               "No orders submitted. Re-dispatch during RTH (09:35 ET).")
        return 2

    print("\n--- LIVE: cancel child orders, then close positions ---")
    rc = 0
    closed, failed = [], []
    for sym, qty, mv, upl, order_ids in plan:
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
            closed.append(sym)
        except _req_exc() as e:
            print(f"  {sym}: close_position FAILED: {e}")
            failed.append(sym)
            rc = 1

    print("\nDone. Verify fills at next EOD / in Alpaca; "
          "journal_reconcile will pick them up.")
    subj = ("[Trading LIQUIDATE] Book flattened"
            if rc == 0 else "[Trading LIQUIDATE] Completed WITH ERRORS")
    _email(subj, f"close_position OK: {', '.join(closed) or 'none'}"
                 + (f"\nFAILED (verify manually): {', '.join(failed)}" if failed else ""))
    return rc


if __name__ == "__main__":
    sys.exit(main())
