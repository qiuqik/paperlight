$ErrorActionPreference = 'Continue'
$project = Split-Path $PSScriptRoot -Parent
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$desktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
$log = Join-Path $project 'result\background-service.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null

function Write-Log($message) {
    Add-Content -LiteralPath $log -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message"
}

Write-Log 'Starting Paperlight background task'
& $docker info *> $null
if ($LASTEXITCODE -ne 0) {
    if (Test-Path -LiteralPath $desktop) {
        Start-Process -FilePath $desktop -WindowStyle Hidden
        Write-Log 'Started Docker Desktop'
    }
}

for ($i = 0; $i -lt 120; $i++) {
    & $docker info *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 5
}
if ($LASTEXITCODE -ne 0) {
    Write-Log 'Docker unavailable after 10 minutes'
    exit 1
}

& $docker compose -f (Join-Path $project 'docker-compose.yml') up -d --no-build 2>&1 |
    ForEach-Object { Write-Log $_ }
$composeExit = $LASTEXITCODE
Write-Log "Paperlight Compose start exit code: $composeExit"
exit $composeExit
