# build-bridge-windows.ps1
#
# Phase 49 Sec.8/Sec.35 -- native Windows x64 release build for the pd-ollama-bridge
# standalone client. Fail-fast: any error exits non-zero.
#
# What it does:
#   1. verifies Windows x64 (AMD64);
#   2. resolves the bridge version from the SINGLE canonical source
#      (bridge/pd_ollama_bridge/__init__.py::__version__);
#   3. refuses a dirty working tree unless -AllowDirty (Phase 49 Sec.37);
#   4. installs PyInstaller (idempotent, pinned range) + PINNED runtime deps
#      (ADV-49-004);
#   5. builds the one-file executable via bridge/pd-ollama-bridge.spec;
#   6. smoke-tests the packaged EXE (--help, --version);
#   7. assembles the release ZIP staging (exe + QUICKSTART.md + LICENSE +
#      THIRD_PARTY_NOTICES.txt);
#   8. writes dist/releases/<VERSION>/pd-ollama-bridge-v<VERSION>-windows-x64.zip;
#   9. runs the MANDATORY shared secret/integrity gate (tools/build_release.py
#      --scan-only) on the produced ZIP -- a bare Windows build cannot emit an
#      ungated artifact (ADV-49-011);
#  10. prints the SHA-256 and byte size.
#
# It does NOT push, publish or upload anything (Phase 49 Sec.53).
#
# Usage:
#   .\build-bridge-windows.ps1              # refuse dirty tree
#   .\build-bridge-windows.ps1 -AllowDirty  # development override (records dirty)

[CmdletBinding()]
param(
    [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "BUILD FAILED: $Message" -ForegroundColor Red
    exit 1
}

$RepoRoot   = $PSScriptRoot
$BridgeDir  = Join-Path $RepoRoot 'bridge'
$PackageDir = Join-Path $BridgeDir 'pd_ollama_bridge'
$SpecPath   = Join-Path $BridgeDir 'pd-ollama-bridge.spec'

Write-Host "Procedural Detective -- Phase 49 Windows x64 bridge release build"
Write-Host "Repo root: $RepoRoot"

# --------------------------------------------------------------------------- #
# 1. Platform / architecture check
# --------------------------------------------------------------------------- #
Write-Step "Verifying platform (Windows x64)"
if (-not [Environment]::Is64BitOperatingSystem) {
    Fail "a 64-bit Windows OS is required."
}
$procArch = $env:PROCESSOR_ARCHITECTURE
if ($procArch -and $procArch -ne 'AMD64') {
    Fail "Windows x64 (AMD64) is required; this host reports PROCESSOR_ARCHITECTURE='$procArch'."
}

# --------------------------------------------------------------------------- #
# 2. Canonical version (single source of truth)
# --------------------------------------------------------------------------- #
Write-Step "Resolving canonical bridge version"
if (-not (Test-Path (Join-Path $PackageDir '__init__.py'))) {
    Fail "canonical version source not found at bridge\pd_ollama_bridge\__init__.py."
}
$version = (& python -c "import sys; sys.path.insert(0, sys.argv[1]); from pd_ollama_bridge import __version__; print(__version__)" $BridgeDir).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($version)) {
    Fail "could not resolve pd_ollama_bridge.__version__ via Python."
}
Write-Host "Canonical version: $version"

# --------------------------------------------------------------------------- #
# 3. Dirty-tree guard (Phase 49 Sec.37)
# --------------------------------------------------------------------------- #
Write-Step "Checking working tree"
$commit = (& git -C $RepoRoot rev-parse HEAD 2>$null).Trim()
$dirtyOutput = (& git -C $RepoRoot status --porcelain 2>$null)
$isDirty = [bool]($dirtyOutput -and ($dirtyOutput -join '').Trim().Length -gt 0)

if ($isDirty) {
    if (-not $AllowDirty) {
        Fail "working tree is dirty. Commit your changes or re-run with -AllowDirty for a NON-publishable development build (Phase 49 Sec.37)."
    }
    Write-Host "WARNING: working tree is DIRTY -- this is a development build and must NOT be published as an official release." -ForegroundColor Yellow
} else {
    Write-Host "Working tree is clean."
}
Write-Host "git commit: $commit"
Write-Host "dirty: $([int]$isDirty)"

# --------------------------------------------------------------------------- #
# 4. Staging / output layout
# --------------------------------------------------------------------------- #
$DistDir  = Join-Path $RepoRoot "dist\releases\$version"
$Staging  = Join-Path $DistDir 'staging-windows'
$BuildRoot = Join-Path $RepoRoot "build\pyinstaller\$version"
$WorkDir  = Join-Path $BuildRoot 'work'
$PkgDist  = Join-Path $BuildRoot 'dist'
$ZipName  = "pd-ollama-bridge-v$version-windows-x64.zip"
$ZipPath  = Join-Path $DistDir $ZipName

Write-Step "Preparing clean staging directories"
Remove-Item -Recurse -Force $Staging, $BuildRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Staging | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

