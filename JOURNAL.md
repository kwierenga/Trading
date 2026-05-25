# Trading Journal

Daily log: 4 sections per entry, ~3 sentences each. Newest at top. Read this in the morning to catch up on what happened, what we learned, what we're testing, and what's next.

**Sections (in order):**
1. **What happened** — concrete events: trades, market moves, decisions, code changes.
2. **What we learned** — patterns observed or hypotheses confirmed/refuted.
3. **Open questions** — hypotheses still being tested; what we don't yet know.
4. **Tomorrow's plan** — the next concrete action.

Cap: 3 sentences per section. If you need more, it belongs in a memo, not the journal.

---

## [EOD] 2026-05-25 Monday
**What happened.** No trades closed today. End equity $100,740, cash $-38,966 (-39% of equity), 5 open position(s). 
**What we learned.** [Add 1-2 sentences during your 15-min review: what surprised you today, what hypothesis got confirmed or refuted, or what you noticed about the market.] 

---

## [SESSION] 2026-05-24 Sunday

**What happened.** Built and pushed a one-shot full-liquidation tool (`liquidate_all.py` + `liquidate.yml` + `SKIP_TODAY.flag` dated 2026-05-27) to flatten the entire book to cash on Wed 05-27 ~09:35 ET and restart the refined strategy from a clean slate. Driver: the book is still pinned at 138.7% gross (~1.39x margin, MSFT ~49% of equity), above the 95% cap, so execute.yml makes zero new buys → zero trades → nothing to learn from. Dry-run validated live: 5 positions → $0 gross, ~$100,740 cash, +$1,632 realized.

**What we learned.** `unwind_margin.py` had a latent gap — `get_open_orders_for_symbol` (status=open) returns the take-profit limit leg but misses the held stop-loss leg, which would orphan a sell order after `close_position`; the new tool cancels every non-terminal leg per symbol first. Confirmed the system can sell, and the live book is unchanged since 05-19 (the margin was never unwound).

**Open questions.** Will the refined strategy, trading from a flat book under the wired 25%/35%/95% caps, actually generate the per-trade learning this restart is meant to unblock? Does cancelling one OCO leg auto-cancel its held sibling, or must both be cancelled explicitly (we assumed the latter, to be safe)?

**Tomorrow's plan.** Mon 05-25 is Memorial Day — market closed, nothing to do. Wed 05-27 ~09:35 ET: dispatch the Liquidate-all workflow with `LIQUIDATE ALL` (empty-box dry-run preview first), confirm flat, then let Thu 05-28's normal flow rebuild the book.

