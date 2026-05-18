# Reconciliation & Safe-Deploy Plan — 2026-05-18

**Status:** DRAFT for review. Nothing here has been executed. `main`/`origin/main` untouched.

## Situation (one paragraph)

Local `main` and `origin/main` diverged at `10a80ac` (2026-05-08). The live cloud
(GitHub Actions runs `origin/main`) has been on ~May-8 code for 10 trading days:
no sector cap, no conviction sizing, no earnings filter, no journal reconcile, no
shadow benchmark. 10 days of real development (Top-10, Phase-0, optimization-closed)
sits only in local commits `4ed56c2`, `b2b9f64`, `bcaefb0` — now also safe on the
pushed branch `backup-local-main-20260518`.

## Conflict surface (verified)

- `.github/workflows/*` — **identical** both sides. Behavior change is purely the
  Python the cron workflows invoke. No workflow-definition risk.
- 24 net-new files (all code/docs/backtest artifacts) — added cleanly, zero conflict.
- All live-affecting code (`execute_strategy.py`, `position_sizer.py`,
  `market_data.py`, `eod_routine.py`, `re_evaluate.py`, …) — modified **only**
  on local since the merge-base, so they take the local version with no conflict.
- **Only 2 files truly conflict:** `JOURNAL.md` and `trade_journal.json`.

## End-state goal (per file)

| File class | Resolution | Why |
|---|---|---|
| All `.py`, `CLAUDE.md`, `RECOMMENDATIONS*.md`, `.gitignore`, `*_results.json` | Local | The real development; remote never touched them |
| 24 net-new files | Add as-is | Local-only, no conflict |
| `trade_journal.json` | **Rebuild from Alpaca** | Both git sides wrong: remote corrupt (19 dup/phantom entries), local stale (frozen 05-13, no MCO). Alpaca is ground truth |
| `JOURNAL.md` | **Union** | Remote has 10 days of real [AM]/[EOD]/[WEEK]; local has Phase-0/optimization entries. Both unique & real |
| `latest_strategy.json`, `sp500_constituents_cache.json` | Remote (untouched) | Live state; not in local commits, no conflict |
| `shadow_portfolio.json` | Local (net-new) | Remote never ran shadow; local's initialized file self-updates once deployed |

## Mechanism: merge, not rebase, no force-push

Merge `origin/main` into local `main` → one merge commit `M` with parents
`bcaefb0` (local code) + `1ef370c` (live history). `origin/main` is an ancestor
of `M`, so `git push origin main` **fast-forwards** — no history rewrite, no
force-push, `main` only moves forward. Git auto-resolves everything except the
2 conflict files.

## Pre-flight checklist (do before touching anything)

1. Confirm safety branch is on origin: `git ls-remote --heads origin backup-local-main-20260518` → non-empty.
2. Read `journal_reconcile.py` `main()` end-to-end. **Verify it only: reads Alpaca
   account/orders, writes `trade_journal.json` (+ `.bak`), and does NOT submit
   orders or send email.** Abort plan if it does anything else.
3. Confirm market state: deploy when the execute window is closed (after 16:00 ET
   or weekend) so the first new-code run is a low-stakes scheduled `morning.yml`,
   not a live `execute.yml`. (Today 05-18: EOD already ran `1ef370c`; window
   closed — deploying tonight means first new-code run is Tue 05-19 06:00 ET.)
4. `git stash list` / `git status` clean except known state.

## Execution steps

**Step 1 — integration branch (isolation)**
```
git checkout main
git checkout -b reconcile-20260518
git merge origin/main          # stops on JOURNAL.md + trade_journal.json conflicts
```

**Step 2 — resolve `JOURNAL.md` (union)**
Take `origin/main`'s JOURNAL.md as the base (most operational entries + it is the
live narrative), then splice in the 5 local-only entries — `[DECIDE] 2026-05-14`,
`[VERDICT] 2026-05-15`, `[RENEGOTIATE] 2026-05-15`, `[CORRECTION] 2026-05-15`,
`[CLOSE] 2026-05-16` — in correct chronological position (newest-first ordering,
so they sit above the 05-13 [EOD] and below/around the 05-15..05-18 daily
entries). Verify no daily entry from either side is dropped. `git add JOURNAL.md`.

