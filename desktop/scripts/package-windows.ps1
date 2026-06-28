param(
    [ValidateSet("portable", "nsis", "all")]
    [string]$Target = "all",
    [string]$Python = "python",
    [switch]$SkipRuntimeBuild,
    [switch]$SkipRuntimeInstall
)

$ErrorActionPreference = "Stop"

$DesktopRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Builder = Join-Path $DesktopRoot "node_modules\.bin\electron-builder.cmd"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$OutputDir = Join-Path $DesktopRoot "release-packages\$Stamp"

if (-not (Test-Path -LiteralPath $Builder)) {
    throw "electron-builder was not found. Run npm install in $DesktopRoot first."
}

if (-not $SkipRuntimeBuild) {
    Write-Host "Building feedgrab desktop runtime..."
    & (Join-Path $PSScriptRoot "build-runtime.ps1") -Python $Python -SkipInstall:$SkipRuntimeInstall
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$Targets = switch ($Target) {
    "portable" { @("portable") }
    "nsis" { @("nsis") }
    default { @("nsis", "portable") }
}

$BuilderArgs = @("--win") + $Targets + @("--x64", "--config.directories.output=$OutputDir")

Write-Host "Packaging feedgrab Desktop for Windows..."
Write-Host "Target: $Target"
Write-Host "Output: $OutputDir"

& $Builder @BuilderArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Windows packages ready: $OutputDir"
