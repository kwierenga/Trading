# Trading strategy — top 10 consolidated

This is the cut-down list of what to actually do. Picked from the longer
[RECOMMENDATIONS.md](RECOMMENDATIONS.md) (items 1-10) and
[RECOMMENDATIONS_part2.md](RECOMMENDATIONS_part2.md) (items 11-20), which
remain as backup detail for each item. The number in `[ref #X]` points back
to the originating rec in those docs.

**Selection criteria:** highest *(impact × confidence) / effort*, with a
hard preference for (a) bug fixes, (b) foundations that compound, and
(c) the one gate that must clear before real money flips in August.

---

## TL;DR — what changes if you do these 10

| Lever | Direct effect |
|---|---|
| #1 trail+scale, #5 pyramid | Stop capping winners. Let the 1–2 working trades pay for the book. |
| #2 conviction sizing, #6 sector cap | Allocate capital to edge, not to slot machines. Stop accidental 75%-tech weeks. |
| #3 earnings filter, #4 estimate-revision factor | Remove biggest avoidable losses; add the strongest known single equity factor. |
| #7 journal reconciliation, #8 shadow benchmark, #9 Alpaca-quote fix | Foundation: real stats, real benchmark, real prices. Nothing measures without these. |
| #10 Phase-0 backtest | The gate. No real-money flip without it clearing. |

Realistic outcome with all 10 done: **12–22% net-of-tax annualized**, max
DD 10–18%. The +1%/week (~67% annualized) target is not reachable inside
the current stocks-only / no-leverage envelope — recalibrate the target
or revisit the leverage constraint, those are the only two paths.

---

## What I dropped from the original 20 (so you can object)

| # in 20-doc | Title | Why dropped |
|---|---|---|
| #7 | Liquidity & spread floor | Doesn't bind at $100k paper or $3k Phase-2. Build when it starts to matter (Phase-3). |
| #8 | Weekly thesis check | Second-order; trail stops (rec #1 here) capture most of the same protection automatically. |
| #10 | Regime + Form 4 hybrid wiring | Per `trading_downtrend_mode.md` memory — explicitly deferred to *next* paper window. Don't pull it forward. |
| #12 | Mean-reversion entry mode | Real edge but expansion, not foundation. Add after Phase-0 backtest validates the base strategy. |
| #13 | Asymmetric re-eval fallback | Small impact; the mechanical fallback only fires when LLM call fails. Nice-to-have. |
| #14 | Time-stops | Capital efficiency item; small dollar impact at this size. Defer. |
| #16 | Two-pass LLM rule-check | Real discipline value but rule violations have been ~zero so far. Build if a real violation slips through. |
| #17 | VIX-aware sizing | Good envelope, but sector cap (rec #6 here) and conviction sizing (rec #2 here) attack the same volatility risk more directly. |
| #18 | Tax-loss harvesting | Dollar-relevant only at year-end and at scale. Schedule for late November as a one-off, not a system feature. |
| #19 | Proposal log | Foundational for *future* attribution work, but doesn't change returns this paper window. Add when you start asking "why did pick X work but Y didn't." |

If any of those feel wrong to drop, flag it and we can swap.

---

## TIER A — Return engine (do first, in order)

### 1. Trail stops + scale-out at +1R / +2R  *[ref #1]*

**Highest single expected-return delta** on this entire list. Today every
winner is capped at the static bracket target. A basing setup that runs
+30-60% over 3 months gets exited at +14-19%.

- Persist `R = entry − stop` on the trade record at submit time.
- Daily EOD: at `+1R` raise stop to break-even and sell 25% at market;
  at `+2R` raise stop to `entry + 1R` and sell another 25%; let the final
  50% trail by `1.5 × ATR(14)` daily.
- Replace the static take-profit leg of the bracket with this schedule.

**Where:** new `position_manager.py`, called from `eod_routine.py`.
Modify `execute_strategy.py` to log initial R.
**Effort:** ~1 day, ~150 LOC. Mostly Alpaca order replacement plumbing.

---

### 2. Conviction → position size mapping  *[ref #3]*

**30 minutes.** Free win. Claude already emits a `conviction` field on
every proposed trade and the field is currently discarded — every approved
trade gets the maximum 25% concentration regardless.

```python
conviction_multiplier = {
    (80, 101): 1.00,   # full size
    (70,  80): 0.75,
    (60,  70): 0.50,
    (0,   60): 0.00,   # skip, too uncertain
}
```

