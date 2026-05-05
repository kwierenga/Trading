# Trading project — rules for Claude

This file is auto-loaded by Claude Code in this repo. It captures the durable rules
that govern the strategy, risk management, and code in this project. The same rules
will eventually govern real-money trading — don't relax them just because it's paper.

For learning that accumulates over time (observations, lessons, open hypotheses), see
the memory directory at `~/.claude/projects/c--Users-klaas-Trading/memory/`.

For the day-to-day log Klaas reads each morning, see [JOURNAL.md](JOURNAL.md) at repo
root. **Read it at the start of every session.** Append a new entry at the start of
each working session OR at end-of-day, summarizing notable events. Format: 4 sections
(*What happened / What we learned / Open questions / Tomorrow's plan*), 3 sentences
each, hard cap. Don't pad. If a section has nothing to say, write one short sentence
explaining why ("no trades placed; market closed early") rather than omitting it.

---

## User profile (brief)

Klaas is paper trading $100k via Alpaca with the explicit goal of building enough
comfort to eventually trade real money. The 6-week experiment targets +1% weekly with
70% confidence. He's a thoughtful, iterative investor — pushes back on suggestions and
refines constraints over multiple turns. Treat him as serious, not a beginner.

He explicitly accepts:
- Stocks-only as a comfort-building constraint (no options, futures, leverage > 2x)
- Capped upside in exchange for reduced complexity
- Up to 15% paper drawdown per single name before capitulating
- Lower turnover at the cost of some signal (US tax: short-term gains hurt)

---

## Asset universe

- **US-listed stocks only.** No options, futures, leverage beyond 2x reg-T margin, or
  shorting unless Klaas explicitly requests them.
- **Universe: S&P 500.** Candidates come from `sp500_universe.py`. No micro-caps, no
  OTC, no foreign listings.

---

## Quality filter — HARD, not soft

Only candidates that pass `market_data.passes_quality_filter()` may reach Claude.
Failures are excluded entirely, not down-weighted.

**Hard floor:**
- Market cap ≥ $2B
- Operating cash flow > 0
- Debt/equity < 300 (yfinance percent units, i.e. ratio < 3.0)
- Profit margin > -10%

Soft factors (Claude weighs in judgment): P/E vs own history, PEG, ROE/ROIC, margin
trend, revenue growth, free cash flow conversion.

---

## Per-position risk

| Rule | Value |
|---|---|
| Max single-position concentration | 25% (hard pre-trade check) |
| Worst-case stop below entry | ~15% (Klaas's drawdown tolerance per name) |
| Stop type | ATR-based (~1.5–2× ATR(14)), not fixed % |
| Sizing target | stop_distance × shares ≈ 1–2% of portfolio equity |

**Math check:** 25% position with 15% stop = ~3.75% portfolio loss when stopped — painful, not catastrophic.

---

## Entry timing

- **Never buy into a confirmed downtrend** (price below 50-day MA AND 50-day MA falling).
- **Prefer basing patterns:** stock has stopped making new lows for 2+ weeks AND is in
  the lower half of its 52-week range.
- **Mantra:** *"Buy the basing knife, not the falling knife."* Cheap + downtrend = value trap.

This is essentially Stan Weinstein Stage 1→Stage 2 transitions or William O'Neil base
breakouts. Don't propose entries that violate this without flagging the deviation.

---

## Tax / turnover (US)

- Short-term gains taxed as ordinary income (up to ~37% federal). Low turnover preferred.
- Flag positions approaching the 1-year LTCG line so we don't sell at day 364.
- Surface turnover metric and tax-drag estimate in performance reports.

---

## Code conventions

- New strategy/risk features go in `ai_strategy_enhanced.py` or `market_data.py`.
- `ai_strategy.py` was retired 2026-05-01 — do not recreate it.
- Don't introduce new bare `except:` blocks. Use specific exception types
  (`json.JSONDecodeError`, `OSError`, `requests.RequestException`, etc.).
- Data sources: yfinance for fundamentals AND price history. Alpaca only for account
  state and order execution.
- Pre-trade concentration check (`position_sizer.validate_concentration`) MUST run
  before any buy is submitted to Alpaca. The hook is in `claude_trader.execute_trade`
  and `execute_strategy.py`.

## Execution flow (current)

1. `python ai_strategy_enhanced.py` — runs S&P 500 screen, asks Claude for 0-3 trades,
   writes `latest_strategy.json`.
2. `python execute_strategy.py [--dry-run]` — reads the JSON, sizes each trade with
   `PositionSizer`, runs `validate_concentration`, asks for confirmation, then submits
   bracket orders (limit entry + take-profit + stop-loss, GTC).
3. Trades are logged to `trade_journal.json` automatically.

`claude_trader.py` is the older interactive flow (single-trade signal). It also runs
the concentration check now, but the screened-strategy flow above is preferred.

## Daily routines (cloud-side)

GitHub Actions workflows in `.github/workflows/` run the daily cycle without
the laptop being on. Repo is public, secrets live in GitHub Environments.

| Workflow | Cron (UTC) | Wall-clock | Trigger | What it does | Env |
|---|---|---|---|---|---|
| `morning.yml` | `0 10 * * 1-5` | 06:00 EDT / 05:00 EST | schedule | Fresh S&P 500 screen → Claude AM plan → commits `latest_strategy.json` + `[AM]` JOURNAL → dispatches `execute.yml` → emails Klaas the plan **with a tap-to-approve link** | `paper` |
| `execute.yml` | — (chained) | runs ~immediately, holds for approval, then waits until 09:35 ET | dispatched by `morning.yml` | Pauses on `paper-execute` Required Reviewers gate. After approval: waits for 09:35 ET, re-calls Claude with current prices for a per-ticker submit/adjust/skip verdict (`re_evaluate.py`), then submits surviving trades as bracket orders to Alpaca → commits `[EXEC]` to main | `paper-execute` (gated) |
| `cancel_stale.yml` | `30 13 * * 1-5` | 09:30 EDT / 08:30 EST | schedule | Cancels any `execute.yml` run still waiting on approval — closes the loop if Klaas didn't approve in time | — |
| `eod.yml` | `15 21 * * 1-5` | 17:15 EDT / 16:15 EST | schedule | Portfolio review email + `[EOD]` JOURNAL entry → commit to main | `paper` |

UTC cron does not shift with DST.

### The daily flow (one email, one tap)

```
06:00 ET  morning.yml fires (cron)
  ├── runs S&P 500 screen + Claude AM plan
  ├── commits latest_strategy.json + [AM] journal
  ├── dispatches execute.yml → captures the waiting run's URL
  └── sends ONE email: plan + APPROVE link

07:00 ET  Klaas reads email at breakfast, taps Approve link from phone

09:30 ET  cancel_stale.yml fires (cron) — auto-cancels if not approved by now

09:35 ET  execute.yml resumes (assuming approved earlier)
  ├── re_evaluate.py: re-calls Claude with current prices
  │   per-ticker verdict: submit / adjust / skip
  │   (mechanical fallback if LLM call fails)
  ├── execute_strategy.py --strategy latest_strategy_postopen.json --yes
  └── commits [EXEC] journal entry
```

**One tap per day = one trading day.** Skipping the tap = day skipped, no harm.

### The execute approval gate

`execute.yml` targets the `paper-execute` GitHub environment, which has
**Required reviewers** enabled (only `kwierenga` can approve). The gate is
opened by `morning.yml`'s dispatch step at ~06:05 ET, and the AM email contains
the direct URL to that waiting run. Tap → Approve → orders submit at 09:35 ET.

Self-approval is allowed (`prevent_self_review: false`) — required since the
trader is solo. If the team grows, flip this and require a different reviewer.

### Re-evaluation at market open

The AM plan is generated with prior-close prices. By 09:35 ET, prices have
moved. `re_evaluate.py` re-calls Claude (same model, same key) with the AM
plan + current prices and asks for `submit / adjust / skip` per ticker.
Adds ~$0.05/day in API cost on top of the morning call. If the LLM call fails,
falls back to a mechanical filter: skip if price > entry × 1.03 (gapped) or
price < stop (already broken); else submit at original limit. The filtered
result is written to `latest_strategy_postopen.json` and that's what
`execute_strategy.py` consumes.

### Secrets

Stored in GitHub Environments, not committed. `.env` stays gitignored for local use.

- **`paper` env** (used by morning + eod): `ANTHROPIC_API_KEY`, `ALPACA_API_KEY`,
  `ALPACA_API_SECRET`, `ALPACA_API_BASE_URL`, `EMAIL_USER`, `EMAIL_APP_PASSWORD`,
  `EMAIL_TO`.
- **`paper-execute` env** (used by execute): `ANTHROPIC_API_KEY` (for re-evaluation
  at open), `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `ALPACA_API_BASE_URL`.
  No email — execute doesn't currently send.

`ALPACA_ENVIRONMENT`, `CLAUDE_MODEL`, `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT` use
defaults from `config.py` and are not set as secrets unless overriding.

### Email

Email is sent via Gmail SMTP using the credentials above. Setup (one-time):
1. Enable 2-Step Verification on the sender Gmail account.
2. Generate an app password at https://myaccount.google.com/apppasswords.
3. Add `EMAIL_USER`, `EMAIL_APP_PASSWORD`, `EMAIL_TO` to GitHub secrets (and
   `.env` for local testing).

**Sender ≠ recipient gotcha**: Gmail silently drops same-account self-sends sent
via SMTP. `EMAIL_USER` and `EMAIL_TO` must be different addresses, or the daily
emails just disappear with no error. (Discovered 2026-05-01 — first AM email
went to a self-send and vanished.)

**`+tag` workaround for one-mailbox setups** (added 2026-05-02): If you want
all daily emails to land in the same Gmail inbox you send from, use a `+tag`
on the recipient. Current config: `EMAIL_USER=trading.klaaswierenga@gmail.com`,
`EMAIL_TO=trading.klaaswierenga+daily@gmail.com`. Gmail treats the `+tag`
address as distinct for delivery (so the self-send drop doesn't trigger) but
routes it back to the same mailbox, and `to:trading.klaaswierenga+daily@gmail.com`
is filterable. **Note:** Alpaca account stays on `klaaswierenga@gmail.com` (the
recovery/alert channel — separation of credential mailbox from digest mailbox is
deliberate).

Yahoo no longer reliably supports SMTP app passwords on consumer accounts —
use Gmail or Outlook.com.

### Local backup (currently disabled)

Windows Task Scheduler tasks `\Trading\MorningRoutine` and `\Trading\EODRoutine`
exist but are **disabled** since 2026-05-02 (when GitHub Actions took over).
They're left in place for re-activation if cloud-side ever fails. To re-enable:

```powershell
Enable-ScheduledTask -TaskPath "\Trading\" -TaskName "MorningRoutine"
Enable-ScheduledTask -TaskPath "\Trading\" -TaskName "EODRoutine"
```

**Don't enable both cloud and local at the same time** — they'll double-fire
the screen, double-email, and produce duplicate JOURNAL entries.

---

## When proposing changes

If a code change or AI prompt would violate any rule above, **flag it explicitly and
ask for confirmation** rather than silently relaxing the rule. The constraints are
deliberate; loosening them needs a reason.
