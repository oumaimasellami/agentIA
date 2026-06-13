$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonBin = "D:\Python\bin\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "nrt_refresh.log"
$ServerScript = Join-Path $ProjectRoot "05_WEB_INTERFACE\server.py"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

function Append-LogLine {
    param([string]$Line)

    $attempts = 6
    for ($i = 0; $i -lt $attempts; $i++) {
        try {
            $fileStream = [System.IO.File]::Open($LogFile, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
            try {
                $writer = New-Object System.IO.StreamWriter($fileStream, [System.Text.UTF8Encoding]::new($false))
                $writer.WriteLine($Line)
                $writer.Flush()
            }
            finally {
                if ($writer) { $writer.Dispose() }
                $fileStream.Dispose()
            }
            return
        }
        catch {
            if ($i -eq ($attempts - 1)) {
                throw
            }
            Start-Sleep -Milliseconds 400
        }
    }
}

function Write-Log {
    param(
        [string]$Message,
        [string]$Level = "INFO"
    )

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] [$Level] $Message"
    Write-Host $line
    Append-LogLine -Line $line
}

function Load-DotEnv {
    param([string]$EnvPath)

    if (-not (Test-Path $EnvPath)) {
        throw ".env file not found: $EnvPath"
    }

    Get-Content $EnvPath | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }

        $parts = $line.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1]
        [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

function Invoke-Step {
    param(
        [string]$Label,
        [string]$ScriptPath
    )

    Write-Log "Starting $Label"
    Push-Location $ProjectRoot
    try {
        & $PythonBin $ScriptPath 2>&1 | Tee-Object -FilePath $LogFile -Append
        if ($LASTEXITCODE -ne 0) {
            throw "$Label failed with exit code $LASTEXITCODE"
        }
        Write-Log "Completed $Label"
    }
    finally {
        Pop-Location
    }
}

function Restart-NrtServerIfEnabled {
    $restartFlag = [System.Environment]::GetEnvironmentVariable("NRT_RESTART_SERVER_AFTER_REFRESH", "Process")
    if (($restartFlag -as [string]).ToLowerInvariant() -notin @("1", "true", "yes", "on")) {
        Write-Log "Server restart skipped (NRT_RESTART_SERVER_AFTER_REFRESH is disabled)"
        return
    }

    Write-Log "Restarting NRT server to reload in-memory indexes"

    $listener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        try {
            Stop-Process -Id $listener.OwningProcess -Force
            Start-Sleep -Seconds 2
            Write-Log "Stopped existing server process on port 5000 (PID $($listener.OwningProcess))"
        }
        catch {
            Write-Log "Failed to stop existing server process: $($_.Exception.Message)" "ERROR"
            throw
        }
    }

    Push-Location $ProjectRoot
    try {
        Start-Process -FilePath $PythonBin -ArgumentList ".\05_WEB_INTERFACE\server.py" -WorkingDirectory $ProjectRoot -WindowStyle Hidden
        Write-Log "NRT server restarted in background"
    }
    finally {
        Pop-Location
    }
}

try {
    Write-Log "============================================================"
    Write-Log "NRT refresh pipeline started"

    Load-DotEnv -EnvPath (Join-Path $ProjectRoot ".env")

    [System.Environment]::SetEnvironmentVariable("AZURE_INSECURE_TLS", "true", "Process")
    [System.Environment]::SetEnvironmentVariable("AZURE_DISABLE_ENV_PROXY", "true", "Process")
    if (-not [System.Environment]::GetEnvironmentVariable("AZURE_EXTRACT_COMMIT_DIFF_LINES", "Process")) {
        [System.Environment]::SetEnvironmentVariable("AZURE_EXTRACT_COMMIT_DIFF_LINES", "true", "Process")
    }
    if (-not [System.Environment]::GetEnvironmentVariable("AZURE_COMMIT_DIFF_MAX_FILES", "Process")) {
        [System.Environment]::SetEnvironmentVariable("AZURE_COMMIT_DIFF_MAX_FILES", "20", "Process")
    }
    if (-not [System.Environment]::GetEnvironmentVariable("AZURE_COMMIT_DIFF_MAX_LINES_PER_FILE", "Process")) {
        [System.Environment]::SetEnvironmentVariable("AZURE_COMMIT_DIFF_MAX_LINES_PER_FILE", "500", "Process")
    }

    Invoke-Step -Label "Azure DevOps extraction" -ScriptPath ".\01_EXTRACTION\step1_azure_extract.py"
    Invoke-Step -Label "Graph data population" -ScriptPath ".\01_EXTRACTION\populate_graph_data.py"
    Invoke-Step -Label "Repository inventory extraction" -ScriptPath ".\01_EXTRACTION\step1d_extract_repo_inventory.py"
    Invoke-Step -Label "Function code index extraction" -ScriptPath ".\01_EXTRACTION\step1e_extract_function_code_index.py"
    Invoke-Step -Label "Strict broker ingestion extraction" -ScriptPath ".\01_EXTRACTION\step3_strict_broker_ingestion_extractor.py"
    Invoke-Step -Label "Neo4j graph rebuild" -ScriptPath ".\02_NEO4J_DATABASE\build_graph_database.py"
    Restart-NrtServerIfEnabled

    Write-Log "NRT refresh pipeline finished successfully"
    Write-Log "============================================================"
    exit 0
}
catch {
    Write-Log $_.Exception.Message "ERROR"
    Write-Log "NRT refresh pipeline failed" "ERROR"
    Write-Log "============================================================" "ERROR"
    exit 1
}
