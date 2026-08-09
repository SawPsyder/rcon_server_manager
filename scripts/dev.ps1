# Kill anything on the dev ports and start API + Vite fresh.
# Usage (from repo root):  powershell -File scripts/dev.ps1
#
# Both processes write to .dev-logs/ so a failed start prints real stderr instead
# of claiming "ready" from a brief false-positive health check.
#
# Processes are spawned via Win32_Process.Create (parented by WMI, not this
# shell) so API/UI stay up after this script exits - including under job-object
# shells that tear down Start-Process children with the parent. Launchers under
# .dev-logs/ carry the env vars WMI would not inherit from this session.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$logDir = Join-Path $root ".dev-logs"
if (-not (Test-Path $logDir)) {
  New-Item -ItemType Directory -Path $logDir | Out-Null
}
$apiOutLog = Join-Path $logDir "api.out.log"
$apiErrLog = Join-Path $logDir "api.err.log"
$uiLog = Join-Path $logDir "ui.log"
$apiPidFile = Join-Path $logDir "api.pid"
$uiPidFile = Join-Path $logDir "ui.pid"
$apiLauncher = Join-Path $logDir "run-api.cmd"
$uiLauncher = Join-Path $logDir "run-ui.cmd"

function Stop-PortListeners([int[]]$Ports) {
  foreach ($port in $Ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
      $procId = $c.OwningProcess
      if ($procId -and $procId -ne 0) {
        # Only kill if the owner is still a real process - stale LISTENING rows
        # with dead PIDs are common on Windows after uvicorn --reload.
        if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) { continue }
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

function Stop-OrphanMultiprocessingWorkers {
  # uvicorn --reload on Windows leaves spawn_main workers holding the socket
  # after the reloader PID dies. netstat still attributes LISTENING to the dead
  # parent, so Stop-PortListeners misses them and health checks look "fine"
  # while new starts cannot bind or write logs.
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and
      $_.CommandLine -match "multiprocessing\.spawn" -and
      $_.CommandLine -match "spawn_main"
    } |
    ForEach-Object {
      $ppid = $null
      if ($_.CommandLine -match "parent_pid=(\d+)") {
        $ppid = [int]$Matches[1]
      }
      $parentAlive = $false
      if ($ppid) {
        $parentAlive = [bool](Get-Process -Id $ppid -ErrorAction SilentlyContinue)
      }
      if (-not $parentAlive) {
        Write-Host "Stopping orphan spawn worker PID $($_.ProcessId) (dead parent $ppid)"
        taskkill /F /T /PID $($_.ProcessId) 2>$null | Out-Null
      }
    }
}

function Stop-StraySsmServers {
  # Belt and braces: a worker whose parent already died is no longer reported
  # against the port, so match on the command line instead.
  Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and (
        ($_.CommandLine -match "uvicorn" -and $_.CommandLine -match "app\.main:app") -or
        ($_.CommandLine -match "uvicorn" -and $_.CommandLine -match "8080")
      )
    } |
    ForEach-Object {
      Write-Host "Stopping stray uvicorn PID $($_.ProcessId)"
      taskkill /F /T /PID $($_.ProcessId) 2>$null | Out-Null
    }
  Stop-OrphanMultiprocessingWorkers
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and (
        ($_.CommandLine -match "vite" -and $_.CommandLine -match "5173") -or
        ($_.CommandLine -match "npm run dev") -or
        ($_.CommandLine -match "run-ui\.cmd") -or
        ($_.CommandLine -match "run-api\.cmd")
      )
    } |
    ForEach-Object {
      Write-Host "Stopping stray UI/launcher PID $($_.ProcessId)"
      taskkill /F /T /PID $($_.ProcessId) 2>$null | Out-Null
    }
}

function Test-UrlReady([string]$Url) {
  try {
    $null = Invoke-WebRequest $Url -TimeoutSec 3 -UseBasicParsing
    return $true
  } catch {
    # Non-2xx still means something is listening and answering.
    if ($_.Exception.Response) { return $true }
    return $false
  }
}

function Test-PortListening([int]$Port) {
  # Require a live owner. Windows can keep LISTENING rows around after the
  # process is gone; treating those as "up" is how we used to print ready
  # for a corpse.
  $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    if ($c.OwningProcess -and (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue)) {
      return $true
    }
  }
  return $false
}

function Show-LogTail([string]$Path, [int]$Lines = 50) {
  Write-Host ""
  if (-not (Test-Path $Path)) {
    Write-Host "  (no log yet: $Path)"
    return
  }
  $len = (Get-Item $Path -ErrorAction SilentlyContinue).Length
  if (-not $len) {
    Write-Host "  (empty log: $Path)"
    return
  }
  Write-Host "----- $Path (last $Lines lines) -----"
  Get-Content $Path -Tail $Lines -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
  Write-Host "----- end -----"
}

