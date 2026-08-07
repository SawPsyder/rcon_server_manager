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
        taskkill /F /PID $procId 2>$null | Out-Null
      }
    }
  }
  Start-Sleep -Seconds 1
}

Write-Host "==> Freeing ports 8080 and 5173"
Stop-PortListeners 8080, 5173

$env:ADMIN_PASSWORD = if ($env:ADMIN_PASSWORD) { $env:ADMIN_PASSWORD } else { "admin" }
$env:SECRET_KEY = if ($env:SECRET_KEY) { $env:SECRET_KEY } else { "dev-secret" }
$env:DATA_DIR = (Resolve-Path ".\data").Path

$py = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Error "Missing backend venv at backend\.venv — create it and install requirements first."
  exit 1
}

Write-Host "==> Starting API  http://127.0.0.1:8080"
Start-Process -FilePath $py -ArgumentList @(
  "-m", "uvicorn", "app.main:app",
  "--reload", "--host", "127.0.0.1", "--port", "8080",
  "--app-dir", "backend"
) -WorkingDirectory $root -WindowStyle Minimized

Write-Host "==> Starting UI   http://127.0.0.1:5173"
Start-Process -FilePath "npm" -ArgumentList @("run", "dev") `
  -WorkingDirectory (Join-Path $root "frontend") -WindowStyle Minimized

Write-Host ""
Write-Host "Dev servers launching in minimized windows."
Write-Host "  UI:  http://127.0.0.1:5173/"
Write-Host "  API: http://127.0.0.1:8080/"
Write-Host "Hard-refresh the browser (Ctrl+Shift+R) after they come up."
