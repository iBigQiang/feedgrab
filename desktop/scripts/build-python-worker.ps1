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
$workerOut = New-Item -ItemType Directory -Force -Path (Join-Path $runtimeRoot "feedgrab-worker")
$pyinstallerBuild = New-Item -ItemType Directory -Force -Path (Join-Path $desktopRoot "build\pyinstaller")

Push-Location $repoRoot
try {
  if (-not $SkipInstall) {
    & $Python -m pip install --upgrade pip wheel
    & $Python -m pip install -e ".[all]" pyinstaller
  }

  & $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --console `
    --name feedgrab-worker `
    --distpath $runtimeRoot `
    --workpath $pyinstallerBuild `
    --specpath $pyinstallerBuild `
    --copy-metadata feedgrab `
    --collect-submodules feedgrab.fetchers `
    --collect-submodules feedgrab.service `
    --collect-all playwright `
    --collect-all patchright `
    --collect-all browserforge `
    --collect-all apify_fingerprint_datapoints `
    --collect-all curl_cffi `
    --collect-all bs4 `
    --collect-all markdownify `
    --hidden-import playwright.async_api `
    --hidden-import patchright.async_api `
    "feedgrab\worker.py"

  $exePath = Join-Path $workerOut "feedgrab-worker.exe"
  if (-not (Test-Path $exePath)) {
    throw "PyInstaller did not create $exePath"
  }

  Write-Host "feedgrab worker built: $exePath"
}
finally {
  Pop-Location
}
