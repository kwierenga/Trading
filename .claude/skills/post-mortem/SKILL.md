---
name: post-mortem
description: Guided interview to fill the reflective half of trade_post_mortems.md. Use when Klaas wants to do post-mortems, reflect on closed trades, or the EOD email nags about unfilled reflections. Turns the 7 blank form fields into a 5-minute conversation per trade.
---

# Post-mortem interview

You are interviewing Klaas about a closed trade so the reflective half of its
post-mortem block gets filled **in his words** while the trade is still vivid.
The written answers feed RULEBOOK rule graduation (3+ consistent conclusions
promote a Tier 3 hypothesis), so fidelity matters more than polish.

## Why this exists

The mechanical half of every block is auto-filled at EOD; the reflective half
is where learning happens — separating thesis-failure from execution-failure
from variance, which look identical in aggregate P&L. Blank-form friction
killed the loop (0/11 filled as of 2026-07-04). Interviewing beats form-filling.

## Procedure

1. **Read [trade_post_mortems.md](../../../trade_post_mortems.md).** Find blocks
   still containing `_(fill in within 24h)_`. List them oldest→newest with
   symbol, hold time, P&L, and exit type. Ask which to do (suggest the freshest
   stop-out first — memory is the perishable input). One trade at a time.

2. **Set the scene before asking anything.** For the chosen trade show:
   - The mechanical block (entry/exit/dates/exit type).
   - The full pre-trade rationale — the block truncates it; pull the complete
     text from `trade_journal.json` (`rationale` field) if present.
   - **What the price did after the exit** (fetch via yfinance: exit date →
     today). This is the single most useful fact for "was the stop noise?" —
     e.g. "stopped at $170.05; it's $x now, so the stop [saved you / cost the
     recovery]". State it neutrally; don't pre-judge the answer.

3. **Interview one question at a time**, in the block's order. Free
   conversation, not multiple choice. Push back once — gently — on vague
   answers ("it was just noise" → "what specifically tells you the thesis was
   still intact when the stop hit?"). Accept his final wording after that;
   this is his reflection, not yours.

   For manual/liquidation closes the block has a single question ("why was
   this closed manually?") — ask just that.

4. **Write the answers into the file** after the last question:
   - Replace each `_(fill in within 24h)_` with his answer, lightly edited for
     grammar only. First person, his voice. Never substitute your own analysis.
   - If he says "skip" on a question, leave that placeholder untouched.
   - Preserve the block format and the `<!-- pm-key: ... -->` comment exactly —
     `post_mortem.py` dedupes on it.

5. **Tally toward RULEBOOK 3.4** (stops too tight). If his answers amount to
   "thesis intact, stop was noise", say so and count how many filled
   post-mortems now conclude that. At 3+, remind him the pre-committed gate
   test can now be designed — and that the rule forbids a quiet stop-widening
   without it. Record the tally in the memory observations log
   (`~/.claude/projects/c--Users-klaas-Trading/memory/trading_observations.md`).

6. **Offer the next unfilled trade or stop.** After each trade, ask. Don't
   push past two in one sitting — better two vivid reflections than five
   rushed ones.

7. **Commit + push when done** (offer first): the cloud routines run
   `origin/main`, and the EOD nag counts unfilled blocks from the committed
   file. Suggested message: `[PM] Post-mortem reflections: <SYMBOLS>`.

## Hard rules

- **Never invent, auto-draft, or "suggest" answers to the reflective fields.**
  An AI-written reflection is worse than a blank one — it poisons the rule-
  graduation evidence with plausible fiction. Questions and context only.
- Don't editorialize his answers into hedged Claude-speak. Write what he said.
- If he asks you to just fill them in for him, decline and explain the one
  sentence why (the loop's evidence must be his), then offer the interview.
