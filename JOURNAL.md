# Trading Journal

Daily log: 4 sections per entry, ~3 sentences each. Newest at top. Read this in the morning to catch up on what happened, what we learned, what we're testing, and what's next.

**Sections (in order):**
1. **What happened** — concrete events: trades, market moves, decisions, code changes.
2. **What we learned** — patterns observed or hypotheses confirmed/refuted.
3. **Open questions** — hypotheses still being tested; what we don't yet know.
4. **Tomorrow's plan** — the next concrete action.

Cap: 3 sentences per section. If you need more, it belongs in a memo, not the journal.

---

## [WEEK] 2026-05-19 → 2026-05-31

**What worked / what didn't.** No positions closed this week, so there's nothing to evaluate on execution quality or exit discipline. What is visible is that the three open positions are all in positive territory — MSFT notably so at +9.3% — but unrealized gains at this concentration (MSFT alone is roughly half the portfolio) are not the same as captured gains. The one concrete decision this week was the real-money architecture question, and the reasoning held up: the algo correctly identified its own floor and didn't get forced into a degraded live run. That's the system working as designed, not a compromise.

**What's puzzling or worth watching.** The journal's Friday EOD entry has a blank where the actual observation should be — "what surprised you, what hypothesis got confirmed" was never filled in. That's a small but consistent data-loss pattern worth noticing: the AM entry asks sharp forward questions, but the EOD reflection that closes the loop is the one that gets skipped. Separately, with cash at 3% of equity, there's almost no dry powder. If any of the three positions hits a thesis-break before new cash arrives, the response options are limited to selling, not rotating.

**Reflective prompts for Klaas.** The MSFT position is now 50% of the portfolio at cost and growing as a share of equity — at what unrealized gain level, or what external signal, would you feel the concentration has become the thesis rather than a side effect of it? The Friday EOD review is the one habit that keeps slipping; is the 15-minute format wrong for end-of-week Fridays specifically, or is something else making it easy to skip? And on the real-money question: the decision to defer the algo until ~$3k funding is sensible on paper, but what's the actual funding timeline — is there a concrete plan, or is $3k a number that could drift indefinitely?


---

## [NOTE] 2026-05-30 Saturday — first real-money decision
**What happened.** Klaas said he's ready to put real money to work ($510) and asked to run "our algorithm" live on Alpaca. We worked through it and landed on a different plan: buy SPY by hand for now, defer the automated algo until the account is funded to ~$3k.
**What we learned.** $510 is below the architecture's floor — whole-share bracket orders + the 25% cap + `int()` share-rounding in `position_sizer.py` mean most quality names size to 0 shares and get skipped, so the algo would screen daily and place nothing. Running it live at this size would also require replacing exchange-resident bracket stops with software-side stops (a *weakening* of a safety rule) — not worth it for $510. Reaffirmed the standing seven-backtest finding: the algo doesn't beat buy-hold SPY, so passive SPY is also the better expected-value use of the cash.
**Open questions.** At what funding level does the algo run as-is (whole shares, 25% cap with room) vs. still needing a deliberate fractional/micro-size redesign? Does Klaas want to keep adding to SPY in tranches as cash arrives, or wait and lump-sum at ~$3k?
**Tomorrow's plan.** Klaas buys ~$505 of SPY (notional market order, no API key needed) in the Alpaca live app during Monday's RTH; hold, no stop/target. Paper algo keeps running untouched as the learning lab. Revisit the algo-live question when the account reaches ~$3k.

