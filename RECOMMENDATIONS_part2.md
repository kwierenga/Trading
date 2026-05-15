# Trading strategy — recommendations 11-20 (companion to RECOMMENDATIONS.md)

Round 2 of the deep-dive. These are at the same priority bar as the first 10
— not a "leftover list." Some are higher-impact than items in TIER B/C of the
first doc; ordering across both docs is suggested at the bottom.

Each rec keeps the same format: **What** / **Where** / **Why** / **Effort**.

---

## TL;DR — round 2 highlights

| # | Lever | Why it matters |
|---|---|---|
| **11** | **Shadow benchmark (SPY + USMV) tracker** | You currently can't answer "does this strategy beat just buying SPY?" — and that's the question the Phase-2 real-money decision needs to be made on. |
| **12** | **Add a mean-reversion entry mode** | The basing-only filter rejects high-quality oversold pullbacks in confirmed uptrends. Doubles the addressable setup count without lowering quality. |
| **15** | **Analyst-estimate-revision factor** | Single most predictive equity factor in 50+ years of academic literature. Currently absent from the screen. |
| **18** | **Tax-loss harvesting + LTCG-aware partial sells** | Real saved-tax dollars at year-end. Scales with account size. |

---

## TIER A2 — Signal & opportunity expansion

### 11. Shadow benchmark portfolio (SPY + USMV passive baseline)

**What:** Maintain a parallel `shadow_portfolio.json` that simulates "$100k
into SPY (70%) + USMV (30%) on day-1, never traded." Update daily in EOD.
Report the strategy's spread vs shadow weekly and monthly.

**Where:** new `shadow_benchmark.py`, called from `eod_routine.py`. Append to
the EOD email and the weekly review.

**Why:** Right now the only metric is absolute equity. You cannot tell whether
+2% in a month was good (SPY did -1%) or bad (SPY did +5%). The Phase-2
graduation criteria in `memory/trading_rollout_plan.md` — "Net-of-tax CAGR >
SPY by ≥1%", "Beats SPY in ≥55% of rolling 3-year windows" — **require this
data and we have none of it**. Without it, the Aug-1 real-money decision has
no defensible basis. Cheap to build, hard to do without later.