# --------------------------------------------------------------------------- #
# 5. PyInstaller + PINNED runtime deps
#
# ADV-49-004: the runtime deps are pinned to EXACT versions that match
# bridge/Dockerfile.release so the Windows and Linux artifacts bundle the same
# dependency set. Residual reproducibility limitation: versions are pinned but
# NOT hash-pinned (no --require-hashes), and PyInstaller is a pinned RANGE.
# --------------------------------------------------------------------------- #
Write-Step "Ensuring PyInstaller + pinned runtime deps are installed"
& python -m pip install --disable-pip-version-check --quiet `
    "pyinstaller>=6.10,<7" `
    "websockets==17.1" `
    "httpx==0.28.1" `
    "httpcore==1.0.9" `
    "certifi==2026.07.22"
if ($LASTEXITCODE -ne 0) { Fail "pip install pyinstaller/runtime deps failed (exit $LASTEXITCODE)." }
$pyiVersion = (& python -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
Write-Host "PyInstaller: $pyiVersion"

# --------------------------------------------------------------------------- #
# 6. Build the one-file executable
# --------------------------------------------------------------------------- #
Write-Step "Building pd-ollama-bridge.exe (one-file)"
& python -m PyInstaller --clean --noconfirm --distpath $PkgDist --workpath $WorkDir $SpecPath
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed (exit $LASTEXITCODE)." }

$ExePath = Join-Path $PkgDist 'pd-ollama-bridge.exe'
if (-not (Test-Path $ExePath)) { Fail "packaged EXE not found at $ExePath." }
$exeBytes = (Get-Item $ExePath).Length
Write-Host "Built EXE: $ExePath ($exeBytes bytes)"

# --------------------------------------------------------------------------- #
# 7. Packaged-EXE smoke test
# --------------------------------------------------------------------------- #
Write-Step "Smoke-testing the packaged EXE"

$verRaw  = & $ExePath --version
$verExit = $LASTEXITCODE
$verOut  = ($verRaw | Out-String).Trim()
if ($verExit -ne 0) { Fail "'pd-ollama-bridge.exe --version' exited $verExit." }
if ($verOut -ne "pd-ollama-bridge $version") {
    Fail "'--version' printed '$verOut'; expected 'pd-ollama-bridge $version'."
}
Write-Host "--version -> $verOut (exit $verExit)"

$helpRaw  = & $ExePath --help
$helpExit = $LASTEXITCODE
$helpOut  = ($helpRaw | Out-String)
if ($helpExit -ne 0) { Fail "'pd-ollama-bridge.exe --help' exited $helpExit." }
if ($helpOut -notmatch 'usage: pd-ollama-bridge') {
    Fail "'--help' did not print the expected usage line."
}
Write-Host "--help -> exit $helpExit (usage line present)"

# --------------------------------------------------------------------------- #
# 8. Assemble ZIP staging
# --------------------------------------------------------------------------- #
Write-Step "Assembling release ZIP staging"
$docs = @(
    @{ Src = (Join-Path $BridgeDir 'QUICKSTART.md');            Dst = 'QUICKSTART.md' },
    @{ Src = (Join-Path $RepoRoot  'LICENSE');                  Dst = 'LICENSE' },
    @{ Src = (Join-Path $BridgeDir 'THIRD_PARTY_NOTICES.txt');  Dst = 'THIRD_PARTY_NOTICES.txt' }
)
Copy-Item $ExePath (Join-Path $Staging 'pd-ollama-bridge.exe')
foreach ($doc in $docs) {
    if (-not (Test-Path $doc.Src)) { Fail "required archive document missing: $($doc.Src)." }
    Copy-Item $doc.Src (Join-Path $Staging $doc.Dst)
}

# --------------------------------------------------------------------------- #
# 9. Create the ZIP + checksum
# --------------------------------------------------------------------------- #
Write-Step "Creating $ZipName"
Remove-Item -Force $ZipPath -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $Staging '*') -DestinationPath $ZipPath -Force
if (-not (Test-Path $ZipPath)) { Fail "ZIP was not created at $ZipPath." }

$hash = (Get-FileHash -Algorithm SHA256 -Path $ZipPath).Hash.ToLower()
$zipBytes = (Get-Item $ZipPath).Length

# Leave only the release artifacts in the output directory.
Remove-Item -Recurse -Force $Staging -ErrorAction SilentlyContinue

# --------------------------------------------------------------------------- #
# 9b. MANDATORY secret/integrity gate (ADV-49-011; Phase 49 Sec.31/44)
#
# The same shared entry point the unified assembly uses scans the RAW archive
# bytes, the ZIP comment, any post-EOCD bytes and every member's bytes, and
# enforces the exact member set. A bare Windows build can therefore NEVER emit a
# publishable-looking ZIP without passing the gate. Values are never printed.
# --------------------------------------------------------------------------- #
Write-Step "Running mandatory release secret/integrity gate on $ZipName"
$ScanScript = Join-Path $RepoRoot 'tools\build_release.py'
if (-not (Test-Path $ScanScript)) { Fail "release gate script not found: $ScanScript." }
& python $ScanScript --scan-only --windows-zip $ZipPath
if ($LASTEXITCODE -ne 0) {
    Fail "release secret/integrity gate FAILED (exit $LASTEXITCODE) -- artifact is NOT publishable."
}

Write-Host ""
Write-Host "=============================================================="
Write-Host "Windows release artifact built" -ForegroundColor Green
Write-Host "  version    : $version"
Write-Host "  git commit : $commit"
Write-Host "  dirty      : $([int]$isDirty)"
Write-Host "  artifact   : $ZipPath"
Write-Host "  size       : $zipBytes bytes"
Write-Host "  sha256     : $hash"
Write-Host "=============================================================="

exit 0
