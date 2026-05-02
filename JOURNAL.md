# Trading Journal

Daily log: 4 sections per entry, ~3 sentences each. Newest at top. Read this in the morning to catch up on what happened, what we learned, what we're testing, and what's next.

**Sections (in order):**
1. **What happened** — concrete events: trades, market moves, decisions, code changes.
2. **What we learned** — patterns observed or hypotheses confirmed/refuted.
3. **Open questions** — hypotheses still being tested; what we don't yet know.
4. **Tomorrow's plan** — the next concrete action.

Cap: 3 sentences per section. If you need more, it belongs in a memo, not the journal.

---

## [WEEK] 2026-04-21 → 2026-05-02

**What worked / what didn't**

With zero closed trades and one open position (NNDM, +10.9% unrealized on a $2 allocation), there is almost nothing to evaluate on execution this week. The strategy generated candidate entries — PYPL, FTNT, MSFT, ADBE, APP appeared across two near-identical Saturday journal entries — but none were placed. The only concrete outcome is that NNDM moved in the right direction on a position so small it is functionally irrelevant to the portfolio. It is worth being precise: nothing worked or didn't work this week in any testable sense, because no decisions were completed.

**What's puzzling or worth watching**

Two things stand out. First, the Saturday journal was duplicated almost verbatim, with the candidate list quietly swapped (FTNT/MSFT replaced by ADBE/APP) — but no note explaining the change or what prompted it. That substitution is a decision, and it happened without a visible rationale. Second, the $2 position in NNDM sits in a portfolio with $100k cash. Either this is a deliberate placeholder to test the system, or position sizing broke down badly at entry. Which it is matters considerably for what comes next.

**Reflective prompts for Klaas**

What caused the FTNT and MSFT candidates to be dropped in favor of ADBE and APP between the two Saturday entries, and is that reasoning written down anywhere? The journal asks whether limit orders will fill, but the orders were never placed — what actually stopped execution from happening on a Saturday when you had a dry-run plan ready? NNDM is up 10.9% on a $2 stake, which means roughly $0.22 of gain: at what position size would this thesis have been worth acting on, and does the current sizing reflect conviction or something else?


---

## [AM] 2026-05-02 Saturday
**Open questions.** Will the 3 proposed entries (PYPL, FTNT, MSFT) fill at limit, or run away pre-market? What's the one thing that could derail the 52% confidence target? Any overnight news on these names worth checking before placing orders? 
**Today's plan.** Run `python execute_strategy.py --dry-run` to preview sizing, then `python execute_strategy.py` to place bracket orders. Monitor 1 open position(s) for thesis-break, stop hits, or LTCG-approaching flags. 

