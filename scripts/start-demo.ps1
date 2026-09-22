# start-demo.ps1 - one-shot Procedural Detective demo launcher (Windows).
#
# ONE command: installs backend/frontend dependencies and builds the SPA only
# when needed, migrates the database, starts ONE uvicorn that serves the built
# SPA AND the API on a single port (single origin, no CORS needed), opens the
# browser, and stops cleanly when you press Enter (or Ctrl+C).
#
# Switches:
#   -NoBrowser   do not open the browser automatically
#   -Port <int>  TCP port to serve on (default 8000)
#   -Rebuild     force a fresh `npm run build` of the frontend
#   -Stop        stop the demo recorded in <repo>\.pd-demo-meta, then exit
#   -RepoRoot <path>  override the detected repository root
#
# The child server is launched through the repository lifecycle tooling
# (python -m tools.process_guard` launch`/`stop`) so every launch is
# identity-verified and every stop is descendant-verified: no stray listeners
# are left behind.

[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [int]$Port = 8000,
    [switch]$Rebuild,
    [switch]$Stop,
    [string]$RepoRoot = ""
)

$ErrorActionPreference = "Stop"

# ------------------------------------------------------------------------
# repo root detection + basic validation
# ------------------------------------------------------------------------
$scriptDir = Split-Path -Parent (Resolve-Path -LiteralPath $MyInvocation.MyCommand.Path)
if ($RepoRoot) {
    $repoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
} else {
    $repoRoot = Split-Path -Parent $scriptDir   # scripts/ -> repository root
}
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "backend\app\main.py"))) {
    Write-Error "repository root not found; pass -RepoRoot <path>"
    exit 2
}
if ($Port -lt 1 -or $Port -gt 65535) {
    Write-Error "invalid port $Port (must be 1-65535)"
    exit 2
}

$metaPath = Join-Path $env:TEMP ("pd-demo-$Port.json")
$markerPath = Join-Path $repoRoot ".pd-demo-meta"

# ------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------
function Test-PortInUse {
    param([int]$Port)
    try {
        $conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
        return ($conns.Count -gt 0)
    } catch {
        # No matching listener, or the cmdlet is unavailable -> bind probe.
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        try {
            $listener.Start()
            return $false
        } catch {
            return $true    # cannot determine -> fail closed
        } finally {
            try { $listener.Stop() } catch { }
        }
    }
}

