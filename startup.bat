@echo off
REM ================================================
REM MESX 0 - NRT System Startup Script (Windows)
REM ================================================

echo.
echo ======================================
echo  🚀 MESX 0 - NRT System Startup
echo ======================================
echo.

REM Step 1: Start Neo4j
echo [1/3] Starting Neo4j Docker container...
docker start neo4j
echo ✓ Neo4j started
echo.
timeout /t 5 /nobreak

REM Step 2: Load graph data
echo [2/3] Loading graph data into Neo4j...
cd /d d:\agentIA\02_NEO4J_DATABASE
D:\Python\bin\python.exe build_graph_database.py
cd /d d:\agentIA
echo ✓ Graph data loaded
echo.

REM Step 3: Start web server
echo [3/3] Starting web server...
cd /d d:\agentIA\05_WEB_INTERFACE
echo.
echo ======================================
echo  ✅ ALL SYSTEMS READY!
echo ======================================
echo.
echo 🌐 Dashboard: http://localhost:5000
echo 📊 Neo4j: http://localhost:7474
echo.
echo User: neo4j
echo Password: forvia2025
echo.
echo Press Ctrl+C to stop the server
echo ======================================
echo.

D:\Python\bin\python.exe server.py
pause
