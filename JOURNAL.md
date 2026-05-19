# Trading Journal

Daily log: 4 sections per entry, ~3 sentences each. Newest at top. Read this in the morning to catch up on what happened, what we learned, what we're testing, and what's next.

**Sections (in order):**
1. **What happened** — concrete events: trades, market moves, decisions, code changes.
2. **What we learned** — patterns observed or hypotheses confirmed/refuted.
3. **Open questions** — hypotheses still being tested; what we don't yet know.
4. **Tomorrow's plan** — the next concrete action.

Cap: 3 sentences per section. If you need more, it belongs in a memo, not the journal.

---

## [EOD] 2026-05-19 Tuesday
**What happened.** No trades closed today. End equity $100,294, cash $-38,966 (-39% of equity), 5 open position(s). 
**What we learned.** [Add 1-2 sentences during your 15-min review: what surprised you today, what hypothesis got confirmed or refuted, or what you noticed about the market.] 

---

## [SESSION] 2026-05-19 Tuesday — execute.yml broken by yesterday's gross-cap wiring; fixed
**What happened.** execute.yml failed twice today (09:35 ET scheduled + 09:50 ET cron-job.org backup) with `ValueError: Missing required Alpaca API credentials` at re_evaluate.py module import — yesterday's [4f55823](https://github.com/kwierenga/Trading/commit/4f55823) added a runtime `AlpacaClient()` call to surface gross headroom, but the workflow step only exported `ANTHROPIC_API_KEY`. Morning routine fired clean at 06:36 ET; AM plan proposed 2 pyramid adds (APP +20 sh, V +30 sh) that would have been blocked by `validate_gross_deployment` anyway (book at -$38,966 cash, headroom = $0). Pushed [dd5fb6c](https://github.com/kwierenga/Trading/commit/dd5fb6c): mirror the Alpaca env block onto the re_evaluate step — tomorrow's 09:35 ET execute picks it up.
**What we learned.** The defensive `try/except` around `AlpacaClient()` in `re_evaluate.main()` cannot catch import-time failures — the import chain trips `ValueError` before `main()` runs, so "never breaks re-eval" was only ever a *runtime* guarantee. The bug and the gross-cap policy produced the same outcome today (0 new buys), which masked the cost; a day with real headroom would have skipped legitimate trades silently. The "previous re_evaluate.py imported `AlpacaClient` at module top and yet succeeded yesterday with no Alpaca creds in the step env" remains genuinely unexplained — local repro confirms the import does fail without creds, so something about yesterday's runner environment differed in a way the logs don't reveal.
**Open questions.** Why did yesterday's run succeed when local repro of the same import chain crashes? Should the pre-trade chain (re_evaluate, execute_strategy) get a single shared env-vars block at the workflow level rather than per-step duplication? Will tomorrow's MCO+UBER market-order fills clear cleanly, or will spread eat enough that the $910 projected loss balloons?
**Tomorrow's plan.** Run `python unwind_margin.py --submit` between 09:30 ET and 15:55 ET to close MCO+UBER at market (dry-run projects: free ~$48,237, gross 138.85% → 90.76%, cash -$38,966 → +$9,271). Wed 09:35 ET execute fires on a stale plan generated against tonight's leveraged book — expect a SUCCESS email even with zero new trades (gross-cap + stale-plan combination). Thursday's AM plan should be the first one to see the clean book and propose redeployment with real headroom.

