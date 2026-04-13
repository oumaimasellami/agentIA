#!/usr/bin/env powershell
<#
.SYNOPSIS
MESX 0 - NRT System Startup Script (PowerShell)

.DESCRIPTION
Starts Neo4j, loads graph data, and launches the web server

.EXAMPLE
.\startup.ps1
#>

Write-Host ""
Write-Host "======================================" -ForegroundColor Blue
Write-Host " 🚀 MESX 0 - NRT System Startup" -ForegroundColor Blue
Write-Host "======================================" -ForegroundColor Blue
Write-Host ""

# Step 1: Start Neo4j
Write-Host "[1/3] Starting Neo4j Docker container..." -ForegroundColor Cyan
docker start neo4j
Write-Host "✓ Neo4j started" -ForegroundColor Green
Write-Host ""
Start-Sleep -Seconds 5

# Step 2: Load graph data
Write-Host "[2/3] Loading graph data into Neo4j..." -ForegroundColor Cyan
Set-Location "d:\agentIA\02_NEO4J_DATABASE"
& "D:\Python\bin\python.exe" build_graph_database.py
Set-Location "d:\agentIA"
Write-Host "✓ Graph data loaded" -ForegroundColor Green
Write-Host ""

# Step 3: Start web server
Write-Host "[3/3] Starting web server..." -ForegroundColor Cyan
Set-Location "d:\agentIA\05_WEB_INTERFACE"
Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host " ✅ ALL SYSTEMS READY!" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green
Write-Host ""
Write-Host "🌐 Dashboard: http://localhost:5000" -ForegroundColor Yellow
Write-Host "📊 Neo4j: http://localhost:7474" -ForegroundColor Yellow
Write-Host ""
Write-Host "Credentials:" -ForegroundColor White
Write-Host "  User: neo4j" -ForegroundColor Gray
Write-Host "  Password: forvia2025" -ForegroundColor Gray
Write-Host ""
Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
Write-Host "======================================" -ForegroundColor Green
Write-Host ""

& "D:\Python\bin\python.exe" server.py
