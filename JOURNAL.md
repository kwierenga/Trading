# Trading Journal

Daily log: 4 sections per entry, ~3 sentences each. Newest at top. Read this in the morning to catch up on what happened, what we learned, what we're testing, and what's next.

**Sections (in order):**
1. **What happened** — concrete events: trades, market moves, decisions, code changes.
2. **What we learned** — patterns observed or hypotheses confirmed/refuted.
3. **Open questions** — hypotheses still being tested; what we don't yet know.
4. **Tomorrow's plan** — the next concrete action.

Cap: 3 sentences per section. If you need more, it belongs in a memo, not the journal.

---

## [NOTE] 2026-05-30 Saturday — first real-money decision
**What happened.** Klaas said he's ready to put real money to work ($510) and asked to run "our algorithm" live on Alpaca. We worked through it and landed on a different plan: buy SPY by hand for now, defer the automated algo until the account is funded to ~$3k.
**What we learned.** $510 is below the architecture's floor — whole-share bracket orders + the 25% cap + `int()` share-rounding in `position_sizer.py` mean most quality names size to 0 shares and get skipped, so the algo would screen daily and place nothing. Running it live at this size would also require replacing exchange-resident bracket stops with software-side stops (a *weakening* of a safety rule) — not worth it for $510. Reaffirmed the standing seven-backtest finding: the algo doesn't beat buy-hold SPY, so passive SPY is also the better expected-value use of the cash.
**Open questions.** At what funding level does the algo run as-is (whole shares, 25% cap with room) vs. still needing a deliberate fractional/micro-size redesign? Does Klaas want to keep adding to SPY in tranches as cash arrives, or wait and lump-sum at ~$3k?
**Tomorrow's plan.** Klaas buys ~$505 of SPY (notional market order, no API key needed) in the Alpaca live app during Monday's RTH; hold, no stop/target. Paper algo keeps running untouched as the learning lab. Revisit the algo-live question when the account reaches ~$3k.

---

## [EOD] 2026-05-29 Friday
**What happened.** No trades closed today. End equity $107,314, cash $3,113 (3% of equity), 3 open position(s). 
**What we learned.** [Add 1-2 sentences during your 15-min review: what surprised you today, what hypothesis got confirmed or refuted, or what you noticed about the market.] 

---

## [AM] 2026-05-29 Friday
**Open questions.** Will the 1 proposed entries (MSFT) fill at limit, or run away pre-market? What's the one thing that could derail the 55% confidence target? Any overnight news on these names worth checking before placing orders? 
**Today's plan.** execute.yml fires automatically at 09:35 ET — re-evaluates each setup against the actual open and submits the survivors. To skip today, push SKIP_TODAY.flag with today's UTC date before 09:35 ET. Monitor 3 open position(s) for thesis-break, stop hits, or LTCG-approaching flags. 

