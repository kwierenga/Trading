# RULEBOOK — paper-validated rules on the path to real money

**Purpose.** The paper account is the training ground; this file is what the
training produces. Every rule that will govern real-money trading must pass
through here first, with provenance (which trade, incident, or backtest
established it) and a status. When the real-money wiring happens — **not
before ~2026-08-11** (Klaas, 2026-06-12: paper-first for ~2 months) — Tier 2
items must either be promoted to Tier 1 (enforced in code) or consciously
waived in writing. Silent skip is the failure mode.

**How rules graduate.**
`Tier 3 candidate (under observation)` → seen 3+ times in post-mortems /
observations → `Tier 2 validated (lesson confirmed, enforcement partial)` →
implemented as a hard code-level check → `Tier 1 enforced`. Prompt-level
guidance never counts as enforcement — the MSFT-to-49% episode proved that
a rule the LLM is "told about" is not a rule.

Related artifacts: [CLAUDE.md](CLAUDE.md) (operating rules), [JOURNAL.md](JOURNAL.md)
(daily log), [trade_post_mortems.md](trade_post_mortems.md) (per-trade reflection),
memory `trading_lessons.md` (distilled patterns), memory
`trading_live_transition_checklist.md` (the 14-item infrastructure list for the flip).

---

## Tier 1 — enforced in code today

| # | Rule | Enforced where | Provenance |
|---|------|----------------|------------|
| 1.1 | Max 25% of equity per name (pre-trade) | `position_sizer.validate_concentration`, hooked in `execute_strategy.py` + `claude_trader.py` | Design constraint; violation history → see 2.1 |
| 1.2 | Max 35% of equity per GICS sector | `position_sizer` sector check in `execute_strategy.py` | Design constraint |
| 1.3 | Max 95% gross deployment, zero margin on the automated path | `position_sizer.validate_gross_deployment` (commit `4f55823`) | 2026-05-18: five independently-sized positions stacked to 1.38x accidental margin (−$38,966 exposure) |
| 1.4 | ATR-based stops, hard bounds 4–15%; risk per trade ≈ 1.5% of equity | `position_sizer` | Klaas's 15% per-name drawdown tolerance |
| 1.5 | Conviction floor 60%; sizing multiplier tiers above it | `execute_strategy.py` | Design constraint |
| 1.6 | No entry if market already below the stop (stale signal) | `execute_strategy.py` gate | Design constraint |
| 1.7 | Plan freshness ≤ 6h; RTH window; NYSE holiday filter; SKIP_TODAY flag; idempotency vs backup trigger | `execute.yml` `shouldrun` + `re_evaluate.py` | Phase-1/2 trigger failures (2026-05-04..06) |
| 1.8 | Hard quality floor: mcap ≥ $2B, op CF > 0, D/E < 3.0, margin > −10% — exclusion, not down-weight | `market_data.passes_quality_filter` | Design constraint |
| 1.9 | No confirmed downtrends, no falling knives reach the prompt | screen filters in `market_data.py` | "Buy the basing knife, not the falling knife" |
| 1.10 | **Data failures fail closed**: NaN bars dropped; >50% of universe unpriceable aborts the plan with a FAILED email | `get_technicals` + `screen_universe` + `morning_routine` (2026-06-11) | 2026-06-10: yfinance outage passed every trend filter vacuously; "no setups" email masked a dead data pipe |
| 1.11 | Journal appends must preserve history (maxsplit=1 pattern) | `eod_routine` / `morning_routine` / `weekly_review` | Journal wiped twice (2026-06-06, 06-07) by three copies of the same bug |
| 1.12 | Liquidations cancel **held** OCO stop legs, not just open legs | `liquidate_all.py` | 2026-05-24: `status='open'` misses the held bracket sibling → orphaned sells |
| 1.13 | Daily dollar-loss kill switch: zero new entries when the account is down ≥3% vs prior close; fails closed on missing account data | `position_sizer.validate_daily_loss_halt`, book-level gate in `execute_strategy.py` before the trade loop (+ `test_kill_switch.py`) | Live-transition checklist #9, built 2026-07-04. **3% default pending Klaas's sign-off in the pre-live constants review** |

## Tier 2 — validated lessons, enforcement partial or pending

