$ErrorActionPreference = 'Continue'
$project = Split-Path $PSScriptRoot -Parent
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$desktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
$python = 'C:\ProgramData\miniconda3\python.exe'
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
if ($LASTEXITCODE -eq 0) {
    & $docker compose -f (Join-Path $project 'backend\docker-compose.yml') up -d *> $null
    Write-Log "Docker Compose start exit code: $LASTEXITCODE"
} else {
    Write-Log 'Docker unavailable after 10 minutes; web server will still start'
}

Write-Log 'Web server listening on 127.0.0.1:8765'
& $python (Join-Path $project 'scripts\serve.py') 2>&1 |
    ForEach-Object { Add-Content -LiteralPath $log -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $_" }
