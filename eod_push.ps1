#!/usr/bin/env pwsh
# End-of-day routine: save snapshot, commit, push to GitHub.
# Run this after the market closes each day.

Set-Location $PSScriptRoot

Write-Host "=== EOD: Saving portfolio snapshot ===" -ForegroundColor Cyan
python performance_tracker.py track
if ($LASTEXITCODE -ne 0) {
    Write-Host "Snapshot failed — aborting before commit." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== EOD: Committing and pushing to GitHub ===" -ForegroundColor Cyan
git add .

$changes = git status --short
if (-not $changes) {
    Write-Host "Nothing changed — skipping commit/push." -ForegroundColor Yellow
    exit 0
}

$date = Get-Date -Format 'yyyy-MM-dd'
git commit -m "EOD snapshot $date"
git push

Write-Host ""
Write-Host "Done. Repo synced: https://github.com/kwierenga/Trading" -ForegroundColor Green