| # | Rule | Current state | What full enforcement looks like (pre-live requirement) |
|---|------|---------------|----------------------------------------------------------|
| 2.1 | Pyramiding must not breach the 25% cap | Pyramid path allows up to 30%; MSFT reached ~49% via adds (twice: pre-05-24 and pre-06-08 restarts) | Hard 25% would-be check inside `pyramid.py` itself, no pyramid exception |
| 2.2 | Scale-out/trail exits are tax-toxic — keep `position_manager` dry-run | `POSITION_MANAGER_LIVE` defaults off | Stays locked unless a pre-committed OOS gate is written FIRST and passes (see memory `scale-out-tax-drag`) |
| 2.3 | The system is not a returns engine — seven pre-committed backtests show no edge vs buy-hold SPY | Honest-objective prompt (2026-06-06); SPY+cash shadow scoreboard leads the EOD email | Real-money sizing decisions must cite the shadow spread, never raw P&L; no alpha-optimization reopening without a genuinely new mechanism + pre-committed gate |
| 2.4 | "Submitted" ≠ "executed" — every candidate's fate must be auditable | `execution_ledger.json` (2026-06-06), fill status reconciled at EOD | Several weeks of fill-rate data → explicit entry-mechanics decision (TTL/cancel-resubmit vs marketable limits) |
| 2.5 | The journal the AM stats are computed from must match Alpaca reality | Nightly reconcile now committed by `eod.yml` (2026-06-12); was silently discarded on the runner before | Pre-live: stats block in the prompt should be regenerated from the reconciled journal, and any orphan-sell in the reconcile report is a red flag |
| 2.6 | Every closed trade gets a post-mortem; unfilled reflections get nagged | `post_mortem.py` + EOD email section (2026-06-12) | 8+ weeks of filled reflections; override-vs-system P&L comparison per the learning-loop design |

## Tier 3 — candidates under observation

| # | Hypothesis | Evidence so far | Promotes when |
|---|-----------|-----------------|----------------|
| 3.1 | Below-market GTC limit entries systematically under-fill | Counter-evidence: 5/5 fills since the 06-08 restart | Ledger shows fill rate by entry-distance over ≥20 submissions |
| 3.2 | Regime gate (SPY 50-DMA) correctly shrinks exposure in downtrends | Wired 2026-06-06; no downtrend traversed yet | First sustained SPY downtrend handled with cash/smaller size and no falling-knife buys |
| 3.3 | Manage-only mode prevents frozen-book churn at the 95% cap | Untested since rebuild (book was 82% cash) | Next full-deployment stretch produces review-only plans, not blocked buys |
| 3.4 | Stops at 1.5–2× ATR(14) are too tight for this entry style (quick stop-outs: ZBRA −7.4% in 1.2d, MSFT −5.2% in 2d) | 4 of 5 signal exits were stops; APP target-hit is the lone counter | 3+ post-mortems concluding "thesis intact, stop was noise" → then a pre-committed gate test, NOT a quiet widening |
| 3.5 | Klaas's overrides add/destroy value | No overrides logged yet | ≥10 override events with logged reasons |

## Real-money wiring (deferred — target window opens ~2026-08-11)

1. **Funding gate first**: live algo deployment requires the account at ~$3k
   AND the five pre-committed `$3k-ready` conditions (memory
   `trading_optimization_project.md`) — a clean OOS run, tax-structure proof,
   drawdown ≤ SPY's, win/loss ≥ 1.5 with positive expectancy, 6 clean forward
   paper weeks. Until then real money stays in hand-bought SPY.
2. **Infrastructure**: walk the full 14-item live-transition checklist
   (separate `live-execute` env + reviewers + wait timer, fresh live keys,
   branch protection, 1-share smoke test, daily dollar-loss kill switch,
   rollback playbook…). Conscious skip with reason is allowed; silent skip is not.
3. **Credential collision**: ✅ the cron-job.org PAT was rotated 2026-07-25 →
   now expires **2027-07-25** (both backup jobs on it). No longer collides with
   the wiring window.
4. **Promotion audit**: every Tier 2 row above must be Tier 1 or consciously
   waived in writing, in this file, before the first live order.

*Maintained by the weekly review + monthly distill. Last updated 2026-06-12.*
