#!/usr/bin/env pwsh
#Requires -Version 5.0

<#
.SYNOPSIS
  Sets up Git LFS for large Parquet file storage.

.DESCRIPTION
  GitHub has size limits:
    - Single file: 100 MB hard limit (warning at 50 MB)
    - Repository: 5 GB recommended, 100 GB hard limit
    
  Git LFS (Large File Storage) stores large files externally while keeping
  pointers in the repo. This script:
    1. Checks if Git LFS is installed
    2. Initializes Git LFS in the repo
    3. Tracks *.parquet files
    4. Verifies setup

.USAGE
  ./scripts/setup_git_lfs.ps1

.NOTES
  Run this ONCE before starting GitHub Actions collection.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "`n=== Git LFS Setup for LOB-X ===" -ForegroundColor Cyan

# ── Step 1: Check if Git LFS is installed ─────────────────────────────────────
Write-Host "`n[1/4] Checking Git LFS installation..." -ForegroundColor Yellow

try {
    $lfsVersion = git lfs version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Git LFS is installed: $lfsVersion" -ForegroundColor Green
    }
} catch {
    Write-Host "✗ Git LFS is NOT installed!" -ForegroundColor Red
    Write-Host "`nInstall Git LFS:" -ForegroundColor Yellow
    Write-Host "  Windows: winget install -e --id GitHub.GitLFS"
    Write-Host "  Or download from: https://git-lfs.github.com/`n"
    exit 1
}

# ── Step 2: Initialize Git LFS ────────────────────────────────────────────────
Write-Host "`n[2/4] Initializing Git LFS in repository..." -ForegroundColor Yellow

try {
    git lfs install 2>&1 | Out-Null
    Write-Host "✓ Git LFS initialized" -ForegroundColor Green
} catch {
    Write-Host "✗ Failed to initialize Git LFS: $_" -ForegroundColor Red
    exit 1
}

# ── Step 3: Verify .gitattributes ─────────────────────────────────────────────
Write-Host "`n[3/4] Verifying .gitattributes configuration..." -ForegroundColor Yellow

$gitattributes = ".gitattributes"
if (Test-Path $gitattributes) {
    $content = Get-Content $gitattributes -Raw
    if ($content -match "\*.parquet.*filter=lfs") {
        Write-Host "✓ .gitattributes already configured for Parquet files" -ForegroundColor Green
    } else {
        Write-Host "✗ .gitattributes exists but not configured correctly" -ForegroundColor Red
        Write-Host "  Expected: *.parquet filter=lfs diff=lfs merge=lfs -text" -ForegroundColor Yellow
        exit 1
    }
} else {
    Write-Host "✗ .gitattributes not found - run from repo root!" -ForegroundColor Red
    exit 1
}

# ── Step 4: Show tracked patterns ─────────────────────────────────────────────
Write-Host "`n[4/4] Checking tracked LFS patterns..." -ForegroundColor Yellow

try {
    $tracked = git lfs track
    Write-Host "✓ Currently tracked patterns:" -ForegroundColor Green
    Write-Host $tracked -ForegroundColor Gray
} catch {
    Write-Host "⚠ Could not list tracked patterns: $_" -ForegroundColor Yellow
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "`n=== Setup Complete ===" -ForegroundColor Cyan
Write-Host "
✓ Git LFS is ready for Parquet files
✓ GitHub Actions will now be able to commit large data files
✓ Files will be tracked with LFS (no size warnings)

Next steps:
  1. Commit .gitattributes: git add .gitattributes && git commit -m 'chore: add Git LFS config'
  2. Push changes: git push
  3. Enable GitHub Actions in your repo settings
  4. Manual trigger test: GitHub repo → Actions → Collect Market Data → Run workflow

LFS Storage Limits (GitHub Free):
  - Storage: 1 GB free
  - Bandwidth: 1 GB/month free
  - Upgrade: `$5/month for 50 GB storage + 50 GB bandwidth

For 3 weeks of collection (~19 GB), you'll need LFS bandwidth pack.
Alternative: Keep only last 7 days in repo, rest in Actions artifacts.
" -ForegroundColor Gray

Write-Host "Done!`n" -ForegroundColor Green