**Step 3 — resolve `trade_journal.json` (rebuild from Alpaca)**
1. `git checkout --theirs trade_journal.json` (start from remote so a valid file exists), `git add` it — placeholder only.
2. Finish the merge: `git commit` (creates merge commit `M`).
3. Deploy code is now in the tree → run reconcile **locally** against live Alpaca:
   ```
   Copy-Item trade_journal.json trade_journal.json.prereconcile.bak
   python journal_reconcile.py        # read-only on Alpaca; rewrites the json
   ```
4. **Verify** the rebuilt journal: ADSK = closed (~-$895), open = APP, MSFT,
   V, MCO, UBER, no PYPL/FTNT phantoms, no 4× duplicates. Eyeball entry count
   (~6–7, matching Alpaca).
5. If correct: `git add trade_journal.json && git commit -m "Rebuild trade_journal.json from Alpaca (post-reconcile truth)"`.
   If wrong: stop, restore `.bak`, do not push — investigate reconcile.

**Step 4 — land on main (fast-forward, no force)**
```
git checkout main
git merge --ff-only reconcile-20260518
git push origin main
```
(`origin/main` is an ancestor → clean fast-forward. If push is rejected, a new
routine commit landed meanwhile: `git fetch`, `git merge origin/main` on the
reconcile branch resolving only any new JOURNAL/journal conflict, retry.)

## Post-deploy verification (closes the systemic gap)

1. **Code actually deployed:** `git show origin/main:journal_reconcile.py | head`
   resolves; `git show origin/main:execute_strategy.py | grep validate_sector`
   is non-empty.
2. **First run is healthy:** manually trigger to verify immediately rather than
   wait — `gh workflow run morning.yml --ref main` (safe: morning is
   informational, no orders). Then check the run log + AM email for new-code
   markers: a sector-check line, the shadow-benchmark block, a reconcile report.
3. **First real execute:** watch Tue 05-19 09:35 ET `execute.yml` — confirm the
   re-eval log shows sector/conviction logic and the EOD email shows the shadow
   benchmark + reconcile sections. Failure emails (`notify_execute.py`) surface
   any import error within seconds.
4. **Permanent safeguard (recommended, separate small commit):** add a step to
   `morning.yml` that prints `git rev-parse HEAD` to the log + the AM email
   footer, and add a session-start note to CLAUDE.md: "check `git status` vs
   `origin/main` — local commits do not auto-deploy." This is the missing
   "did the cloud get the code?" check that let this run silently for 10 days.

## Rollback

`main` only moved forward via merge `M`, so rollback is non-destructive:
```
git revert -m 1 <M-sha>      # reverts the merged code, keeps history
git push origin main
```
Workflows email on every failure regardless. The safety branch
`backup-local-main-20260518` and `trade_journal.json.prereconcile.bak` are
independent recovery points.

## Risk assessment

- Paper account; deploying *more* risk guards (sector cap, conviction skip,
  earnings filter, reconcile) is strictly safer than the status quo.
- Largest residual risk: a latent bug in 10-day-old code breaking a workflow →
  mitigated by failure emails + clean `git revert` rollback (no force-push).
- `trade_journal.json` rebuild is the delicate step → mitigated by `.bak`,
  explicit eyeball verification gate, and "stop if wrong, don't push."

## Decision points for Klaas

1. **Pre-rebuild journal locally (recommended)** vs let the first cloud EOD
   self-heal it. Recommended = correct journal from minute one; the alternative
   means one more day of corrupt-journal EOD/weekly numbers.
2. **Deploy tonight** (first new-code run Tue 06:00 ET, full day to verify) vs
   wait for the weekend (zero live runs during verification, lowest stakes).
3. Whether to fold the permanent SHA-in-email safeguard into this deploy or do
   it as a fast follow-up.