function Invoke-DemoStop {
    param([string]$MarkerPath, [string]$RepoRoot)
    if (-not (Test-Path -LiteralPath $MarkerPath)) {
        Write-Host "[stop] no demo marker found at $MarkerPath - nothing to stop (no-op)."
        exit 0
    }
    $meta = (Get-Content -LiteralPath $MarkerPath -Raw).Trim()
    if (-not (Test-Path -LiteralPath $meta)) {
        Write-Host "[stop] stale marker: process metadata '$meta' is missing. Removing marker."
        Remove-Item -LiteralPath $MarkerPath -Force -ErrorAction SilentlyContinue
        exit 0
    }
    Push-Location -LiteralPath $RepoRoot
    try {
        & python -m tools.process_guard stop --meta $meta
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    Remove-Item -LiteralPath $MarkerPath -Force -ErrorAction SilentlyContinue
    Write-Host "[stop] process_guard stop exit code: $code"
    exit $code
}

function Test-Readiness {
    param([int]$Port, [int]$TimeoutSeconds = 60)
    $url = "http://127.0.0.1:$Port/api/v1/readiness"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-RestMethod -Uri $url -TimeoutSec 3
            if ($r.status -eq "ready" -and $r.migrations -eq "ok") { return $true }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# ------------------------------------------------------------------------
# -Stop mode
# ------------------------------------------------------------------------
if ($Stop) {
    Invoke-DemoStop -MarkerPath $markerPath -RepoRoot $repoRoot    # exits
}

# ------------------------------------------------------------------------
# port preflight (a server may already be running)
# ------------------------------------------------------------------------
if (Test-PortInUse -Port $Port) {
    Write-Host "ERROR: port $Port is already listening." -ForegroundColor Red
    Write-Host "A demo server may already be running. Either:"
    Write-Host "  - stop it with:  powershell -ExecutionPolicy Bypass -File scripts\stop-demo.ps1"
    Write-Host "  - or pick another port:  .\scripts\start-demo.ps1 -Port $($Port + 1)"
    exit 1
}

# ------------------------------------------------------------------------
# main lifecycle (auto-stops on any failure after this point)
# ------------------------------------------------------------------------
$launched = $false
try {
    # --- backend dependencies ------------------------------------------
    Push-Location -LiteralPath $repoRoot
    try {
        Write-Host "[setup] checking backend deps (import app.main)..."
        & python -c "import app.main"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[setup] installing backend (editable + dev extras)..."
            & python -m pip install -e "./backend[dev]"
            if ($LASTEXITCODE -ne 0) { throw "backend install failed" }
            & python -c "import app.main" | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "backend still not importable after install" }
        }
    } finally {
        Pop-Location
    }

    # --- frontend deps + build ------------------------------------------
    $frontendDir = Join-Path $repoRoot "frontend"
    $nodeMods = Join-Path $frontendDir "node_modules"
    $distIndex = Join-Path $frontendDir "dist\index.html"
    if (-not (Test-Path -LiteralPath $nodeMods)) {
        Write-Host "[setup] installing frontend deps (npm install)..."
        Push-Location -LiteralPath $frontendDir
        try {
            & npm.cmd install
            if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        } finally {
            Pop-Location
        }
    }
    if ($Rebuild -or (-not (Test-Path -LiteralPath $distIndex))) {
        Write-Host "[setup] building frontend (npm run build)..."
        Push-Location -LiteralPath $frontendDir
        try {
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
        } finally {
            Pop-Location
        }
        if (-not (Test-Path -LiteralPath $distIndex)) { throw "frontend build output missing: $distIndex" }
    }

    # --- migrations ------------------------------------------------------
    Push-Location -LiteralPath $repoRoot
    try {
        Write-Host "[setup] applying database migrations (alembic upgrade head)..."
        & python -m alembic -c backend/alembic.ini upgrade head
        if ($LASTEXITCODE -ne 0) { throw "alembic migration failed" }
    } finally {
        Pop-Location
    }

    # --- launch via the repository lifecycle guard ----------------------
    # The SPA is served by the SAME uvicorn when STATIC_DIR (absolute path to
    # the frontend production build) is set - single origin, no CORS needed.
    # --no-proxy-headers is load-bearing (DEF-094): uvicorn's platform default
    # --proxy-headers trusts loopback and rewrites request.client from a
    # hostile X-Forwarded-For BEFORE the app's TRUST_PROXY=false identity gate.
    $distAbs = (Resolve-Path -LiteralPath (Join-Path $frontendDir "dist")).Path
    if (-not $env:STATIC_DIR) { $env:STATIC_DIR = $distAbs }
    if (-not $env:PD_FILE_LOGS) { $env:PD_FILE_LOGS = "true" }
    $logArgs = @()
    if ($env:PD_FILE_LOGS -notmatch "^(0|false|no)$") {
        if (-not $env:PD_LOG_FILE) {
            $env:PD_LOG_FILE = Join-Path $repoRoot "logs\procedural-detective.log"
        }
        $consoleLogFile = Join-Path $repoRoot "logs\procedural-detective-console.log"
        $logArgs = @("--log-file", $consoleLogFile)
        Write-Host "[logs] structured: $env:PD_LOG_FILE (rotating, 5 MB, 3 backups)"
        Write-Host "[logs] launcher console: $consoleLogFile"
    } else {
        Write-Host "[logs] console only"
    }
    Write-Host "[start] launching server on 127.0.0.1:$Port (STATIC_DIR=$env:STATIC_DIR)..."
    Push-Location -LiteralPath $repoRoot
    try {
        & python -m tools.process_guard launch `
            --cmd python `
            --port $Port `
            --meta $metaPath `
            $logArgs `
            --args -m uvicorn app.main:app --host 127.0.0.1 --port $Port --no-proxy-headers
        if ($LASTEXITCODE -ne 0) { throw "launch failed (see output above)" }
    } finally {
        Pop-Location
    }
    $launched = $true
    Set-Content -LiteralPath $markerPath -Value $metaPath -Encoding ASCII
    Write-Host "[start] demo tracked in $markerPath"

    # --- readiness ------------------------------------------------------
    Write-Host "[start] waiting for readiness on http://127.0.0.1:$Port/api/v1/readiness..."
    if (-not (Test-Readiness -Port $Port)) {
        Write-Host "ERROR: the demo backend did not become ready within 60 seconds." -ForegroundColor Red
        Write-Host "This usually means a migrations/database problem. Check the alembic output above;"
        Write-Host "then rerun:  python -m alembic -c backend/alembic.ini upgrade head   from the repo root."
        exit 1
    }
    Write-Host "[start] readiness OK (status=ready, migrations=ok)."

    # --- browser ---------------------------------------------------------
    if (-not $NoBrowser) {
        Write-Host "[start] opening http://localhost:$Port in your browser..."
        Start-Process "http://localhost:$Port"
    }

    # --- hold, then stop on Enter / Ctrl+C -------------------------------
    Write-Host ""
    Write-Host "Procedural Detective running at http://localhost:$Port - press Enter to stop."
    $null = Read-Host
} finally {
    # ALWAYS stop via the lifecycle guard and prove the port is free.
    if ($launched) {
        Write-Host "[stop] stopping the demo server via tools.process_guard..."
        Push-Location -LiteralPath $repoRoot
        try {
            & python -m tools.process_guard stop --meta $metaPath
            $finallyExit = $LASTEXITCODE
        } catch {
            $finallyExit = 1
        } finally {
            Pop-Location
        }
        Remove-Item -LiteralPath $markerPath -Force -ErrorAction SilentlyContinue
        if (Test-PortInUse -Port $Port) {
            Write-Host "[stop] ERROR: port $Port is still listening (process_guard exit $finallyExit)." -ForegroundColor Red
        } else {
            Write-Host "[stop] SHUTDOWN_OK - port $Port is free."
        }
    } else {
        Write-Host "[stop] no demo server was launched; nothing to stop."
    }
}
