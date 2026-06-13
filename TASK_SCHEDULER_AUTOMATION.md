# Task Scheduler Automation

This setup lets Windows run the full NRT refresh pipeline automatically without launching each extraction command manually.

## What the scheduled task runs

The scheduled task launches:

- `run_nrt_refresh.ps1`

That script runs this pipeline in order:

1. `01_EXTRACTION/step1_azure_extract.py`
2. `01_EXTRACTION/populate_graph_data.py`
3. `01_EXTRACTION/step1d_extract_repo_inventory.py`
4. `01_EXTRACTION/step1e_extract_function_code_index.py`
5. `01_EXTRACTION/step3_strict_broker_ingestion_extractor.py`
6. `02_NEO4J_DATABASE/build_graph_database.py`

## Why this is useful

- automatic refresh of WorkItems, PRs, commits, files, functions, and graph
- no need to rerun each script manually
- stays aligned with the current project logic
- keeps functional scope data fresh too because repository inventory and function code index are rebuilt

## Current server behavior

Your current `server.py` loads several indexes at startup:

- commits
- pull requests
- workitem-dev links
- function code index
- repo inventory index

So:

- Neo4j is updated by the refresh pipeline
- JSON files are updated too
- but if the web server is already running, it may still keep old in-memory indexes

If you want automatic reload too, add this in `.env`:

```env
NRT_RESTART_SERVER_AFTER_REFRESH=true
```

Then the refresh script will restart `server.py` automatically after the rebuild.

## Manual execution

Run from an Administrator PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File "D:\agentIA\run_nrt_refresh.ps1"
```

## Log file

Pipeline logs are written to:

```text
D:\agentIA\logs\nrt_refresh.log
```

## Suggested Task Scheduler command

Example: run every 2 hours.

```powershell
schtasks /Create /TN "FORVIA_NRT_Refresh" /SC HOURLY /MO 2 /RL HIGHEST /RU "%USERNAME%" /TR "powershell.exe -ExecutionPolicy Bypass -File \"D:\agentIA\run_nrt_refresh.ps1\"" /F
```

## Example task update

If you want to change the schedule later:

```powershell
schtasks /Change /TN "FORVIA_NRT_Refresh" /RI 120
```

## Example task launch

```powershell
schtasks /Run /TN "FORVIA_NRT_Refresh"
```

## Example task check

```powershell
schtasks /Query /TN "FORVIA_NRT_Refresh" /V /FO LIST
```

## Notes

- The machine must be on and connected to the company network.
- `.env` must contain valid Azure DevOps and Neo4j credentials.
- This is a scheduled full refresh approach. It is simpler than Service Hooks and does not require exposing a webhook endpoint.
- If you keep the web app open for users, enabling `NRT_RESTART_SERVER_AFTER_REFRESH=true` is recommended.