Bonus: the shadow becomes the explicit fallback if Phase-0 backtest (rec #9)
fails — buy the shadow blend with the $3k.

**Effort:** Half a day. yfinance gives SPY+USMV daily closes; just track
shares-since-day-1 × current-close.

---

### 12. Mean-reversion oversold-in-uptrend entry mode

**What:** Add a second valid entry pattern beyond the current
`is_basing & range_position_pct < 50` rule:

```
mean_revert_setup = (
    in_uptrend                     # SMA50 rising, price above SMA50
    and 30 < range_position_pct < 70   # not at highs, not at lows
    and rsi14 < 35                 # oversold
    and pct_below_sma20 > 3        # pulled back ≥3% below SMA20
    and not in_downtrend
)
```

Send candidates passing EITHER pattern (basing OR mean-revert) to Claude with
the pattern type tagged. Score mean-revert setups slightly lower than basing
(setup_score +6 vs +10) — they're real but lower-conviction.

**Where:** `market_data.get_technicals` (add `rsi14` field), `market_data.
_setup_score` (add the mean-revert branch), and the prompt header in
`ai_strategy_enhanced.build_enhanced_claude_prompt` (note both patterns are
valid).

**Why:** The basing filter excludes a real category of high-quality entries:
quality compounders that pull back to SMA50 in the middle of an uptrend.
Classic example: AAPL or MSFT down 5-7% on a market-wide selloff with no
company news, RSI dips to 30, then snaps back within 2 weeks. The current
strategy passes on these because `range_position_pct > 50`. **Doubles the
opportunity set without dropping quality** (the fundamental floor is
unchanged; only the entry-pattern recognition expands).

**Effort:** Half a day. RSI is 10 lines; the rest is wiring.

---

### 13. Asymmetric mechanical fallback in `re_evaluate.py`

**What:** Current `mechanical_decision()` is asymmetric in an unhelpful way:
skip on gap-up >3%, skip on broken stop, otherwise submit. Add the missing
asymmetry on the *opportunity* side:

```
if -0.05 < cur/entry - 1 < -0.02:   # gapped DOWN 2-5% on no news
    return {"action": "submit",
            "limit_price": cur * 1.001,   # take the gift
            "rationale": "gapped down — better entry available"}
```

And the converse: if gapped down >5%, the news is probably real and bad —
revert to skip ("avoid catching knife on a fresh gap").

**Where:** `re_evaluate.mechanical_decision`.

**Why:** Quality names that gap down on macro noise without company news are
the single best entry. The current mechanical fallback fires only when the
LLM call fails; the LLM-based path *should* recognize this, but the
fallback should match. **Cheap insurance against an LLM API outage on a
volatile open day.**

**Effort:** 30 minutes. Single function. Add a "fresh news?" check by
comparing current price to pre-market level if available.

---

### 14. Time-stops — exit positions that have gone nowhere in 60 days

**What:** Daily EOD check: for each open position held > 60 days with
`abs(unrealized_pct) < 5%` AND not within 30 days of LTCG eligibility,
flag for exit on next AM cycle. Pass to Claude with the open-position context
so it can decide whether thesis still holds (and override the time-stop if
yes — but with a fresh-conviction stamp).

**Where:** `eod_routine.py` adds a `time_stop_candidates` field to the
journal entry; `morning_routine.py` surfaces them in the AM prompt.

**Why:** Capital efficiency. A position that's neither working nor failing
is opportunity cost — that 25% chunk of equity could be in a setup that's
actually moving. Stocks-only / low-turnover doesn't mean "no turnover."
Currently nothing forces exit of a stuck name. **Frees ~5-15% of capital
per quarter for fresh setups.** The LTCG-window protection prevents
tax-stupid forced sales (don't kick out a stuck name on day 350).

**Effort:** 2 hours. Mostly journal queries.

---

### 15. Analyst-estimate-revision factor in the screen

**What:** For each candidate, fetch trailing-60-day EPS-estimate revisions:
`(latest_estimate - estimate_60d_ago) / estimate_60d_ago`. Add to setup
score:

| Revision | Score |
|---|---|
| > +5% (analysts raising hard) | +5 |
| 0 to +5% (drift up) | +2 |
| -2% to 0% | 0 |
| < -2% | -3 |

Also surface `estimate_trend` in the prompt context so Claude can read it.

**Where:** `market_data.get_fundamentals` adds `eps_estimate_60d_change`;
`_setup_score` uses it. yfinance `Ticker.earnings_estimate` (or
`recommendations_summary`) provides analyst-mean estimates.

**Why:** **Earnings-estimate revisions are arguably the strongest single
equity factor in 50+ years of academic research** (post-earnings-
announcement-drift, Bernard & Thomas, Chordia et al). Magnitude varies but
the sign is robust across decades and markets. The current screen has zero
estimate-revision signal; the LLM might infer it from quarterly numbers but
not systematically. **This is the single highest-leverage scoring change
on this list.**

**Effort:** Half a day. yfinance has the data; the score branch is 5 lines.

---

### 16. Two-pass LLM rule-check (Haiku verifier behind Opus/Sonnet planner)

**What:** After `ai_strategy_enhanced` produces `latest_strategy.json`, send
the proposed trades + the literal CLAUDE.md rule list to a cheaper Haiku
call with a single instruction: *"Identify any trade that violates any
rule. Return JSON {violations: [...], all_clear: bool}."* If `all_clear=
false`, the violating trade is dropped (or escalated to email for manual
review) before re-eval and execute.

**Where:** new `rule_check.py` invoked between `morning_routine.run_plan_
phase` and the journal-write step.

**Why:** The Phase-1 graduation criterion is "≤1 violation of CLAUDE.md
rules in the period." Currently nothing systematically checks. The planner
LLM is given the rules but is also being asked to be creative — combining
"don't violate rules" + "find good trades" in one call is asking the model
to mark its own homework. A separate verifier with a single, narrow
mandate is the cheapest way to catch silent rule drift. Haiku at $0.25/M
input tokens makes this ~$0.001/day. **High value-per-dollar.**

**Effort:** 2 hours. Reuses the existing prompt scaffolding.

---

## TIER B2 — Risk envelope improvements

### 17. Volatility-regime sizing (VIX-aware)

**What:**

```
if vix < 15:    max_position_pct = 0.25  # current default
if 15 <= vix <= 25: max_position_pct = 0.20
if 25 < vix <= 35: max_position_pct = 0.15
if vix > 35: HALT new entries; manage open positions only.
```

Apply to `position_sizer.MAX_POSITION_PCT` dynamically per cycle.

**Where:** new helper `volatility_regime.py` (yfinance `^VIX` daily close).
Override `MAX_POSITION_PCT` at the top of each AM cycle.

**Why:** Realized return-per-unit-risk drops in high-VIX regimes (well-
documented). Position sizes that look reasonable at VIX=14 are recklessly
large at VIX=30. Cuts realized DD without much CAGR cost — the missed gains
in high-VIX bull runs are paid for by the avoided losses in high-VIX
selloffs. **Behavioral commitment device** in addition to statistical
edge: forces de-risking exactly when the FOMO instinct says to lean in.

**Effort:** 2 hours.

---

### 18. Tax-loss harvesting in December + LTCG-aware partial sells

**What:**
- **Late November / December check:** scan open positions for unrealized
  losses > 5%. For each, propose: (a) close the position now, (b) re-enter
  a *similar-but-not-substantially-identical* name after 31 days (the
  wash-sale window). Pre-build a "tax-loss pair" map per sector (e.g.,
  losing-MSFT pairs to a sale + 32-day-later re-entry; or pair to GOOG as
  the wash-sale-safe substitute for the interim).
- **LTCG-aware partial sells in rec #1's scale-out:** prefer to sell
  short-term lots over long-term lots when scaling out at +1R/+2R *unless*
  the short-term sale would push you into a higher tax bracket. Default:
  always prefer short-term lot for partial sells (you keep the LTCG-eligible
  lot for the eventual full exit).

**Where:** new `tax_management.py`. Lot accounting from Alpaca's
`/positions` doesn't include lot-by-lot data — you'll need to track it
yourself in the journal (entry timestamp = lot date).

**Why:** US short-term gains are taxed at up to 37% federal; LTCG at 15-20%.
Even a modest 12% gross-CAGR strategy can lose 3-5% to tax inefficiency
without active management. December tax-loss harvesting at $100k typically
saves $500-2000/year; at $20k Phase-3 levels, $100-400/year — small
absolute but high IRR on the time invested. **Free money you'd otherwise
write to the Treasury.**

**Effort:** 1 day. Lot tracking is the meaty part.

---

### 19. Persist proposed-but-not-filled trades for learning attribution

**What:** Append-only `proposals.jsonl` — every trade Claude proposes (AM)
gets logged with its full context AND its eventual disposition: `filled`,
`reeval_skipped`, `limit_never_hit`, `manually_rejected`. Update at execute
time and again at end-of-day.

**Where:** new `proposal_log.py`; hooks in `ai_strategy_enhanced` (write
proposals), `re_evaluate` (annotate skip), `execute_strategy` (annotate
fill).

**Why:** Currently `latest_strategy.json` is overwritten daily — the signal
"we proposed MSFT 3 days running and only filled day 3" is lost. Without
this you cannot answer:
- What % of proposed trades actually fill?
- Of those that didn't fill (limit too low), what would the P&L have been?
- Are re-eval skips usually right (gap-up never reverted) or wrong?
- Does Claude propose the same name day after day or rotate?

**This is the data spine for measuring whether the strategy components are
adding value, separately from the bottom-line P&L.**

**Effort:** 3 hours. Just append-only JSON Lines + 3 hook points.

---

### 20. Use Alpaca quote API (not yfinance) for re-eval intraday prices

**What:** Replace `market_data.latest_price` with `AlpacaClient.get_latest_
quote(symbol)` inside `re_evaluate.attach_current_prices`. Alpaca's
`/v2/stocks/{symbol}/quotes/latest` returns real-time bid/ask; use the
midpoint.

**Where:** `re_evaluate.py:55-62`, plus new `AlpacaClient.get_latest_
quote` method.

**Why:** This is a **bug**, not just an enhancement. yfinance prices are
typically 15-minute delayed. At 09:35 ET, `latest_price()` may return the
prior-close print, the pre-market last, or a 9:20 ET print depending on
which yfinance endpoint hit. The re-eval logic computes `overnight_pct`
and decides submit/skip based on this — sometimes operating on a 60-90
minute stale price during the volatile market open. Alpaca's quote API is
free, real-time, and already authenticated. **Pure execution-quality
improvement; non-trivial impact during the gap-up/gap-down decisions
re-eval was specifically built to make.**

**Effort:** 1-2 hours. Add the method + swap the call.

---

## Combined priority across both docs (1-20)

If asked "what would you do first if you only had a week," I'd order all 20
items by **expected impact / effort**:

### Week 1 (foundation + bugs)
- **#5** journal reconciliation (foundation)
- **#20** Alpaca-quote bug fix in re-eval (real bug)
- **#11** shadow benchmark tracker (measurement foundation)
- **#3** conviction → size mapping (free win, 30 min)
- **#4** earnings filter (cheap risk reduction)

### Week 2 (return-boosters)
- **#1** trailing stops + scale-out (#1 expected-return delta)
- **#15** analyst-estimate-revision factor (highest-impact signal addition)
- **#19** proposal log (learning data spine)
- **#16** two-pass LLM rule-check (cheap insurance)

### Week 3 (opportunity expansion)
- **#2** pyramid on confirmed winners
- **#12** mean-reversion entry mode
- **#13** asymmetric re-eval fallback
- **#6** sector cap

### Week 4 (risk envelope)
- **#7** liquidity floor
- **#8** weekly thesis check
- **#14** time-stops
- **#17** VIX-aware sizing
- **#18** tax-loss harvesting

### Pre-Phase-2 (mandatory before real money)
- **#9** Phase-0 backtest with the actual strategy
- **#10** regime + Form 4 hybrid wiring (start of next paper window)

---

## Things I considered but rejected for round 2

- **Multi-LLM ensemble** (run trades through 3 different models, vote): adds
  cost and operational complexity for marginal expected lift. Two-pass
  rule-check (#16) captures most of the discipline benefit at a fraction
  of the cost.
- **Pre-market gap analysis as input to re-eval:** the gap signal is
  already implicit in the open price. Adding 5am pre-market levels doesn't
  cleanly improve the submit/skip decision.
- **Move execution to TWAP/VWAP for large orders:** at $25k positions on
  S&P 500 names, market impact is <1bp. Not worth the operational cost
  until Phase-3+ at much larger size.
- **Performance attribution by factor (sector/quality/setup-type/conviction):**
  needs N≥30 closed trades to be statistically meaningful. The proposal
  log (#19) is the data infrastructure that *enables* attribution later;
  building the attribution UI now is premature.
- **Beta-to-SPY budget (target portfolio beta 0.7-0.9):** quality stocks
  typically have beta 0.8-1.1; the natural portfolio beta is already in
  the target range. Active beta management adds knobs without obvious
  return improvement.
- **Real-time news ingestion** (Bloomberg / NewsAPI / Polygon news
  endpoint): high false-positive rate for actionable trade signals;
  earnings filter (#4) captures the highest-value news category cheaply.

---

## What I'd also flag for Klaas to think about

1. **#11 (shadow benchmark) and #9 (Phase-0 backtest) are both about the
   same question:** does this strategy actually beat passive? They attack
   it from different angles (forward-looking shadow vs historical
   walk-forward). Both should exist before any real-money deployment.

2. **#15 (estimate revisions) plus the existing technical filters would
   add a third leg** to the current value+trend stool: value (cheap on
   fundamentals) + trend (technical setup) + revisions (analyst sentiment).
   This three-factor stool is roughly the definition of "GARP" (Growth At
   a Reasonable Price) — a well-validated factor combination that has
   beaten SPY net-of-fees in most decades since the 1980s.

3. **#19 (proposal log) is the unsexy item that makes everything else
   measurable.** Without it, in 6 months we still won't be able to say
   "Claude's high-conviction picks beat its low-conviction picks by X%"
   — we'll just have aggregate P&L and a guess.
