# Trading Journal

Daily log: 4 sections per entry, ~3 sentences each. Newest at top. Read this in the morning to catch up on what happened, what we learned, what we're testing, and what's next.

**Sections (in order):**
1. **What happened** — concrete events: trades, market moves, decisions, code changes.
2. **What we learned** — patterns observed or hypotheses confirmed/refuted.
3. **Open questions** — hypotheses still being tested; what we don't yet know.
4. **Tomorrow's plan** — the next concrete action.

Cap: 3 sentences per section. If you need more, it belongs in a memo, not the journal.

---

## [WEEK] 2026-05-26 → 2026-06-07

**What worked / what didn't.** No positions were opened or closed this week, so there is nothing to evaluate on trade selection or execution quality. What did happen is structural: three significant process changes were designed and committed — the honesty reframe on return targets, the regime/capacity gate, and the execution ledger. Those are the week's actual output. The one concrete failure pattern visible in the data is that MSFT reached ~49% of book, directly violating the 25% per-name cap that was supposedly in place. That didn't happen this week — it was inherited — but the decision to restart rather than trim it is itself a data point worth sitting with.

**What's puzzling or worth watching.** This is the second clean-slate restart in roughly two weeks. The first restart (May 24) produced re-concentration into MSFT via pyramiding, which is the exact failure the second restart is designed to prevent. The new caps and manage-only logic are untested in live conditions, so the question of whether they hold is open. Also worth watching: the EOD journal entry for June 5 is incomplete — the "what we learned" field was never finished. That's a small thing, but it's the kind of friction that tends to compound when the system is under stress.

**Reflective prompts for Klaas.** The first restart didn't prevent re-concentration — what specifically will be different this time, and how will you know within the first week whether the new caps are actually binding rather than just specified? The decision to liquidate and restart rather than trim MSFT to 25% and correct in place was made twice now — what is driving the preference for clean slates over surgical fixes, and is that preference serving the strategy or avoiding a harder judgment call? If the next rebuild from cash produces a book that looks diversified on day one but drifts toward concentration again by week three, what is the decision rule you will use — trim mechanically, restart again, or something else?


---

## [NOTE] 2026-06-06 Saturday — clean-slate restart decided (flatten Monday 06-08)
**What happened.** Klaas chose to sell ALL positions and start over so the just-revised strategy rebuilds from a clean cash slate (the current book — MSFT 48%, V 24%, MCO 24%, 3% cash — is a legacy artifact of old sizing, not the current system). Dry-ran `liquidate_all.py`: 3 positions = $100,098 gross → ~$103,210 cash, realizing ~+$1,300 paper P&L. Pushed `SKIP_TODAY.flag` dated 2026-06-08 so Monday's execute.yml stands down while we flatten.
**What we learned.** This is the second clean restart (first was 2026-05-24) — the book re-concentrated via MSFT pyramiding after the last one, so watch whether the new manage-only + per-name/sector caps actually keep the rebuild diversified this time. Market is closed (Saturday), so the live flatten must run Monday during RTH via `liquidate.yml` (workflow_dispatch, confirm = "LIQUIDATE ALL"). Starting from cash also gives a clean performance baseline vs the SPY+cash shadow.
**Open questions.** Will the revised AM prompt (honest objective + regime + capacity context) rebuild a sensible, diversified book from cash, or re-concentrate / stall on unfilled limit entries (the new ledger will show fills)? Does the pyramid logic need a guardrail so it can't push a single name past 25% again?
**Tomorrow's plan.** Monday 06-08 during RTH (09:35–15:30 ET): dispatch `liquidate.yml` with confirm "LIQUIDATE ALL" to flatten to cash. Then let Tuesday's AM screen rebuild fresh; watch the new email sections (ledger, regime/capacity, leading SPY scoreboard).

