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

## Daily routines

Two scheduled scripts deliver email summaries to klaaswierenga@gmail.com on weekdays:

- **`python morning_routine.py`** — runs at 6am EST. Forces a fresh S&P 500 screen,
  generates today's plan, appends an `[AM]` entry to JOURNAL.md (open questions +
  today's plan), saves `latest_strategy.json`, emails a brief summary.
- **`python eod_routine.py`** — runs at 4:15pm EST. Captures today's closed trades,
  open-position state, turnover, and tax drag. Appends an `[EOD]` entry to JOURNAL.md
  with a manual "what we learned" prompt. Emails a 15-min review.

Email is sent via SMTP (`email_notifier.py`). Defaults to Gmail SMTP; override
`EMAIL_SMTP_HOST` / `EMAIL_SMTP_PORT` for Outlook.com or others. Setup (Gmail):
1. Enable 2-Step Verification on the Gmail account.
2. Generate an app password at https://myaccount.google.com/apppasswords.
3. Add `EMAIL_USER`, `EMAIL_APP_PASSWORD`, `EMAIL_TO` to `.env` (see `.env.example`).
4. Test: `python email_notifier.py "test" "hello"`.

Note: Yahoo no longer reliably supports SMTP app passwords on consumer accounts —
the option has been removed from the security UI even with 2FA enabled.

To wire the schedules: Windows Task Scheduler → Create Basic Task →
- Trigger: Daily, Weekdays only. **Set the time in your machine's local timezone, not EST.**
  Task Scheduler triggers fire in the local timezone, so convert:
  - 6:00 AM EST → 12:00 PM CET (winter) / 12:00 PM CEST (summer, ie EDT)
  - 4:15 PM EST → 10:15 PM CET (winter) / 10:15 PM CEST (summer, ie EDT)
  After every DST transition (US and EU don't switch on the same day), re-check the trigger.
- Action: Start a program → use the `.venv` interpreter so `yfinance`/`anthropic`/`pytz` resolve:
  `C:\Users\klaas\Trading\.venv\Scripts\python.exe` with arguments
  `C:\Users\klaas\Trading\morning_routine.py` (set "Start in" to `C:\Users\klaas\Trading`).
- Conditions: only run if network is available

Sanity check: when the routine runs, the body of the email shows the dispatch timestamp
with offset (e.g. `2026-05-01T06:00:00-04:00`). If the offset isn't `-05:00` (winter)
or `-04:00` (summer), the trigger time is wrong.

---

## When proposing changes

If a code change or AI prompt would violate any rule above, **flag it explicitly and
ask for confirmation** rather than silently relaxing the rule. The constraints are
deliberate; loosening them needs a reason.
