# Trading strategy — 10 recommendations to materially improve returns

Written overnight 2026-05-12 → for review 2026-05-13. Deep-dive across the
strategy code, position sizer, risk path, journal, AM/EOD routines, and the
last ~10 days of activity. **Nothing has been changed in code yet** — this is
a prioritized proposal list. Each item names the file(s) to touch and an
estimated effort.

---

## TL;DR — the four things that actually matter

| # | Lever | Why it's the lever |
|---|---|---|
| **1** | **Trail stops + scale-out at +1R / +2R** | The current bracket caps every winner at the static target. A quality basing setup that runs +25-40% over months is currently exited at +14-19%. Single biggest expected-return delta. |
| **2** | **Pyramid into confirmed winners** | All conviction is expressed at entry. There's no code path to add to a name that's working. The strategy doc literally says "deploy reserve for adding to winners" — but the mechanism doesn't exist. |
| **3** | **Earnings-calendar filter** | A bracket entry the day before earnings is a coin-flip. Easy to block. Cuts the worst losses. |
| **4** | **Fix the broken trade journal** | `trade_journal.json` has 17 entries, all `status=open`. None of the recent paper closes show up. Claude's prompt context (`build_rich_context`) feeds win-rate=0%, profit_factor=0, no recent trades. The whole learning loop is dead-ended. |

---

## Honest math reality-check (read this first)

The stated goal is **+1%/week with 70% confidence ≈ +67% annualized**, with
**≤15% per-name DD**, **stocks-only**, **low turnover**, and **S&P 500 only**.

Pick two of three: low turnover, low DD, +67% annual. The constraint stack
forbids the standard ways to hit that return (leverage, options, momentum
breakouts, micro-caps). Realistic CAGR ranges for the strategy as written:

| Scenario | Expected CAGR | Max DD | What's required |
|---|---|---|---|
| Strategy as-written, no changes | 6–12% | 8–15% | Quality value held to LTCG, ~2 trades/month |
| With recs #1–#5 below | 12–22% | 10–18% | Letting winners run, sizing to conviction, avoiding earnings blow-ups |
| With #1–#5 + accept 2x reg-T leverage on highest-conviction names | 18–35% | 18–30% | Klaas would have to revisit his "no leverage" line |

The +1%/week target is unlikely without leverage or wider universe — but
**12–22% net of tax is genuinely good** for a part-time stocks-only strategy
on a US-tax-aware book. Recalibrating the target may itself be a return-
preserving move (chasing 1%/week tempts breaking the rules that protect DD).

---

## TIER A — Direct return-boosters

### 1. Trailing stops + scale-out at +1R/+2R (HIGHEST EXPECTED IMPACT)

**Where:** new module `position_manager.py` invoked daily by `eod_routine.py`,
plus modifications to `execute_strategy.py` to log initial R per trade.

**What:**
- Compute **R = entry − stop** at trade entry; persist it on the trade
  record. (Currently lost after order submit.)
- Daily EOD: for each open Alpaca position, if `unrealized_gain ≥ 1R`, raise
  stop to **break-even + commission cushion** (cancel old stop leg, submit
  new one). At `≥2R`, raise stop to `entry + 1R`. At `≥3R`, trail by
  `1.5×ATR(14)` on a daily basis.
- **Scale-out**: at `+1R`, sell **25%** at market. At `+2R`, sell another
  **25%**. Let the final 50% ride the trail. This locks ~75% of expected
  gain even if the trail eventually catches the stock, while keeping a free
  call on multi-bagger upside.
- Replace the static take-profit leg of the bracket with the scale-out
  schedule above (target only fires the final 50%).

**Why this is #1:** Look at what would have happened with PYPL/MSFT/FTNT
proposed entries — the current static targets cap MSFT at +14.4% and APP at
+19.4%. A basing-pattern winner that breaks out commonly does +30-60% over
2-4 months in this universe. Even capturing half of that on the back 50%
adds meaningfully more than the entire +1R lock-in. The downside is
asymmetric: you give up nothing if the stock goes against you (you exit at
the original stop on full size before any scale-out fires).

**Effort:** 1 day. Adds ~150 LOC. Mostly Alpaca API plumbing.

---

### 2. Pyramid (add tranche) on winners that confirm

