<#
.SYNOPSIS
  Install NAI Deep Agents as a Windows service with NSSM, or as a Scheduled Task if NSSM is missing.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\windows\install-service.ps1 -InstallDir C:\nai-deep-agents
#>
param(
  [string]$InstallDir = (Resolve-Path "$PSScriptRoot\..\..").Path,
  [string]$ServiceName = "NaiDeepAgents",
  [int]$Port = 8000,
  [switch]$ScheduledTask
)
$ErrorActionPreference = "Stop"
$exe = Join-Path $InstallDir ".venv\Scripts\nai-agents.exe"
if (-not (Test-Path $exe)) { throw "Not found: $exe. Run 'uv sync' in $InstallDir first." }
$dataDir = Join-Path $env:ProgramData "nai-agents"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

$nssm = Get-Command nssm -ErrorAction SilentlyContinue
if ($nssm -and -not $ScheduledTask) {
  & nssm install $ServiceName $exe "serve --host 127.0.0.1 --port $Port"
  & nssm set $ServiceName AppDirectory $InstallDir
  & nssm set $ServiceName AppEnvironmentExtra "APP_DATA_DIR=$dataDir"
  & nssm set $ServiceName AppStdout (Join-Path $dataDir "service.log")
  & nssm set $ServiceName AppStderr (Join-Path $dataDir "service.err.log")
  & nssm set $ServiceName AppRotateFiles 1
  & nssm set $ServiceName Start SERVICE_AUTO_START
  & nssm start $ServiceName
  Write-Host "Service '$ServiceName' installed and started: http://127.0.0.1:$Port"
} else {
  $action = New-ScheduledTaskAction -Execute $exe -Argument "serve --host 127.0.0.1 --port $Port" -WorkingDirectory $InstallDir
  $trigger = New-ScheduledTaskTrigger -AtStartup
  $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
  $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Limited
  [Environment]::SetEnvironmentVariable("APP_DATA_DIR", $dataDir, "Machine")
  Register-ScheduledTask -TaskName $ServiceName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
  Start-ScheduledTask -TaskName $ServiceName
  Write-Host "Scheduled task '$ServiceName' registered and started: http://127.0.0.1:$Port"
}
