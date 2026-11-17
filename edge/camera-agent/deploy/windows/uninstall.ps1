<#
.SYNOPSIS
  Remove the edge agent's scheduled task. Data (C:\ProgramData\aci-agent: events, cameras,
  credentials, backups) is KEPT unless -Purge is given.

    .\deploy\windows\uninstall.ps1            # stop + unregister the task
    .\deploy\windows\uninstall.ps1 -Purge     # also delete all agent data (irreversible)
#>
[CmdletBinding()]
param([string]$DataDir = "$env:ProgramData\aci-agent", [switch]$Purge)
$ErrorActionPreference = "Stop"
$TaskName = "ACI Edge Agent"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "[uninstall] task removed"
}
if ($Purge) {
  $answer = Read-Host "Delete ALL agent data in $DataDir? Type 'purge'"
  if ($answer -eq "purge") { Remove-Item -Recurse -Force $DataDir; Write-Host "[uninstall] data deleted" }
  else { Write-Host "[uninstall] data kept" }
} else {
  Write-Host "[uninstall] data kept in $DataDir"
}
