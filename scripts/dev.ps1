# Kill anything on the dev ports and start API + Vite fresh.
# Usage (from repo root):  powershell -File scripts/dev.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Stop-PortListeners([int[]]$Ports) {
  foreach ($port in $Ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
      $procId = $c.OwningProcess
      if ($procId -and $procId -ne 0) {
        Write-Host "Stopping PID $procId on port $port"
        # /T kills the whole tree. uvicorn --reload runs a worker child that
        # inherits the socket, so killing only the listed owner leaves the old
        # server answering on the port - which looks exactly like "my code
        # changes did not take effect".
        taskkill /F /T /PID $procId 2>$null | Out-Null
      }
    }
  }
  Start-Sleep -Seconds 1
}

function Stop-StraySsmServers {
  # Belt and braces: a worker whose parent already died is no longer reported
  # against the port, so match on the command line instead.
  Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "uvicorn" -and $_.CommandLine -match "app\.main:app" } |
    ForEach-Object {
      Write-Host "Stopping stray uvicorn PID $($_.ProcessId)"
      taskkill /F /T /PID $($_.ProcessId) 2>$null | Out-Null
    }
}

Write-Host "==> Freeing ports 8080 and 5173"
Stop-PortListeners 8080, 5173
Stop-StraySsmServers

$env:ADMIN_PASSWORD = if ($env:ADMIN_PASSWORD) { $env:ADMIN_PASSWORD } else { "admin" }
$env:SECRET_KEY = if ($env:SECRET_KEY) { $env:SECRET_KEY } else { "dev-secret" }
$env:DATA_DIR = (Resolve-Path ".\data").Path
$env:PUBLIC_BASE_URL = if ($env:PUBLIC_BASE_URL) { $env:PUBLIC_BASE_URL } else { "http://127.0.0.1:5173" }

# Turnstile and SMTP stay off locally unless you export them before running this.
# To exercise the widget without registering localhost on the real widget, use
# Cloudflare's always-passes test pair:
#   $env:TURNSTILE_SITE_KEY = "1x00000000000000000000AA"
#   $env:TURNSTILE_SECRET   = "1x0000000000000000000000000000000AA"
if ($env:TURNSTILE_SITE_KEY -and $env:TURNSTILE_SECRET) {
  Write-Host "==> Turnstile ENABLED (site key $($env:TURNSTILE_SITE_KEY))"
}

$py = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  # Keep this file pure ASCII. Windows PowerShell 5.1 reads a BOM-less .ps1 as
  # ANSI, so a UTF-8 em-dash decodes to a curly quote - which PowerShell accepts
  # as a string delimiter, breaking the parse with errors pointing elsewhere.
  Write-Error "Missing backend venv at backend\.venv - create it and install requirements first."
  exit 1
}

# uvicorn --reload spawns its worker through multiprocessing, which on Windows
# uses sys._base_executable. For a venv built on the Microsoft Store Python that
# is the WindowsApps stub, not this venv - so the reload worker cannot import
# anything installed here and silently keeps serving the last good build.
# Putting the venv's site-packages on PYTHONPATH makes the worker whole again.
$sitePackages = Join-Path $root "backend\.venv\Lib\site-packages"
if (Test-Path $sitePackages) {
  $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sitePackages;$env:PYTHONPATH" } else { $sitePackages }
}

Write-Host "==> Starting API  http://127.0.0.1:8080"
Start-Process -FilePath $py -ArgumentList @(
  "-m", "uvicorn", "app.main:app",
  "--reload", "--host", "127.0.0.1", "--port", "8080",
  "--app-dir", "backend"
) -WorkingDirectory $root -WindowStyle Minimized

Write-Host "==> Starting UI   http://127.0.0.1:5173"
# Launch through cmd: npm is npm.cmd, a batch file, and Start-Process -FilePath
# "npm" does not resolve it - it fails silently and leaves 5173 dead while the
# script still reports success.
Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", "npm run dev") `
  -WorkingDirectory (Join-Path $root "frontend") -WindowStyle Minimized

Write-Host ""
Write-Host "Waiting for both servers..."

# Do not just claim success. A dev server that failed to start looks exactly
# like stale code in the browser, which is a miserable thing to debug.
function Wait-Url([string]$Url, [int]$TimeoutSeconds = 45) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $null = Invoke-WebRequest $Url -TimeoutSec 3 -UseBasicParsing
      return $true
    } catch {
      if ($_.Exception.Response) { return $true }  # answered, even if non-200
    }
    Start-Sleep -Milliseconds 1000
  }
  return $false
}

$apiUp = Wait-Url "http://127.0.0.1:8080/api/health"
$uiUp = Wait-Url "http://127.0.0.1:5173/"

Write-Host ""
Write-Host "  API: http://127.0.0.1:8080/   $(if ($apiUp) { 'ready' } else { 'NOT RESPONDING' })"
Write-Host "  UI:  http://127.0.0.1:5173/   $(if ($uiUp) { 'ready' } else { 'NOT RESPONDING' })"

if ($apiUp -and $uiUp) {
  Write-Host ""
  Write-Host "Hard-refresh the browser (Ctrl+Shift+R)."
} else {
  Write-Host ""
  Write-Warning "Check the minimized console window for the failing server."
  exit 1
}
