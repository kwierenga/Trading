# Trading Journal

Daily log: 4 sections per entry, ~3 sentences each. Newest at top. Read this in the morning to catch up on what happened, what we learned, what we're testing, and what's next.

**Sections (in order):**
1. **What happened** — concrete events: trades, market moves, decisions, code changes.
2. **What we learned** — patterns observed or hypotheses confirmed/refuted.
3. **Open questions** — hypotheses still being tested; what we don't yet know.
4. **Tomorrow's plan** — the next concrete action.

Cap: 3 sentences per section. If you need more, it belongs in a memo, not the journal.

---

## [AM] 2026-05-19 Tuesday
**Open questions.** Will the 2 proposed entries (APP, V) fill at limit, or run away pre-market? What's the one thing that could derail the 55% confidence target? Any overnight news on these names worth checking before placing orders? 
**Today's plan.** execute.yml fires automatically at 09:35 ET — re-evaluates each setup against the actual open and submits the survivors. To skip today, push SKIP_TODAY.flag with today's UTC date before 09:35 ET. Monitor 5 open position(s) for thesis-break, stop hits, or LTCG-approaching flags. 

---

## [SESSION] 2026-05-18 Monday — reconciled 10-day cloud/local divergence
**What happened.** A "review today's activity" check uncovered that the live cloud had been running 2026-05-08 code for 10 trading days (Top-10/Phase-0 never pushed) and that the cloud `trade_journal.json` was corrupt (19 duplicate/phantom entries, ADSK wrongly open). Reconciled via a no-force merge: deployed all local code, rebuilt `trade_journal.json` from Alpaca (1 closed ADSK −3.69%, 6 open incl. previously-missing MCO 56@445.10), unioned `JOURNAL.md`, and added a deploy-discipline safeguard (SHA in AM email + workflow log + CLAUDE.md session-start check). Pushed as a clean fast-forward; a manual `morning.yml` run succeeded on the new code and printed `Deployed commit: 7a736b4`.
**What we learned.** Local commits never auto-deploy and nothing surfaced the drift — the live system silently lacked sector cap, conviction sizing, earnings filter, journal reconcile, and shadow benchmark for 10 days. The −$38,966 cash (1.38x gross; equity actually $102,698, +2.7%) traces to having no aggregate buying-power check: five names each sized to the ~25% per-name cap stacked past 100% of equity. The old cloud append logic also truncated `JOURNAL.md` to ~2 entries per run, so 05-09→05-17 operational dailies are unrecoverable (noted in-file).
**Open questions.** Does the now-deployed risk stack change behaviour on the next live execute, or were the gaps mostly latent? Should an aggregate buying-power / max-gross-deployment gate be added (the one hole present in BOTH old and new code)? Is the 1.38x accidental margin to be unwound deliberately or left to ride?
**Tomorrow's plan.** Watch Tue 05-19 06:00 ET morning + 09:35 ET execute on the new code — confirm the AM email shows `[deployed main@<sha>]`, the EOD email shows shadow + reconcile blocks, and re-eval applies sector/conviction logic. Klaas to set the aggregate-buying-power policy (max gross %, margin allowed y/n) before any code touches the trade path. No further code changes until the new stack has run clean in production for a few days (validate-before-extend).

