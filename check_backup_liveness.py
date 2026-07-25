"""
Backup-trigger liveness check.

The two cron-job.org repository_dispatch backups —
  * execute.yml  (event type "execute-strategy", fires ~14:33 UTC)
  * morning.yml  (event type "morning-routine",  fires ~10:20 UTC)
depend on a fine-grained GitHub PAT stored in cron-job.org (currently expires
2027-07-25). When that PAT lapses, GitHub 401s the dispatch and NO workflow run
is created — a SILENT failure with no alert of its own. That is exactly the gap
behind the 2026-07-25 near-miss: only GitHub's courtesy expiry email surfaced it.

This closes the gap. It asks the GitHub Actions API for the most recent
repository_dispatch run of each workflow and, if either is older than
STALE_TRADING_DAYS NYSE sessions, emails an alert so a dead backup becomes loud.

Invoked daily from eod.yml. Requires GITHUB_TOKEN with actions:read.
Best-effort by design: it never raises into the workflow — a monitoring check
must not be able to fail the EOD job.

Run locally:
    GITHUB_TOKEN=$(gh auth token) python check_backup_liveness.py
"""

import os
import sys
from datetime import datetime

import pytz
import requests

from email_notifier import send_email
from market_calendar import trading_days_between

REPO = os.environ.get("GITHUB_REPOSITORY", "kwierenga/Trading")
GITHUB_API = "https://api.github.com"

# Alert when a backup has produced no repository_dispatch run in this many NYSE
# trading sessions. 2 tolerates a single-day cron-job.org hiccup but catches a
# real PAT expiry within two trading days — well before it can matter.
STALE_TRADING_DAYS = 2

# (workflow file, human label, cron-job.org event type)
BACKUPS = [
    ("execute.yml", "execute.yml backup trigger", "execute-strategy"),
    ("morning.yml", "morning-routine backup", "morning-routine"),
]


def _latest_dispatch_date(session, wf_file):
    """
    Date (UTC) of the most recent repository_dispatch run for wf_file, or None if
    there are none. Raises requests.RequestException on API failure.
    """
    url = f"{GITHUB_API}/repos/{REPO}/actions/workflows/{wf_file}/runs"
    resp = session.get(
        url, params={"event": "repository_dispatch", "per_page": 1}, timeout=20
    )
    resp.raise_for_status()
    runs = resp.json().get("workflow_runs", [])
    if not runs:
        return None
    created = runs[0]["created_at"]  # e.g. "2026-07-24T14:33:11Z"
    return (
        datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=pytz.UTC)
        .date()
    )


def check():
    """Return a list of (label, event_type, age_str) for stale backups."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("check_backup_liveness: no GITHUB_TOKEN in env — skipping.")
        return []

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    today = datetime.now(pytz.UTC).date()
    stale = []
    for wf_file, label, event_type in BACKUPS:
        try:
            last = _latest_dispatch_date(session, wf_file)
        except requests.RequestException as e:
            # Transient API problem — log and skip; don't false-alarm.
            print(f"  {label}: API error ({type(e).__name__}) — skipping this check")
            continue
        if last is None:
            print(f"  {label}: NO repository_dispatch run found")
            stale.append((label, event_type, "never (no repository_dispatch run found)"))
            continue
        age = trading_days_between(last, today)
        if age >= STALE_TRADING_DAYS:
            print(f"  {label}: STALE — last dispatch {last} ({age} trading days ago)")
            stale.append((label, event_type, f"{last} ({age} trading days ago)"))
        else:
            print(f"  {label}: ok — last dispatch {last} ({age} trading days ago)")
    return stale


def _send_alert(stale):
    lines = [
        "One or more cron-job.org backup triggers have gone SILENT — no "
        f"repository_dispatch run in the last {STALE_TRADING_DAYS} trading days.",
        "",
        "Most likely cause: the fine-grained GitHub PAT stored in cron-job.org "
        "expired (or the cron-job.org job was disabled). GitHub 401s the dispatch "
        "and creates no workflow run, so nothing else would surface it.",
        "",
        "Stale backup(s):",
    ]
    for label, event_type, age in stale:
        lines.append(f"  - {label} (event: {event_type}) — last fired {age}")
    lines += [
        "",
        "FIX:",
        "  1. Generate a fine-grained PAT (Contents: read+write on "
        "kwierenga/Trading) at https://github.com/settings/tokens?type=beta",
        "  2. Paste 'Bearer <token>' into the Authorization header of the "
        "affected cron-job.org job(s).",
        "  3. TEST RUN each — expect HTTP 204.",
        "",
        "Impact: the primary GitHub-scheduled crons are unaffected, so trades "
        "still run — but the punctual backup redundancy is gone until fixed.",
    ]
    body = "\n".join(lines)
    subject = "ALERT: Trading cron-job.org backup SILENT (PAT expired?)"
    if send_email(subject, body):
        print("check_backup_liveness: alert email sent.")
    else:
        # send_email never raises; returns False on missing config / SMTP error.
        print("check_backup_liveness: alert email NOT sent (see email log above).")
        print(body)


def main():
    print(
        f"check_backup_liveness: checking {REPO} backups "
        f"(stale threshold = {STALE_TRADING_DAYS} trading days)"
    )
    stale = check()
    if stale:
        _send_alert(stale)
    else:
        print("check_backup_liveness: all backups healthy.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        # Deliberate catch-all: a monitoring check must never fail the EOD
        # workflow. Log the error type + message and exit 0.
        print(f"check_backup_liveness: unexpected error ({type(e).__name__}): {e}")
    sys.exit(0)
