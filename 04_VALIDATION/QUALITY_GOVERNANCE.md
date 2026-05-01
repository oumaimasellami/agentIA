# NRT Quality Governance - Industrialisation

## Objectif
Definir une gouvernance qualite continue pour garantir la fiabilite du scope NRT dans le temps.

## Artefacts
- Script quality gate: 04_VALIDATION/nrt_quality_governance.py
- Rapport genere: nrt_quality_governance_report.json

## Regles (Quality Gates)
1. Consistency Gate
- Definition: coherence API scope vs requete Neo4j equivalente.
- Mesure: consistency_rate.

2. Orphan WorkItem Gate
- Definition: ratio de WorkItems sans relation COVERS.
- Mesure: orphan_workitem_rate.

3. Zero-Direct Gate
- Definition: ratio de User Stories avec 0 microservice direct.
- Mesure: zero_direct_rate.

4. Zero-Functions Gate
- Definition: ratio de User Stories avec 0 fonction totale.
- Mesure: zero_functions_rate.

5. Screen Source Gate
- Definition: ratio de User Stories avec screen_source=confirmed_ui_routes_only.
- Mesure: confirmed_screen_source_rate.

6. Scope Size Gates
- Definition: P95 de la taille scope pour eviter les derivees.
- Mesures: p95_total_ms et p95_total_functions.

## Seuils par defaut
- consistency_rate >= 0.99
- orphan_workitem_rate <= 0.55
- zero_direct_rate <= 0.40
- zero_functions_rate <= 0.40
- confirmed_screen_source_rate >= 0.95
- p95_total_ms <= 25
- p95_total_functions <= 80

## Execution
Depuis la racine du repo:

D:\Python\bin\python.exe 04_VALIDATION\nrt_quality_governance.py

## Audit periodique recommande
- Quotidien: execution quality gate + archivage rapport JSON
- Hebdomadaire: revue des top mismatches / zero_direct
- Mensuel: ajustement seuils selon evolution du perimetre

## Decision industrielle
- PASS global: autoriser publication/release du scope
- FAIL global: ouvrir actions correctives avant validation finale
