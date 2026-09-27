$ErrorActionPreference = 'Stop'
$taskName = 'Paperlight Background'
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Disable-ScheduledTask -TaskName $taskName | Out-Null
docker compose -f (Join-Path (Split-Path $PSScriptRoot -Parent) 'backend\docker-compose.yml') down
Write-Output 'Paperlight web and API stopped. Run scripts\install-background.ps1 to restart.'
