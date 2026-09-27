$ErrorActionPreference = 'Stop'
$taskName = 'Paperlight Background'
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Disable-ScheduledTask -TaskName $taskName | Out-Null
docker compose -f (Join-Path (Split-Path $PSScriptRoot -Parent) 'docker-compose.yml') stop
Write-Output 'Paperlight Compose services stopped. Run scripts\install-background.ps1 to restart.'
