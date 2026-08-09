# Self-test for scripts/dev.ps1 readiness + failure reporting.
# Usage (from repo root):  powershell -File scripts/test_dev_ps1.ps1
#
# Drives the real scripts/dev.ps1 entry point (not a reimplementation).
# Exit 0 only if success path and controlled failure path both behave.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$scratch = if ($env:SSM_DEV_TEST_SCRATCH) {
  $env:SSM_DEV_TEST_SCRATCH
} else {
  Join-Path $root ".dev-logs\selftest"
}
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

$devScript = Join-Path $root "scripts\dev.ps1"
if (-not (Test-Path $devScript)) {
  throw "Missing $devScript"
}

function Invoke-DevScript {
  param(
    [string]$OutLog,
    [hashtable]$ExtraEnv = @{}
  )
  # Clear one-shot overrides unless the caller sets them.
  $oldApi = $env:SSM_DEV_API_CMD
  $oldUi = $env:SSM_DEV_UI_CMD
  try {
    if ($ExtraEnv.ContainsKey("SSM_DEV_API_CMD")) {
      $env:SSM_DEV_API_CMD = $ExtraEnv["SSM_DEV_API_CMD"]
    } else {
      Remove-Item Env:SSM_DEV_API_CMD -ErrorAction SilentlyContinue
    }
    if ($ExtraEnv.ContainsKey("SSM_DEV_UI_CMD")) {
      $env:SSM_DEV_UI_CMD = $ExtraEnv["SSM_DEV_UI_CMD"]
    } else {
      Remove-Item Env:SSM_DEV_UI_CMD -ErrorAction SilentlyContinue
    }

    $p = Start-Process -FilePath "powershell.exe" -ArgumentList @(
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $devScript
    ) -WorkingDirectory $root -Wait -PassThru -WindowStyle Hidden `
      -RedirectStandardOutput $OutLog -RedirectStandardError "$OutLog.err"
    return $p.ExitCode
  } finally {
    if ($null -eq $oldApi) { Remove-Item Env:SSM_DEV_API_CMD -ErrorAction SilentlyContinue }
    else { $env:SSM_DEV_API_CMD = $oldApi }
    if ($null -eq $oldUi) { Remove-Item Env:SSM_DEV_UI_CMD -ErrorAction SilentlyContinue }
    else { $env:SSM_DEV_UI_CMD = $oldUi }
  }
}

function Assert-EndpointsHealthy {
  param([string]$Label)
  $api = Invoke-WebRequest "http://127.0.0.1:8080/api/health" -TimeoutSec 5 -UseBasicParsing
  if ($api.StatusCode -ne 200) { throw "$Label API status $($api.StatusCode)" }
  $api.Content | Set-Content -Path (Join-Path $scratch "api-health-$Label.json") -Encoding utf8
  $json = $api.Content | ConvertFrom-Json
  if ($json.status -ne "ok") { throw "$Label health status is '$($json.status)', expected ok" }

  $ui = Invoke-WebRequest "http://127.0.0.1:5173/" -TimeoutSec 5 -UseBasicParsing
  if ($ui.StatusCode -ne 200) { throw "$Label UI status $($ui.StatusCode)" }
  if (-not $ui.Content -or $ui.Content.Length -lt 50) { throw "$Label UI body too short" }
  if ($ui.Content -notmatch "<html|<!DOCTYPE html|id=`"root`"") {
    throw "$Label UI body does not look like the Vite/app shell"
  }
  $ui.Content | Set-Content -Path (Join-Path $scratch "ui-root-$Label.html") -Encoding utf8
  Write-Host "OK endpoints ($Label)"
}

$failures = @()

# --- Success path (twice) ---
foreach ($n in 1, 2) {
  Write-Host "==> success run $n"
  $out = Join-Path $scratch "dev-run-$n.log"
  $code = Invoke-DevScript -OutLog $out
  $text = Get-Content $out -Raw -ErrorAction SilentlyContinue
  $err = Get-Content "$out.err" -Raw -ErrorAction SilentlyContinue
  if ($code -ne 0) {
    $failures += "success run $n exited $code"
    Write-Host $text
    Write-Host $err
    continue
  }
  if ($text -notmatch "API:.*ready" -or $text -notmatch "UI:.*ready") {
    $failures += "success run $n did not report both ready"
    Write-Host $text
    continue
  }
  if ($text -match "FAILED") {
    $failures += "success run $n stdout contains FAILED"
  }
  try {
    Assert-EndpointsHealthy -Label "run$n-immediate"
    Start-Sleep -Seconds 5
    Assert-EndpointsHealthy -Label "run$n-after5s"
  } catch {
    $failures += "success run $n health: $($_.Exception.Message)"
  }
}

# --- Controlled failure path: UI command exits immediately ---
Write-Host "==> controlled UI failure"
$failOut = Join-Path $scratch "dev-fail.log"
# Keep API healthy so the failure side is clearly the UI; bad npm-like command.
$failUiLog = Join-Path $root ".dev-logs\ui.log"
$failCode = Invoke-DevScript -OutLog $failOut -ExtraEnv @{
  # Body of run-ui.cmd (script wraps @echo off). Exit non-zero immediately.
  SSM_DEV_UI_CMD = "echo UI boom simulated> `"$failUiLog`" & exit /b 7"
}
$failText = @(
  Get-Content $failOut -Raw -ErrorAction SilentlyContinue
  Get-Content "$failOut.err" -Raw -ErrorAction SilentlyContinue
) -join "`n"

if ($failCode -eq 0) {
  $failures += "failure path exited 0 (expected non-zero)"
}
if ($failText -notmatch "UI:.*FAILED|UI process exited|UI did not become ready|UI died|UI spawn FAILED") {
  $failures += "failure path did not label UI as failed"
}
if ($failText -notmatch "ui\.log|Log tails|-----|\.dev-logs") {
  $failures += "failure path did not surface log path/tail"
}
# Copy log tails evidence
$tails = Join-Path $scratch "log-tails.txt"
@(
  "=== fail stdout ==="
  $failText
  "=== .dev-logs/ui.log ==="
  (Get-Content (Join-Path $root ".dev-logs\ui.log") -Raw -ErrorAction SilentlyContinue)
  "=== .dev-logs/api.err.log (tail) ==="
  (Get-Content (Join-Path $root ".dev-logs\api.err.log") -Tail 20 -ErrorAction SilentlyContinue)
) | Set-Content -Path $tails -Encoding utf8

Write-Host "failure path exit=$failCode (expect non-zero)"

# --- Restore clean successful start ---
Write-Host "==> restore success"
$restoreOut = Join-Path $scratch "dev-restore.log"
$restoreCode = Invoke-DevScript -OutLog $restoreOut
if ($restoreCode -ne 0) {
  $failures += "restore run exited $restoreCode"
  Write-Host (Get-Content $restoreOut -Raw -ErrorAction SilentlyContinue)
} else {
  try {
    Assert-EndpointsHealthy -Label "restore"
  } catch {
    $failures += "restore health: $($_.Exception.Message)"
  }
}

Write-Host ""
if ($failures.Count -eq 0) {
  Write-Host "ALL self-tests passed. Evidence under $scratch"
  exit 0
}

Write-Host "SELF-TEST FAILURES:" -ForegroundColor Red
foreach ($f in $failures) { Write-Host " - $f" -ForegroundColor Red }
exit 1
