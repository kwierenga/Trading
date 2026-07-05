# ROLLBACK — what to do when something goes wrong (live-transition item #14)

Written 2026-07-05, before go-live, so nobody designs a fire escape during the
fire. Applies to paper today and live later; the live-only steps are marked.
Commands are PowerShell (Windows laptop). Escalate down the list only as far
as the situation requires — each step is more drastic than the last.

**Key fact that shapes everything here: stopping the workflows does NOT
unprotect open positions.** Every position is held inside a GTC bracket at
Alpaca — the stop-loss and take-profit legs live server-side at the broker and
keep working with GitHub Actions completely off. Turning things off stops NEW
risk; it never removes existing protection.

---

## Step 1 — Stand down today's trading (reversible, zero side effects)

Push a dated skip flag before 14:35 UTC. Works from any device, including
phone via Working Copy. `eod.yml` auto-clears it in the evening.

```powershell
Set-Content SKIP_TODAY.flag (Get-Date -AsUTC -Format yyyy-MM-dd)
git add SKIP_TODAY.flag; git commit -m "skip today"; git push
```

Use when: something looks off (weird AM plan, suspect data, unexplained
email) but nothing has actually broken. Buys 24h to investigate.

## Step 2 — Halt the pipeline for multiple days

Disable the scheduled workflows (state survives until re-enabled):

```powershell
gh workflow disable execute.yml
gh workflow disable morning.yml   # optional: stops plan generation too
```

Remember BOTH backup triggers: cron-job.org keeps POSTing
`repository_dispatch` daily — a disabled workflow ignores dispatches, so this
is sufficient. Re-enable later with `gh workflow enable <name>`.

Use when: a code/infra defect needs more than a day to fix, or any
unexplained order appears. Leave eod.yml running — its reconcile + email is
how you keep watching the book while entries are halted.

## Step 3 — Flatten the book (destroys positions, keeps account)

Preferred: the guarded workflow (dry-runs unless the confirm phrase matches,
NYSE-open + RTH gated, cancels **held** OCO legs so no orphaned sells):

```powershell
gh workflow run liquidate.yml --ref main -f confirm="LIQUIDATE ALL"
```

Manual fallback: Alpaca dashboard → cancel ALL open orders for a symbol
(including the held stop leg) → then close the position. Order matters:
cancel legs first, close second, per the 2026-05-24 lesson.

Use when: you no longer trust what the system might do with the positions,
or a drawdown breach says get out.

## Step 4 — Cut credentials (LIVE: the definitive kill)

Regenerating Alpaca keys instantly invalidates the old pair everywhere —
GitHub secrets, laptop `.env`, anything leaked. This is the hard stop.

1. Alpaca console → API keys → **Regenerate** (live keys live ONLY in the
   `live-execute` env; paper keys in `paper`).
2. If compromise is suspected, also rotate: Anthropic key
   (console.anthropic.com), the cron-job.org PAT (GitHub → Settings →
   Developer settings), Gmail app password (myaccount.google.com/apppasswords).
3. Do NOT delete the GitHub environment — losing its config/history helps
   nobody; stale secrets in it are already dead after rotation.

## Step 5 — Post-incident, before any re-enable

1. Write the incident into JOURNAL.md ([NOTE] entry) + memory observations
   while it's fresh: what fired, what you did, what you saw.
2. Root-cause in code BEFORE re-enabling — "it looks fine now" is not a cause
   (see 2026-05-19: bugs that align with policy hide for days).
3. If code was at fault on LIVE: fix → deploy → run ≥2 clean days on PAPER →
   only then re-enable live. Paper is the staging environment forever.
4. Re-enable: `gh workflow enable execute.yml` (+ morning), delete any stale
   SKIP_TODAY.flag, verify next scheduled run end-to-end.

---

## Decision aid: which step?

| Symptom | Step |
|---|---|
| AM plan looks wrong / data smells stale | 1 |
| Workflow bug, bad deploy, duplicate orders attempted | 2 (+1 for today) |
| Unexplained POSITION or fill you didn't expect | 2, then investigate; 3 if unexplained after an hour |
| Daily-loss kill switch fired + you agree with it | Nothing — that's the system working; review at EOD |
| Key/secret possibly exposed | 4 immediately, then 2 |
| "I don't trust it anymore" (the honest gut call) | 3 + 2, reflect at leisure — flat and stopped is a fine place to think |
