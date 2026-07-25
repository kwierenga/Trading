# GO-LIVE READINESS — target ~2026-08-04

Tracks the two workstreams from the 2026-07-04 readiness deep dive: making the
TOOL live-grade (14-item checklist) and the ALGORITHM's standing against the
five locked $3k-ready gates. Update statuses here as items close; the week-4
go/no-go memo gets written from this file.

**Standing decision (locked):** on 08-04 real money starts as hand-bought SPY.
The algorithm trades real dollars only when the 5 gates pass — or Klaas writes
a conscious waiver in RULEBOOK.md. Gates do not move because the date arrived.

---

## Workstream 1 — infrastructure checklist (live-transition memory, 14 items)

| # | Item | Status (2026-07-05) |
|---|---|---|
| 1 | Separate `live-execute` env | ✅ Created 2026-07-05 via API |
| 2 | Required reviewers on it | ✅ kwierenga |
| 3 | Wait timer ≥ 1 min | ✅ 5 min |
| 4 | Deployments restricted to `main` | ✅ |
| 5 | Branch protection on `main` | 🟡 Design below; **enable week 3, not before a watched full-cycle day** |
| 6 | Live Alpaca keys (separate keypair) | ⬜ **KLAAS** — open live account first (lead time!), keys go ONLY in `live-execute` |
| 7 | Live `ALPACA_API_BASE_URL` | 🟡 Mechanism ready (per-env secret); set with #6 |
| 8 | Constants review for real dollars | ⬜ Week 3. On the table: 25%→15% concentration, 1.5%→1% risk/trade, pyramiding OFF, kill-switch 3% sign-off (RULEBOOK 1.13) |
| 9 | Daily dollar-loss kill switch | ✅ Built + tested 2026-07-04 (RULEBOOK 1.13); proving itself on paper now |
| 10 | 1-share live smoke test | ⬜ Week 4, after 6+7 |
| 11 | Alpaca account email notifications | ⬜ **KLAAS** — 5 min in Alpaca console |
| 12 | Tax-tracking dry-run | ✅ 2026-07-05: verified on live journal + fixed a real off-by-one (anniversary-day sale was labeled LTCG; IRS requires >1 year — `test_ltcg_boundary.py` pins it) |
| 13 | Rotate Anthropic key | 🟡 **KLAAS** — cron-job.org PAT ✅ rotated 2026-07-25 (→2027-07-25); Anthropic key still to rotate |
| 14 | Rollback playbook | ✅ [ROLLBACK.md](ROLLBACK.md), written 2026-07-05 |

Beyond the checklist: the cron-job.org PAT (was the single most time-critical
item — it would have died on go-live day) is ✅ **rotated 2026-07-25 → 2027-07-25**,
both jobs updated and TEST-RUN green. Remaining KLAAS creds: Anthropic key
rotation, Alpaca live account + keys + notifications.

### Item 5 design — branch protection without breaking the bots

Constraint: morning/execute/eod/weekly all `git push` to `main` with the
workflow `GITHUB_TOKEN` (github-actions[bot]). Classic "require PR" protection
would break the daily loop.

**Recommended: a repository RULESET on `main`** — require pull request before
merging, with a **bypass list containing only the GitHub Actions app**. Bot
commits keep flowing; human direct pushes (Klaas, Claude sessions) are forced
through a PR = one deliberate diff-read before anything reaches the branch
the cloud trades from. Do NOT put the admin role on the bypass list — that
would neuter the rule for exactly the pushes it exists to slow down.

Rollout: create ruleset in "evaluate" (log-only) mode on a Monday morning →
confirm that day's [AM]/[EXEC]/[EOD] bot commits all pass → flip to active on
Tuesday. If it misbehaves: rulesets toggle off instantly (no state lost).

Documented fallback (conscious skip, needs a written line in RULEBOOK if
chosen): keep `main` unprotected and lean on the `live-execute` env gate —
every live order run already requires manual approval + 5-min wait,
independent of what's on main. Weaker (doesn't protect the paper loop or
catch bad merges), but defensible for a solo repo.

## Workstream 2 — algorithm vs the five locked gates (scored 2026-07-04)

| Gate | Requirement | Standing |
|---|---|---|
| 1 | OOS LLM run: pre-tax CAGR ≥ SPY − 3pp | ❌ last run ~14pp short; nothing has changed it. **Binding.** |
| 2 | Post-tax within 4pp of pre-tax | ❌ live holds avg 8.4d, all STCG |
| 3 | MaxDD ≤ SPY | ✅ forward window (−2.6% vs −4.5%), small n |
| 4 | Win/loss ≥ 1.5 AND positive expectancy | 🟡 4.81 ✅ / **−1.61%/trade ❌** on n=9 |
| 5 | 6 clean forward weeks, zero rule violations | ⏳ clock restarted 2026-07-06 (new code) → earliest **~08-17** |

The only sanctioned path that could move gates 1/2/4 before scale-up: fill
post-mortems (`/post-mortem` skill) → 3+ "thesis intact, stop was noise"
conclusions unlock the RULEBOOK 3.4 pre-committed stop-width test → if that
passes ITS gate, one fresh OOS validation run. No other tuning. Weekly, the
[WEEK] review should update gate 4/5 numbers here.

## Who owns what (next 4 weeks)

- **Klaas only:** PAT + Anthropic key rotation; Alpaca live account + keys +
  notifications; post-mortem reflections (the actual bottleneck); RULEBOOK 2.1
  pyramid decision; constants sign-off (item 8); final go/no-go.
- **Claude (any session):** ruleset evaluate-mode rollout + verification;
  1-share smoke-test wiring when keys exist; weekly gate-scorecard updates
  here; Tier 3.4 test spec once 3+ post-mortems exist; week-4 go/no-go memo
  draft; Tier-2 promotion audit prep.
