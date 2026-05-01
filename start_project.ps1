# Stable project startup script (no container/image recreation)
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\start_project.ps1

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "MESX NRT - Stable Startup" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Load Neo4j settings from .env when available so startup matches the app config.
$envFile = Join-Path $PSScriptRoot ".env"
$neo4jUri = "bolt://localhost:7687"
$neo4jUser = "neo4j"
$neo4jPassword = "neo4j2026!"

if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }

        $parts = $line.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1].Trim()

        switch ($key) {
            "NEO4J_URI" { $neo4jUri = $value }
            "NEO4J_USER" { $neo4jUser = $value }
            "NEO4J_PASSWORD" { $neo4jPassword = $value }
        }
    }
}

# 1) Validate Neo4j container presence
$neo4jExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^neo4j$" -Quiet
if (-not $neo4jExists) {
    Write-Host "[ERROR] Container 'neo4j' does not exist on this machine." -ForegroundColor Red
    Write-Host "Create it once, then use this script for daily startup." -ForegroundColor Yellow
    exit 1
}

# 2) Start Neo4j container (non-destructive)
Write-Host "[1/3] Starting Neo4j container..." -ForegroundColor Yellow
docker start neo4j | Out-Null

# 3) Wait for Bolt readiness using Python driver checks
Write-Host "[2/3] Waiting for Neo4j Bolt readiness..." -ForegroundColor Yellow
$maxAttempts = 20
$attempt = 0
$ready = $false

while ($attempt -lt $maxAttempts -and -not $ready) {
    $attempt += 1
    $result = D:\Python\bin\python.exe -c "from neo4j import GraphDatabase; d=GraphDatabase.driver(r'$neo4jUri', auth=(r'$neo4jUser', r'$neo4jPassword')); d.verify_connectivity(); d.close(); print('OK')" 2>&1

    if ($LASTEXITCODE -eq 0 -and $result -match "OK") {
        $ready = $true
    }
    else {
        Start-Sleep -Seconds 2
    }
}

if (-not $ready) {
    Write-Host "[ERROR] Neo4j is not reachable on $neo4jUri" -ForegroundColor Red
    if ($result) {
        Write-Host $result -ForegroundColor DarkYellow
    }
    Write-Host "Run: docker logs --tail 100 neo4j" -ForegroundColor Yellow
    exit 1
}

Write-Host "Neo4j is ready." -ForegroundColor Green

# 4) Start server
Write-Host "[3/3] Starting web server..." -ForegroundColor Yellow
Set-Location D:\agentIA

# If port 5000 is already used, stop stale process to avoid socket conflict.
$listener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    Write-Host "Port 5000 already in use by PID $($listener.OwningProcess). Stopping old process..." -ForegroundColor Yellow
    Stop-Process -Id $listener.OwningProcess -Force
}

D:\Python\bin\python.exe .\05_WEB_INTERFACE\server.py
