# Trading Journal

Daily log: 4 sections per entry, ~3 sentences each. Newest at top. Read this in the morning to catch up on what happened, what we learned, what we're testing, and what's next.

**Sections (in order):**
1. **What happened** — concrete events: trades, market moves, decisions, code changes.
2. **What we learned** — patterns observed or hypotheses confirmed/refuted.
3. **Open questions** — hypotheses still being tested; what we don't yet know.
4. **Tomorrow's plan** — the next concrete action.

Cap: 3 sentences per section. If you need more, it belongs in a memo, not the journal.

---

## [NOTE] 2026-06-06 Saturday — execution observability build (enhancements 1-5)
**What happened.** Diagnosed why "trades often don't execute, unclear why": entries are below-market GTC limits that frequently never fill, the book is ~97% deployed so the gross cap blocks new buys, and the journal/email reported *submits* not *fills* with no record of the ~9 silent skip gates. Built five fixes: a per-candidate execution ledger (`execution_ledger.py`) wired into every skip/place site in `execute_strategy.py`; a fill-status reconcile against Alpaca (`reconcile_and_persist`); a rewritten `notify_execute` success email that lists every placed order's real fill status and every skip's exact gate+reason; an EOD stale-entry-order sweep (`order_sweep.py`, dry-run default via `ORDER_SWEEP_LIVE`); and a deployment-reality line in the AM email. Ledger now committed by execute.yml/eod.yml for git-auditable "why didn't it trade" history.
**What we learned.** The reported problem was observability + fill mechanics, not alpha — "submitted" was silently conflated with "executed," and below-market GTC limits on basing names rarely fill. Separately noticed origin/main's JOURNAL.md has been stuck at 2 entries for 6+ commits — the cloud routine appears to prune history (intact in git, not in the file); flagged for follow-up. Did NOT touch picking/timing/universe — the seven-backtest "no edge vs buy-hold SPY" finding stands.
**Open questions.** Over the next several live sessions, what's the actual fill rate on submitted limits, and is the dominant skip reason the gross cap (as expected) or something else? Why is JOURNAL.md being truncated to 2 entries in the cloud when `append_to_journal` is written to prepend-and-preserve?
**Tomorrow's plan.** Let the observability build run untouched in production for several days and read the new email sections before extending (per validate-before-extending). Investigate the JOURNAL truncation, then decide on flipping the sweep live and/or enhancements 6-10.

---

## [EOD] 2026-06-05 Friday
**What happened.** No trades closed today. End equity $102,978, cash $3,113 (3% of equity), 3 open position(s). 
**What we learned.** [Add 1-2 sentences during your 15-min review: what surprised you today, what hypothesis got confirmed or refuted, or what you noticed about the market.] 

---

## [AM] 2026-06-05 Friday
**Open questions.** Will the 1 proposed entries (ZBRA) fill at limit, or run away pre-market? What's the one thing that could derail the 52% confidence target? Any overnight news on these names worth checking before placing orders? 
**Today's plan.** execute.yml fires automatically at 09:35 ET — re-evaluates each setup against the actual open and submits the survivors. To skip today, push SKIP_TODAY.flag with today's UTC date before 09:35 ET. Monitor 3 open position(s) for thesis-break, stop hits, or LTCG-approaching flags. 

