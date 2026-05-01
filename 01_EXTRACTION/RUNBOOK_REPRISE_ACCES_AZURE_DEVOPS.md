# Runbook Reprise Acces Azure DevOps (MES_X.0)

## Objectif
Relancer proprement l'etape WorkItem -> Commit des que les droits Azure DevOps sont retablis, puis verifier l'impact sur la qualite de la base.

## Prerequis
- Acces confirme au projet MES_X.0.
- PAT avec scopes minimum:
  - Work Items (Read)
  - Code (Read)
  - Pull Requests (Read)
- Si environnement entreprise avec proxy/certificat interne:
  - soit certificat CA interne disponible
  - soit mode debug temporaire `--insecure`

## Etape 1 - Test rapide access (50 WI)
Depuis `D:\agentIA\01_EXTRACTION`:

```powershell
D:\Python\bin\python.exe .\step1b_extract_workitem_commit_links.py --max-workitems 50
```

Si certificat TLS bloque:

```powershell
D:\Python\bin\python.exe .\step1b_extract_workitem_commit_links.py --max-workitems 50 --insecure
```

## Etape 2 - Criteres de succes du test rapide
Dans `workitem_commit_links.json`:
- `metadata.stats.ado_errors` doit etre proche de 0.
- `metadata.stats.with_ado_links` doit etre > 0.
- `metadata.stats.with_any_links` doit etre > 0.

Si ces criteres ne sont pas atteints:
- verifier droits Work Item au niveau projet
- verifier si certains WI sont dans d'autres Area Path securises

## Etape 3 - Extraction complete
Quand le test rapide est OK:

```powershell
D:\Python\bin\python.exe .\step1b_extract_workitem_commit_links.py
```

Sorties attendues:
- `D:\agentIA\workitem_commit_links.json`
- `D:\agentIA\work_items_enriched.json`

## Etape 4 - Rebuild et enrichissement graphe
Depuis `D:\agentIA`:

```powershell
D:\Python\bin\python.exe .\02_NEO4J_DATABASE\build_graph_database.py
D:\Python\bin\python.exe .\02_NEO4J_DATABASE\enrich_commit_workitem_function_relations.py
```

## Etape 5 - Verification qualite (obligatoire)

```powershell
D:\Python\bin\python.exe .\validate_nrt_userstory_consistency.py
D:\Python\bin\python.exe .\04_VALIDATION\nrt_quality_governance.py
D:\Python\bin\python.exe .\04_VALIDATION\neo4j_kb_health_check.py
```

## Etape 6 - Delta avant/apres
Comparer:
- `nrt_quality_governance_report.json`
- `neo4j_kb_health_report.json`
- `nrt_userstory_consistency_report.json`

Objectifs:
- `matches = 100%` (consistency)
- baisse de `zero_direct_rate`
- baisse de `zero_functions_rate`
- maintien de l'integrite structurelle

## Plan de secours
Si ADO reste inaccessible:
- continuer en mode `--offline` pour valider seulement le pipeline technique
- ne pas tirer de conclusion metier sur la completude tant que `with_ado_links = 0`