Apply in `execute_strategy.py` where `sizer.calculate_for_trade` is called.

**Effort:** 30 min. Single function.

---

### 3. Earnings-calendar filter  *[ref #4]*

A bracket entered the day before earnings is a coin-flip with asymmetric
downside (a -15% gap blows past your stop, Alpaca fills *below* it).
Single cheapest source of avoided drawdowns.

- Exclude candidates with earnings in the next **5 trading days**.
- Exclude candidates with earnings in the **last 2 trading days** that
  gapped >5% (post-earnings drift is information-rich and we're not yet
  modeling it).
- Surface `next_earnings_date` in the snapshot rendered for Claude.

**Where:** new `earnings_calendar.py` (yfinance `Ticker.get_earnings_dates`),
called from `market_data.passes_quality_filter` and `re_evaluate.py`.
**Effort:** 2–3 hours.

---

### 4. Analyst-estimate-revision factor in the screen  *[ref #15]*

**Single strongest single equity factor in 50+ years of academic research**
(post-earnings-announcement drift; Bernard & Thomas; Chordia et al). Sign
is robust across decades and markets. Currently zero such signal in the
screen — Claude *might* infer it from quarterly numbers ad hoc but not
systematically.

Per candidate, fetch trailing-60-day EPS estimate revisions and feed into
`_setup_score`:

| Revision (60d) | Score |
|---|---|
| > +5% | +5 |
| 0 to +5% | +2 |
| -2% to 0% | 0 |
| < -2% | −3 |

**Where:** `market_data.get_fundamentals` adds `eps_estimate_60d_change`
(yfinance has it via `Ticker.earnings_estimate` / `recommendations_summary`),
`_setup_score` uses it.
**Effort:** Half a day.

---

### 5. Pyramid (add tranche) on confirmed winners  *[ref #2]*

The strategy doc explicitly says "deploy cash reserve for adding to
winners" and the code path doesn't exist. With three names at 25% each,
the dry powder just sits.

- Position held ≥ 5 trading days, unrealized ≥ +5%, price above SMA20,
  no SMA50-slope flip → propose an add of 8-12% equity at next AM cycle.
