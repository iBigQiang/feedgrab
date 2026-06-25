param(
  [string]$Python = "python",
  [string]$RuntimeDir = "",
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$desktopRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$repoRoot = Resolve-Path (Join-Path $desktopRoot "..")
if (-not $RuntimeDir) {
  $RuntimeDir = Join-Path $desktopRoot "runtime\feedgrab-runtime"
}
$runtimeRoot = New-Item -ItemType Directory -Force -Path $RuntimeDir
$browserRoot = New-Item -ItemType Directory -Force -Path (Join-Path $runtimeRoot "ms-playwright")

$env:PLAYWRIGHT_BROWSERS_PATH = $browserRoot.FullName
$env:PLAYWRIGHT_SKIP_BROWSER_GC = "1"

Push-Location $repoRoot
try {
  if (-not $SkipInstall) {
    & $Python -m pip install -e ".[browser]" patchright playwright
  }

  & $Python -m playwright install chromium

  $pythonBin = & $Python -c "import pathlib, sys; print(pathlib.Path(sys.executable).parent)"
  $patchrightExe = Join-Path $pythonBin "patchright.exe"
  if (Test-Path $patchrightExe) {
    & $patchrightExe install chromium
  } else {
    Write-Warning "patchright.exe was not found next to $Python; Playwright Chromium is installed, patchright install skipped."
  }

  $chromiumDirs = Get-ChildItem -LiteralPath $browserRoot.FullName -Directory -Filter "chromium*" -ErrorAction SilentlyContinue
  if (-not $chromiumDirs) {
    throw "Chromium was not installed under $($browserRoot.FullName)"
  }

  Write-Host "Chromium runtime ready: $($browserRoot.FullName)"
}
finally {
  Pop-Location
}
