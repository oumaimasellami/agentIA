# Backend Traceability Guide (Meeting)

## 1) What The API Does
Endpoint used by UI:
- `/api/nrt-scope?workitem_id=<ID>`

Main backend orchestration:
- [get_nrt_scope](05_WEB_INTERFACE/server.py#L525)

Output returned to UI:
- `direct_microservices`
- `indirect_microservices`
- `summary` (counts)

## 2) Direct Scope Logic (Exact Order)
The backend computes direct scope in this order:

1. Base direct path (strong evidence):
- [find_direct_microservices_with_functions](05_WEB_INTERFACE/server.py#L853)
- Cypher path:
  - `WorkItem <- COVERS - Function <- IMPLEMENTS - Microservice`

2. Commit evidence enrichment (additive):
- [enrich_direct_microservices_with_commit_evidence](05_WEB_INTERFACE/server.py#L792)
- Cypher path:
  - `WorkItem - RELATES_TO_COMMIT -> Commit - TOUCHES_FUNCTION -> Function <- IMPLEMENTS - Microservice`
- Goal:
  - reduce false negatives by adding touched functions.

3. Fallback A (when no direct scope found):
- [find_direct_microservices_by_commit_message_reference](05_WEB_INTERFACE/server.py#L621)
- Source:
  - commit message references like `AB#<WI>`, `WI <ID>`, `Work Item <ID>`.

4. Fallback B for Bugs (when still empty):
- [find_direct_microservices_by_workitem_text_signature](05_WEB_INTERFACE/server.py#L710)
- Source:
  - keywords from Bug title/description/tags/area path
  - matched against function names and commit messages.

## 3) Indirect Scope Logic
After direct scope is built, backend computes dependencies:
- [find_indirect_microservices_with_functions](05_WEB_INTERFACE/server.py#L890)
- Dependency edges:
  - `API_CALLS`
  - `MESSAGE_BROKER`
  - `INGESTION`

Then functions on indirect services are collected via:
- `Microservice - IMPLEMENTS -> Function`

## 4) How WorkItem <-> Commit Link Is Built Today
There are two mechanisms currently in the project:

A) Graph enrichment (already used now):
- [enrich_commit_workitem_function_relations.py](02_NEO4J_DATABASE/enrich_commit_workitem_function_relations.py#L20)
- Builds:
  - `Commit - TOUCHES_FUNCTION -> Function` from `MODIFIES + IMPLEMENTS`
  - `WorkItem - RELATES_TO_COMMIT -> Commit` from overlap with `COVERS`

B) Azure DevOps extraction step (new, incremental):
- [step1b_extract_workitem_commit_links.py](01_EXTRACTION/step1b_extract_workitem_commit_links.py#L164)
- Designed to read native ADO WorkItem relations + fallback commit messages.
- Current blocker observed: ADO permission `403` on WorkItem relation endpoint.

## 5) Evidence Levels (Important For Reviewers)
Use this wording in meeting:

1. Strong evidence:
- `COVERS` path
- `RELATES_TO_COMMIT + TOUCHES_FUNCTION` path

2. Medium evidence:
- commit message reference fallback

3. Heuristic evidence:
- Bug text signature fallback

This makes the system transparent: when strong links are missing, fallback is explicit and visible (`summary.fallback_source`).

## 6) Why Bugs Were Empty Before
Many Bugs had no `COVERS` links and no explicit commit reference in graph.
So old logic returned zero scope.
Now fallback logic ensures Bugs also get actionable scope.

## 7) Live Demo Steps For Meeting
1. Start app:
- `powershell -ExecutionPolicy Bypass -File .\start_project.ps1`

2. Open:
- `http://localhost:5000/workitem-scope`

3. Show one User Story and one Bug:
- User Story example: `127835`
- Bug example: `119053`

4. Explain source of result using `summary.fallback_source`:
- `null` = strong links found directly
- `commit_message_reference` or `workitem_text_signature` = fallback used

## 8) Key Message To Defend
- Engine is deterministic and validated against Neo4j parity.
- Remaining risk is traceability completeness in source links, not engine correctness.
- Fallback strategy is explicit, controlled, and auditable.