function Start-DetachedProcess {
  param(
    [Parameter(Mandatory = $true)][string]$CommandLine,
    [Parameter(Mandatory = $true)][string]$WorkingDirectory,
    [Parameter(Mandatory = $true)][string]$Name
  )
  if (-not (Test-Path $WorkingDirectory)) {
    throw "$Name working directory missing: $WorkingDirectory"
  }
  # Parent is WmiPrvSE, not this shell - survives script exit and many job teardowns.
  $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine      = $CommandLine
    CurrentDirectory = $WorkingDirectory
  }
  if ($null -eq $result -or $result.ReturnValue -ne 0) {
    $code = if ($null -eq $result) { "null" } else { $result.ReturnValue }
    throw "$Name Win32_Process.Create failed (ReturnValue=$code). Command: $CommandLine"
  }
  $proc = Get-Process -Id $result.ProcessId -ErrorAction SilentlyContinue
  if (-not $proc) {
    throw "$Name started as PID $($result.ProcessId) but process handle is gone already."
  }
  return $proc
}

function Wait-DevServer {
  param(
    [string]$Name,
    [string]$Url,
    [int]$Port,
    [System.Diagnostics.Process]$Process,
    [string[]]$LogPaths,
    [int]$TimeoutSeconds = 45,
    [int]$SettleSeconds = 3
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if ($null -ne $Process) {
      $Process.Refresh()
      if ($Process.HasExited) {
        # Launcher may exit while a child still serves (rare). Only treat as
        # failure if the endpoint is also dead.
        if ((Test-UrlReady $Url) -and (Test-PortListening $Port)) {
          Start-Sleep -Seconds $SettleSeconds
          if ((Test-UrlReady $Url) -and (Test-PortListening $Port)) {
            Write-Host "$Name launcher exited but endpoint is healthy - treating as ready."
            return $true
          }
        }
        $code = $Process.ExitCode
        Write-Host ""
        Write-Host "$Name process exited early (exit code $code)." -ForegroundColor Red
        foreach ($p in $LogPaths) { Show-LogTail $p }
        return $false
      }
    }

    if ((Test-UrlReady $Url) -and (Test-PortListening $Port)) {
      # Settle, then re-check process + URL + port so we never print "ready"
      # for a process that dies right after the first response.
      Start-Sleep -Seconds $SettleSeconds
      if ($null -ne $Process) {
        $Process.Refresh()
        if ($Process.HasExited) {
          $code = $Process.ExitCode
          Write-Host ""
          Write-Host "$Name died right after first response (exit code $code)." -ForegroundColor Red
          foreach ($p in $LogPaths) { Show-LogTail $p }
          return $false
        }
      }
      if (-not (Test-UrlReady $Url)) {
        Write-Host ""
        Write-Host "$Name answered once then stopped responding." -ForegroundColor Red
        foreach ($p in $LogPaths) { Show-LogTail $p }
        return $false
      }
      if (-not (Test-PortListening $Port)) {
        Write-Host ""
        Write-Host "$Name is no longer listening on port $Port." -ForegroundColor Red
        foreach ($p in $LogPaths) { Show-LogTail $p }
        return $false
      }
      return $true
    }

    Start-Sleep -Milliseconds 500
  }

  Write-Host ""
  Write-Host "$Name did not become ready within ${TimeoutSeconds}s." -ForegroundColor Red
  if ($null -ne $Process) {
    $Process.Refresh()
    if ($Process.HasExited) {
      Write-Host "  Process exit code: $($Process.ExitCode)"
    } else {
      Write-Host "  Process still running (PID $($Process.Id)) but URL never answered."
    }
  }
  foreach ($p in $LogPaths) { Show-LogTail $p }
  return $false
}

function Escape-CmdSetValue([string]$Value) {
  if ($null -eq $Value) { return "" }
  return ($Value -replace '"', '""')
}

function Write-ApiLauncher {
  param([string]$Path)
  # Optional one-shot override for self-tests: SSM_DEV_API_CMD = body after @echo off
  if ($env:SSM_DEV_API_CMD) {
    Set-Content -Path $Path -Value "@echo off`r`n$($env:SSM_DEV_API_CMD)`r`n" -Encoding ascii
    return
  }
  $lines = @(
    "@echo off"
    "set `"ADMIN_PASSWORD=$(Escape-CmdSetValue $env:ADMIN_PASSWORD)`""
    "set `"SECRET_KEY=$(Escape-CmdSetValue $env:SECRET_KEY)`""
    "set `"DATA_DIR=$(Escape-CmdSetValue $env:DATA_DIR)`""
    "set `"PUBLIC_BASE_URL=$(Escape-CmdSetValue $env:PUBLIC_BASE_URL)`""
    "set `"PYTHONPATH=$(Escape-CmdSetValue $env:PYTHONPATH)`""
    "cd /d `"$(Escape-CmdSetValue $root)`""
    "`"$(Escape-CmdSetValue $py)`" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8080 --app-dir backend 1>`"$(Escape-CmdSetValue $apiOutLog)`" 2>`"$(Escape-CmdSetValue $apiErrLog)`""
  )
  Set-Content -Path $Path -Value ($lines -join "`r`n") -Encoding ascii
}

