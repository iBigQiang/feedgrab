param(
  [string]$Python = "python",
  [string]$RuntimeDir = "",
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$desktopRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $RuntimeDir) {
  $RuntimeDir = Join-Path $desktopRoot "runtime\feedgrab-runtime"
}

& (Join-Path $PSScriptRoot "build-python-worker.ps1") -Python $Python -RuntimeDir $RuntimeDir -SkipInstall:$SkipInstall
& (Join-Path $PSScriptRoot "prepare-chromium-runtime.ps1") -Python $Python -RuntimeDir $RuntimeDir -SkipInstall:$SkipInstall

Write-Host "feedgrab desktop runtime ready: $RuntimeDir"