**Where:** `position_manager.py` (same as #1), plus a new flag on
`latest_strategy.json` schema: `pyramid_eligible: true|false`.

**What:**
- For positions held >= 5 trading days with unrealized_gain >= +5% AND price
  above 20-day MA AND no negative SMA50 slope flip: propose an "add" trade
  of **8-12% of equity** at the next AM cycle, with stop set to `breakeven
  on combined cost basis`.
- Concentration cap raises to **30%** for pyramid adds (vs 25% for fresh
  entries) only when initial tranche already shows +5% — the cap was set
  for fresh, untested ideas; a confirmed winner is a different risk profile.
- Hard cap: max 2 adds per position. Beyond that, just trail.
- Does NOT bypass any other rule: still respects sector cap (rec #7),
  earnings filter (rec #4), portfolio heat.

**Why:** Strategy doc says "Cash reserve of ~34% maintained as dry powder
for adding to winners" — there's no code path for this. With three names at
25% each, the dry powder just sits. Pyramiding on the 1-2 working positions
is how concentrated strategies actually compound. Combined with rec #1's
trailing stops, this is "pure asymmetric": add only when already winning,
trail to lock in ground gained.

**Effort:** 1 day on top of #1's plumbing.

---

### 3. Conviction → position size mapping (use Claude's signal)

**Where:** `execute_strategy.py:127` (where `sizer.calculate_for_trade` is called).

**What:**

```python
conviction_multiplier = {
    range(80, 101): 1.00,  # full size
    range(70, 80):  0.75,
    range(60, 70):  0.50,
    range(0, 60):   0.00,  # skip — too uncertain to risk capital
}
```

Apply multiplier to recommended shares. Currently every approved trade gets
the maximum 25% concentration regardless of conviction. The May-8 plan had
trades at 78%, 74%, and 70% conviction — all three got 25% size. A 70%-
conviction trade should not be sized identically to a 90%-conviction one;
the LLM's stated uncertainty is signal that's currently discarded.

**Why:** Claude's `conviction` field already exists and Claude actually
uses it well (variation on each call). Costs nothing to use. Lower
expected DD on weak setups, higher implicit allocation to strong ones.
**Combined with #1 and #2, this mechanically tilts the book toward the
trades with the best edge** without any new screen logic.

**Effort:** 30 minutes. Single function.

---

### 4. Earnings-calendar filter (avoid binary blow-ups)

**Where:** new helper `earnings_calendar.py` using yfinance `.calendar` (or
`Ticker.get_earnings_dates(limit=4)`); call from `re_evaluate.py` AND from
the screen in `market_data.py` (so candidates within 5 trading days of
earnings are excluded entirely from the prompt).

**What:**
- Skip any candidate with earnings in **next 5 trading days**.
- Skip any candidate with earnings in **last 2 trading days** that gapped
  >5% (post-earnings drift, but the gap is too information-rich to size
  blindly into it).
- Add `next_earnings_date` to the candidate snapshot rendered for Claude
  so the LLM can also factor it in for the survivors.

**Why:** Earnings produce single-day moves of ±5–20% on quality large caps.
A bracket order entered the day before earnings is a coin-flip with
asymmetric downside (a -15% gap blows past your stop and Alpaca fills at
worse than the stop price). Cheapest way to remove the largest single
source of avoidable drawdowns. **Conservative estimate: cuts annualized
volatility by ~20% with no expected-return cost.**

**Effort:** 2-3 hours. yfinance has `Ticker(sym).get_earnings_dates()`.

---

### 5. Fix the broken trade journal — Alpaca-reconciliation as source of truth

**Where:** new `journal_reconcile.py` invoked at the start of `eod_routine.py`.

**Current state:** `trade_journal.json` has 17 entries, all `status=open`.
JOURNAL.md says "5/12 some gains were made, and shares bought and sold" —
the Alpaca account had real fills and exits the journal never recorded.
This means `TradeJournal.get_statistics()` returns `win_rate=0`, `wins=0`,
`losses=0`, `profit_factor=0`, which goes straight into Claude's daily
prompt (`ai_strategy_enhanced.py:158-164`) as the "HISTORICAL PERFORMANCE"
block. **Claude is choosing trades while believing it has no track record.**

**What:**
1. At EOD, fetch all Alpaca orders + positions for the last 7 days.
2. For each Alpaca fill that has no matching journal entry: insert one with
   inferred metadata (mark `source='alpaca_reconciled'`).
3. For each journal `status=open` entry that no longer matches an open
   Alpaca position: mark closed with the actual exit price + reason
   (`stop_hit` / `target_hit` / `manual` inferred from order type).
4. De-dup: the May-2 entries 1-12 are obvious replays of the same 3 orders
   submitted 4 times; collapse to 3 entries.
5. Write a reconciliation report into the EOD email.

**Why:** Without this, none of the post-trade learning loop in
`memory/trading_learning_loop.md` actually has data to operate on. Recs
#1, #2, #3, #6 all benefit from accurate trade stats. The weekly review
prompt in `weekly_review.py` is also feeding empty data. **This is the
foundational fix that makes every other rec compound.**

**Effort:** Half a day. Mostly Alpaca order-history API + dedup logic.

---

## TIER B — Risk reduction & data quality

### 6. Sector concentration cap (35% per GICS sector)

**Where:** `position_sizer.py` — new function `validate_sector_concentration`,
called from `execute_strategy.py` after the existing per-name check.

**What:** Sum existing position values by sector + proposed trade. Reject
if any one sector would exceed 35% of equity.

**Why:** The May-8 plan was MSFT + ADSK + APP — **all three software**.
Three 25% positions = 75% in tech. The 25% per-name cap doesn't catch
this. A single bad day for NDX takes the entire book in correlated lock-
step. Any day Treasury yields move 50bps, the entire book moves the same
direction. Adding a sector cap costs ~zero return (Claude has 30 candidates
across 11 sectors to choose from) and meaningfully cuts tail risk.

**Effort:** 2 hours. yfinance already returns `sector` in the snapshot.

---

### 7. Liquidity & spread floor in the screen

**Where:** `market_data.passes_quality_filter` — add criteria.

**What:**
- Require **avg daily $-volume ≥ $50M over last 20 days** (currently no
  liquidity check at all — only mkt cap floor).
- Reject if **bid-ask spread > 0.05%** at last close.
- For Phase 2 ($3k real money), tighten to ≥$10M ADV — most S&P 500 names
  pass; some smaller index members don't.

**Why:** Doesn't matter at $100k paper, but Phase 2 ($3k) and Phase 3 ($20k
target) start to feel slippage on thinly-traded names. Build the rule now
so the strategy has been validated *with* liquidity hygiene before real
money flips. Costs nothing on the current account.

**Effort:** 1 hour.

---

### 8. Weekly thesis-check on open positions (catch breaks before stops)

**Where:** new `position_review.py`, fires Friday EOD via existing
`eod.yml` workflow with a phase argument.

**What:** For each open position:
- Re-fetch latest fundamentals: did revenue / EPS / margin trend break?
- Re-check trend: SMA50 slope still positive? Price still above SMA50?
- Re-fetch sector regime: did sector ETF break SMA50?
- Earnings since entry? If yes, did the gap match the trend direction?
- Form 4 in last 30 days for this issuer? Insider sells signal weakening
  internal conviction.
- If 2+ flags fire, recommend **manual exit before next week's open**, not
  waiting for a stop hit.

**Why:** Stops protect against price moves but miss thesis breaks that
unfold above the stop level. A revenue miss that drops MSFT 7% leaves it
above its $399 stop but invalidates the "Azure compounder" thesis the
trade was sized on. Currently the only review of an open position happens
at execute time of a *new* trade in the same name. **This is the cheapest
way to capture the discipline Klaas's user-memory says he struggles with
("loss management is harder").**

**Effort:** Half a day. Reuses `market_data.get_snapshot` + `regime.py`.

---

## TIER C — Foundation (must do before scaling capital)

### 9. **Actually run the Phase-0 backtest before flipping real money in August**

**Where:** rewrite `backtest_engine.py` to use the *real* strategy
(`screen_sp500` + `_setup_score` ranking + ATR sizing + bracket exits with
recs #1-#3 applied), not the placeholder generic engine that exists today.

**What (per the locked rollout plan — `memory/trading_rollout_plan.md`):**
- 2010–2018 in-sample (parameter locking)
- 2019–2025 out-of-sample (run **once**, accept the answer)
- 1.5% survivorship-bias haircut
- 5 bps/side transaction costs
- 32% STCG / 15% LTCG tax model
- Five graduation gates (CAGR vs SPY, DD ≤0.8× SPY's, etc.)

**Why:** Klaas has been paper-trading for ~2 weeks and is on a Phase-2
real-money flip target of August 1. **Phase 0 was scheduled May 4–31 and
appears to have been skipped entirely in favor of straight paper.** The
locked plan explicitly says "5 pass: proceed; ≤3: stop, buy SPY+USMV
blend." Without the backtest, Klaas has no out-of-sample evidence of
edge — he's about to put real money on a strategy whose only evidence is
~2 weeks of paper trading on N≈10 trades, which is statistically
indistinguishable from coin-flipping.

This is the single most important rec on this list **for protecting
real-money capital**, even though it's not the highest expected-return
lever. If the backtest fails the gates, the right move is *not* trading
this strategy at all — and you'll have saved real capital from a failed
experiment. If it passes, every other rec on this list compounds with
much higher confidence.

**Effort:** 3-5 days. Worth it.

---

### 10. Wire the Form 4 / regime-mode hybrid that's already built but unused

**Where:** `morning_routine.py:163` (insert regime gate before the screen).
Per `memory/trading_downtrend_mode.md` the data layer (`regime.py`,
`form4_data.py`, `form4_signals.py`) is shipped and tested but
**not wired**.

**What:** At AM-plan time:
1. `regime.detect_regime("SPY")` — if `is_downtrend=True`:
   - Replace the trend-mode candidate ranking with Form-4-cluster signals
     filtered to candidates that *also* pass the existing quality filter.
   - Tighten the quality filter floor (mkt cap → $5B, debt/equity < 200,
     positive EPS for trailing 4 quarters).
   - Send a different prompt header to Claude that explains the regime
     switch and asks for *zero or one* trade only, max 15% size.
2. If `is_uptrend`: current path unchanged.
3. If `mixed/sideways`: current path with sector cap raised to 50% (more
   permissive — sideways markets are harder to read, allow more concentration
   in the ones that do show conviction).

**Why:** Trend-mode self-suspends in downtrends (basing+no-falling-knife
filters exclude everything). Klaas sat in 100% cash through any selloff
day, missing exactly the time-window when insider buys are most predictive.
The hybrid was deliberately deferred per the memory note "wait until
current paper window closes" — this rec is to **schedule the wire-in for
the start of the next paper window**, not to do it mid-experiment.

Also: even outside downtrend mode, **insider cluster buys are useful as a
tie-breaker score** in ranked candidates. Add `+5` to `_setup_score` for
candidates with a Form-4 cluster in the last 30 days. Costs little, captures
a known-predictive signal.

**Effort:** Half a day to wire the regime gate. 1 day to add Form-4 as a
score factor in trend mode too.

---

## What I'm explicitly NOT recommending (and why)

- **Options or covered calls** — Klaas's stated comfort line. Don't push
  past it; the comfort goal is part of the project, not a bug.
- **Inverse ETFs (SH, PSQ)** — implicitly covered by the regime-mode plan
  via cash-up. Direct inverse exposure adds operational complexity for the
  same effect.
- **Averaging down on losers** — known retail killer. Pyramiding (rec #2)
  is the inverse: only adds to winners.
- **Higher-frequency intraday entries** — defeats the low-turnover/tax
  preference, doesn't survive transaction costs at $100k.
- **Multi-LLM ensemble for trade selection** — interesting but defers
  responsibility for decisions; doesn't solve the actual bottleneck (Phase
  0 backtest gap, broken journal).
- **Replacing yfinance entirely** — yfinance fundamentals ARE often stale
  but the cost/benefit of a paid data source isn't worth it until Phase 2+.
- **Adding the macro overlay** (VIX, yield curve, dollar) — adds variance
  to the signal, hard to validate without rec #9's backtest first.

---

## Suggested implementation sequence (if all 10 are agreed)

| Week | Items | Outcome |
|---|---|---|
| Week 1 | #5 (journal fix) + #4 (earnings filter) + #3 (conviction sizing) | Foundation: real stats, fewer blow-ups, smarter sizing |
| Week 2 | #1 (trail stops) + #6 (sector cap) + #7 (liquidity floor) | Risk-reduction layer in place |
| Week 3 | #2 (pyramid) + #8 (weekly thesis check) | Asymmetric upside engine running |
| Week 4-5 | **#9 (Phase 0 backtest)** before flipping anything real | The gate |
| Week 6+ | #10 (regime + Form 4 wiring) **at start of next paper window** | Hybrid ready |

That's roughly 4 weeks of part-time work, fitting before the August Phase-2
flip date. Cleanly orderable so each rec builds on the last.

---

## Open questions for Klaas

These didn't fit the 10-rec frame but matter:

1. Is the **+1%/week target negotiable** if it forces breaking other rules?
   The math doesn't square without leverage. Recalibrating to 12-22% net-of-
   tax annualized may itself preserve more capital than chasing the original
   number.
2. Phase 0 was supposed to be May 4–31 — **did you decide to skip it on
   purpose**, or did it get crowded out by infra work? If skip-on-purpose,
   why? If crowded-out, rec #9 is the catch-up. (No judgment either way —
   honest question.)
3. The **journal-reconciliation gap** (rec #5) means we don't actually know
   what trades have closed in May. Want the reconciliation to also produce a
   "what really happened May 1-12" report so the missing context is filled
   in?