function Write-UiLauncher {
  param([string]$Path)
  # Optional one-shot override for self-tests: SSM_DEV_UI_CMD = body after @echo off
  if ($env:SSM_DEV_UI_CMD) {
    Set-Content -Path $Path -Value "@echo off`r`n$($env:SSM_DEV_UI_CMD)`r`n" -Encoding ascii
    return
  }
  $lines = @(
    "@echo off"
    "cd /d `"$(Escape-CmdSetValue $frontendDir)`""
    "npm run dev 1>`"$(Escape-CmdSetValue $uiLog)`" 2>&1"
  )
  Set-Content -Path $Path -Value ($lines -join "`r`n") -Encoding ascii
}

Write-Host "==> Freeing ports 8080 and 5173"
Stop-PortListeners 8080, 5173
Stop-StraySsmServers
# Second pass after orphan workers die - releases sockets and log handles.
Stop-PortListeners 8080, 5173
Start-Sleep -Seconds 1

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

# Fresh logs each run so a failure dumps only this attempt. Retry briefly:
# orphan workers can hold log handles a moment after taskkill.
function Reset-LogFile([string]$Path) {
  for ($i = 0; $i -lt 10; $i++) {
    try {
      if (Test-Path $Path) { Remove-Item $Path -Force -ErrorAction Stop }
      New-Item -ItemType File -Path $Path -Force -ErrorAction Stop | Out-Null
      return
    } catch {
      Start-Sleep -Milliseconds 200
    }
  }
  # Last resort: truncate in place if delete is denied.
  try {
    Set-Content -Path $Path -Value "" -Encoding ascii -ErrorAction Stop
  } catch {
    Write-Host "Warning: could not reset log $Path : $($_.Exception.Message)" -ForegroundColor Yellow
  }
}
foreach ($f in @($apiPidFile, $uiPidFile, $apiLauncher, $uiLauncher)) {
  if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
}
foreach ($f in @($apiOutLog, $apiErrLog, $uiLog)) {
  Reset-LogFile $f
}

$frontendDir = Join-Path $root "frontend"
Write-ApiLauncher -Path $apiLauncher
Write-UiLauncher -Path $uiLauncher

$apiCmd = "cmd.exe /c `"$(Escape-CmdSetValue $apiLauncher)`""
$uiCmd = "cmd.exe /c `"$(Escape-CmdSetValue $uiLauncher)`""

Write-Host "==> Starting API  http://127.0.0.1:8080"
Write-Host "    logs: $apiOutLog"
Write-Host "          $apiErrLog"
Write-Host "    launcher: $apiLauncher"
try {
  $apiProc = Start-DetachedProcess -CommandLine $apiCmd -WorkingDirectory $root -Name "API"
} catch {
  Write-Host "API spawn FAILED: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
}
$apiProc.Id | Set-Content -Path $apiPidFile -Encoding ascii
Write-Host "    pid:  $($apiProc.Id)"

Write-Host "==> Starting UI   http://127.0.0.1:5173"
Write-Host "    log:  $uiLog"
Write-Host "    launcher: $uiLauncher"
try {
  $uiProc = Start-DetachedProcess -CommandLine $uiCmd -WorkingDirectory $frontendDir -Name "UI"
} catch {
  Write-Host "UI spawn FAILED: $($_.Exception.Message)" -ForegroundColor Red
  Show-LogTail $uiLog
  if ($apiProc -and -not $apiProc.HasExited) {
    taskkill /F /T /PID $apiProc.Id 2>$null | Out-Null
  }
  exit 1
}
$uiProc.Id | Set-Content -Path $uiPidFile -Encoding ascii
Write-Host "    pid:  $($uiProc.Id)"

Write-Host ""
Write-Host "Waiting for both servers (process + port + HTTP, with settle recheck)..."

$apiUp = Wait-DevServer -Name "API" -Url "http://127.0.0.1:8080/api/health" -Port 8080 `
  -Process $apiProc -LogPaths @($apiErrLog, $apiOutLog)
$uiUp = Wait-DevServer -Name "UI" -Url "http://127.0.0.1:5173/" -Port 5173 `
  -Process $uiProc -LogPaths @($uiLog)

Write-Host ""
if ($apiUp) {
  Write-Host "  API: http://127.0.0.1:8080/   ready (PID $($apiProc.Id))"
} else {
  Write-Host "  API: http://127.0.0.1:8080/   FAILED" -ForegroundColor Red
}
if ($uiUp) {
  Write-Host "  UI:  http://127.0.0.1:5173/   ready (PID $($uiProc.Id))"
} else {
  Write-Host "  UI:  http://127.0.0.1:5173/   FAILED" -ForegroundColor Red
}

if ($apiUp -and $uiUp) {
  Write-Host ""
  Write-Host "Hard-refresh the browser (Ctrl+Shift+R)."
  Write-Host "Logs keep writing under $logDir"
  exit 0
}

Write-Host ""
Write-Warning "One or both servers failed. Log tails are above; full logs under $logDir"
# Leave whichever process is still healthy running so a partial start is usable;
# only report failure via exit code.
exit 1
