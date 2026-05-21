# Webhook Automation Setup (Azure DevOps -> NRT Scope)

This document enables automatic refresh of NRT scope when new Azure DevOps data is created (WorkItems, PRs, Commits).

## 1) What is automated

When webhook event is received by:

- `POST /api/webhook/azure-devops`

the backend triggers this pipeline automatically:

1. `01_EXTRACTION/step1_azure_extract.py`
2. `01_EXTRACTION/populate_graph_data.py`
3. `01_EXTRACTION/step3_strict_broker_ingestion_extractor.py` (optional)
4. `02_NEO4J_DATABASE/build_graph_database.py`

Status endpoint:

- `GET /api/webhook/status`

## 2) Required env variables

Set in `.env`:

```env
AZURE_WEBHOOK_TOKEN=your_strong_secret
WEBHOOK_AUTO_REFRESH_ENABLED=true
WEBHOOK_REFRESH_MODE=backfill
WEBHOOK_INCLUDE_STEP3=true
WEBHOOK_AZURE_INSECURE_TLS=true
WEBHOOK_AZURE_DISABLE_ENV_PROXY=true
WEBHOOK_AZURE_MAX_WORKERS=2
WEBHOOK_AZURE_MAX_RETRIES=8
WEBHOOK_AZURE_REQUEST_DELAY_MS=250
NRT_PYTHON_BIN=D:\Python\bin\python.exe
```

Notes:

- `backfill` mode is faster (recommended for near real-time updates).
- `full` mode runs full extraction pipeline.

## 3) Azure DevOps Service Hook

In Azure DevOps project:

1. `Project Settings` -> `Service hooks` -> `Create subscription`
2. Choose events:
   - `Code pushed`
   - `Pull request updated`
   - `Work item updated` (optional but useful)
3. Action: `Web Hooks`
4. URL:
   - `https://<your-public-url>/api/webhook/azure-devops`
5. Method: `POST`
6. Header:
   - `X-NRT-Webhook-Token: <AZURE_WEBHOOK_TOKEN>`

## 4) Connectivity requirement

Azure DevOps must reach your endpoint.

- `localhost` is not reachable from Azure.
- Use a public HTTPS URL (company reverse proxy, VM, tunnel).

## 5) Local validation

### Check webhook status

```powershell
Invoke-WebRequest -UseBasicParsing http://localhost:5000/api/webhook/status | % Content
```

### Simulate a webhook event

```powershell
$body = @{
  eventType = "git.push"
  resource  = @{ repository = @{ name = "mesx-production-api" } }
} | ConvertTo-Json -Depth 5

Invoke-WebRequest -UseBasicParsing `
  -Uri http://localhost:5000/api/webhook/azure-devops `
  -Method POST `
  -ContentType "application/json" `
  -Headers @{ "X-NRT-Webhook-Token" = "<AZURE_WEBHOOK_TOKEN>" } `
  -Body $body | % Content
```

## 6) Azure-only guarantee

The webhook only triggers existing strict extraction pipeline.
Data provenance remains:

- Azure DevOps WorkItem/PR/Commit APIs
- Azure DevOps repo file contents
- Deterministic parser + deterministic line diff

No generated/invented data is injected by webhook.
