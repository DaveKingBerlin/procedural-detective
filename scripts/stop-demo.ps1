# stop-demo.ps1 - stop the running demo started by scripts\start-demo.ps1.
#
# Reads <repo>\.pd-demo-meta, drives `python -m tools.process_guard stop`,
# verifies the port is free, and removes the marker. No-op (exit 0) when no
# demo is running.

[CmdletBinding()]
param(
    [string]$RepoRoot = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent (Resolve-Path -LiteralPath $MyInvocation.MyCommand.Path)
if ($RepoRoot) {
    $repoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
} else {
    $repoRoot = Split-Path -Parent $scriptDir
}
$markerPath = Join-Path $repoRoot ".pd-demo-meta"

function Test-PortInUse {
    param([int]$Port)
    try {
        $conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
        return ($conns.Count -gt 0)
    } catch {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        try {
            $listener.Start()
            return $false
        } catch {
            return $true
        } finally {
            try { $listener.Stop() } catch { }
        }
    }
}

if (-not (Test-Path -LiteralPath $markerPath)) {
    Write-Host "No running demo found (no marker at $markerPath). Nothing to stop."
    exit 0
}

$meta = (Get-Content -LiteralPath $markerPath -Raw).Trim()
if (-not (Test-Path -LiteralPath $meta)) {
    Write-Host "Stale marker: process metadata '$meta' is missing. Removing marker - nothing to stop."
    Remove-Item -LiteralPath $markerPath -Force -ErrorAction SilentlyContinue
    exit 0
}

# Read the recorded port BEFORE the guard erases the metadata on success.
$recordedPort = $null
try {
    $metaObj = Get-Content -LiteralPath $meta -Raw | ConvertFrom-Json
    $recordedPort = [int]$metaObj.port
} catch {
    $recordedPort = $null
}

Write-Host "Stopping the Procedural Detective demo (meta: $meta)..."
Push-Location -LiteralPath $repoRoot
try {
    & python -m tools.process_guard stop --meta $meta
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    Write-Host "process_guard stop did not complete (exit $exitCode). Marker kept at $markerPath."
    exit $exitCode
}

Remove-Item -LiteralPath $markerPath -Force -ErrorAction SilentlyContinue
if ($recordedPort -ne $null) {
    if (Test-PortInUse -Port $recordedPort) {
        Write-Host "ERROR: port $recordedPort is still listening after stop."
        exit 1
    } else {
        Write-Host "SHUTDOWN_OK - port $recordedPort is free."
    }
} else {
    Write-Host "SHUTDOWN_OK (no port recorded in meta)."
}
exit 0