- Concentration cap raised to **30%** for adds (vs 25% for fresh).
- Stop on combined cost basis at break-even.
- Max **2 adds** per position.
- Still respects sector cap (rec #6) and earnings filter (rec #3).

Combined with rec #1, this is the asymmetric compounder: trail to lock
ground gained, pyramid only on what's already working.

**Where:** same `position_manager.py` as rec #1, plus a `pyramid_eligible`
flag on the daily strategy JSON.
**Effort:** ~1 day on top of #1's plumbing.

---

## TIER B — Risk envelope

### 6. Sector concentration cap (35% per GICS sector)  *[ref #6]*

May 8 plan was MSFT + ADSK + APP — **all three software**. Three 25%
positions = 75% in one sector. Per-name cap doesn't catch this. One bad
NDX day takes the entire book in lockstep.

Sum existing positions + proposed trade by sector; reject if any one
sector would exceed 35% of equity.

**Where:** new `position_sizer.validate_sector_concentration`, called
from `execute_strategy.py` after the per-name check.
**Effort:** 2 hours. yfinance already returns `sector`.

---

## TIER C — Foundation (must run before everything else compounds)

### 7. Fix the broken trade journal (Alpaca reconciliation)  *[ref #5]*

`trade_journal.json` currently shows 17 entries, all `status=open`. The
journal hasn't matched reality since at least May 2. `TradeJournal.
get_statistics()` returns `win_rate=0, wins=0, losses=0, profit_factor=0`,
which goes straight into Claude's daily prompt as the HISTORICAL
PERFORMANCE block.

**Claude is choosing trades while believing it has no track record.**
Every other rec on this list compounds only when this is fixed.

- EOD: fetch Alpaca orders + positions for the last 7 days.
- For each fill with no matching journal entry: insert one
  (`source='alpaca_reconciled'`).
- For each `status=open` entry no longer in Alpaca positions: mark closed
  with the actual exit price and inferred reason.
- De-dup the May-2 replay (entries 1-12 are 3 orders submitted 4 times).
- Print a reconciliation report into the EOD email.

**Where:** new `journal_reconcile.py`, called at start of `eod_routine.py`.
**Effort:** Half a day.

---

### 8. Shadow benchmark portfolio (SPY 70% + USMV 30%)  *[ref #11]*

You currently cannot answer the question the Phase-2 graduation criteria
in `trading_rollout_plan.md` are *defined on*: "Net-of-tax CAGR > SPY by
≥1%", "Beats SPY in ≥55% of rolling 3-year windows." Without this, the
August real-money decision has no defensible basis.

- Maintain `shadow_portfolio.json`: $100k into 70% SPY + 30% USMV on
  day 1, never traded.
- Update daily in EOD; report strategy spread vs shadow weekly and monthly.
- Append the spread line to the EOD email.

If Phase-0 backtest (rec #10) fails, the shadow blend becomes the explicit
real-money fallback.

**Where:** new `shadow_benchmark.py`, called from `eod_routine.py`.
**Effort:** Half a day.

---

### 9. Use Alpaca quote API for re-eval intraday prices  *[ref #20]*

**This is a bug, not an enhancement.** `market_data.latest_price` returns
yfinance prints that can be 15–90 minutes stale at 09:35 ET — sometimes
the prior close, sometimes a pre-market last, sometimes an early-session
print. `re_evaluate.py` computes `overnight_pct` and decides submit/skip
based on this stale data, during the most volatile window of the day.

Swap to `/v2/stocks/{symbol}/quotes/latest` (Alpaca, free, real-time,
already authenticated). Use bid-ask midpoint.

**Where:** new `AlpacaClient.get_latest_quote`; replace the call inside
`re_evaluate.attach_current_prices`.
**Effort:** 1–2 hours.

---

### 10. **Run the Phase-0 backtest before flipping real money in August**  *[ref #9]*

Per the locked rollout plan (`memory/trading_rollout_plan.md`), Phase 0
was scheduled May 4–31 and **appears to have been skipped** in favor of
straight paper trading. The Aug-1 Phase-2 real-money flip currently rests
on ~2 weeks of paper trading and N ≈ 10 trades — statistically
indistinguishable from coin-flipping.

Rewrite `backtest_engine.py` to use the *actual* strategy
(`screen_sp500` + `_setup_score` ranking + ATR sizing + trail/scale per
rec #1 + earnings filter per rec #3) and run the locked protocol:

- 2010–2018 in-sample (parameter locking).
- 2019–2025 out-of-sample (**run once**, accept the answer).
- 1.5% survivorship-bias haircut, 5 bps/side transaction costs,
  32% STCG / 15% LTCG tax model.
- Five graduation gates per the rollout-plan memory.

If it passes (≥4 of 5 gates): everything else on this list compounds with
real confidence. If it fails (≤3): the right move is to *not* trade this
strategy at all — and you've saved real capital from a failed experiment.

**This is the single most important rec on the list for protecting real-
money capital**, even though it's not the highest expected-return lever.

**Effort:** 3–5 days. Worth it.

---

## Sequencing — 5 weeks, do in this order

| Week | Items | Outcome |
|---|---|---|
| **1 (foundation + bugs)** | #7 journal reconciliation, #9 Alpaca-quote fix, #8 shadow benchmark, #2 conviction sizing, #3 earnings filter | Real stats, real prices, real benchmark, smarter sizing, no earnings blow-ups. Most of these are <½ day each. |
| **2 (return engine)** | #1 trail + scale-out, #4 analyst-estimate revisions | The two biggest expected-return levers in place. |
| **3 (compounding + risk)** | #5 pyramid on winners, #6 sector cap | Asymmetric upside switched on; correlated-blowup risk removed. |
| **4–5 (the gate)** | #10 Phase-0 backtest with the strategy above | Pass → proceed to August flip. Fail → buy the shadow blend with the $3k and don't trade. |

After week 5, *then* consider the items dropped from the 20: liquidity
floor, weekly thesis check, VIX sizing, mean-reversion mode, proposal
log. Each is a real idea — they just don't make the top 10 *until the
foundation underneath them is solid*.

---

## Three open questions (unchanged from the 20-doc, still unanswered)

1. **Is the +1%/week target negotiable** if it forces breaking other
   rules? The math doesn't square without leverage. Recalibrating to
   12–22% net-of-tax annualized may itself preserve more capital than
   chasing the original number.
2. Phase 0 was scheduled May 4–31. **Skip-on-purpose or crowded out?**
   If on-purpose, why? If crowded out, rec #10 is the catch-up.
3. The **journal-reconciliation gap (rec #7)** means we don't actually
   know what trades have closed in May. Want the reconciliation to also
   produce a "what really happened May 1–12" report so the missing
   context is filled in?
