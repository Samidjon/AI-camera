<#
.SYNOPSIS
  AI Camera Intelligence - edge agent installer for Windows (mini PC / NVR box / workstation).

.DESCRIPTION
  Runs the agent at system start-up (before anyone logs in) and restarts it after a crash, via a
  Windows Scheduled Task running as LocalSystem. Nothing is downloaded by this script except the
  locked Python dependencies resolved by `uv sync --frozen`.

    Code + virtualenv   <AppDir>           (default: the folder this script belongs to)
    Data + secrets      C:\ProgramData\aci-agent   (ACL: SYSTEM + Administrators only)
    Configuration       C:\ProgramData\aci-agent\.env
    Task                "ACI Edge Agent"   (at start-up, restart every 1 min on failure)

  Run in an elevated PowerShell:
    .\deploy\windows\install.ps1 [-ServerUrl https://cameras.example.com] [-EnrollmentToken <token>] [-WithClaude] [-NoStart]

  Requirements: uv (https://docs.astral.sh/uv/) on PATH. uv provides Python 3.12 if missing.
#>
[CmdletBinding()]
param(
  [string]$AppDir = (Resolve-Path "$PSScriptRoot\..\..").Path,
  [string]$DataDir = "$env:ProgramData\aci-agent",
  [string]$ServerUrl = "",
  [string]$EnrollmentToken = "",
  [switch]$WithClaude,
  [switch]$NoStart
)
$ErrorActionPreference = "Stop"
$TaskName = "ACI Edge Agent"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Run this script in an elevated PowerShell (Run as administrator)."
}
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { throw "uv not found. Install it first: https://docs.astral.sh/uv/getting-started/installation/" }

Write-Host "[install] dependencies (uv sync --frozen) in $AppDir"
$extras = @()
if ($WithClaude) { $extras = @("--extra", "claude") }
Push-Location $AppDir
try { & $uv sync --frozen --no-dev --python 3.12 @extras; if ($LASTEXITCODE) { throw "uv sync failed" } }
finally { Pop-Location }
$python = Join-Path $AppDir ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "virtualenv python not found: $python" }

Write-Host "[install] data directory $DataDir"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
# Only SYSTEM and Administrators may read the data (databases, key file, credentials).
& icacls $DataDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null

$envFile = Join-Path $DataDir ".env"
if (-not (Test-Path $envFile)) {
  $jwt = -join ((1..48) | ForEach-Object { [char[]]"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" | Get-Random })
  @(
    "# AI Camera Intelligence - edge agent (Windows). Secrets: keep this file private.",
    "ACI_AGENT_ENVIRONMENT=production",
    "ACI_AGENT_DATA_DIR=$DataDir",
    "ACI_AGENT_HOST=127.0.0.1",
    "ACI_AGENT_PORT=8100",
    "# Must equal the central server's ACI_JWT_SECRET so dashboard sign-ins are accepted here.",
    "ACI_AGENT_JWT_SECRET=$jwt",
    "ACI_AGENT_SERVER_URL=$ServerUrl",
    "ACI_AGENT_SERVER_ENROLLMENT_TOKEN=$EnrollmentToken",
    "ACI_AGENT_AI_MODELS_DIR=$(Join-Path $AppDir 'models')"
  ) | Set-Content -Path $envFile -Encoding utf8
  Write-Host "[install] wrote $envFile - set ACI_AGENT_JWT_SECRET to the server's value if you use a central server"
} else {
  Write-Host "[install] keeping existing $envFile"
}

Write-Host "[install] scheduled task '$TaskName'"
$action = New-ScheduledTaskAction -Execute $python -Argument "-m aci_agent run" -WorkingDirectory $DataDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$taskPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
  -Principal $taskPrincipal -Description "AI Camera Intelligence edge agent (offline-first camera analysis)" -Force | Out-Null

if (-not $NoStart) {
  Start-ScheduledTask -TaskName $TaskName
  Write-Host "[install] started. Health: http://127.0.0.1:8100/api/v1/health"
}
Write-Host "[install] done. Logs: $DataDir (system log in the dashboard -> Devices -> This agent)."
