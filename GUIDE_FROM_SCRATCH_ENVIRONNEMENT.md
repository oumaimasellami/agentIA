# Guide From Scratch - Environnement agentIA

## Objectif
Ce guide te permet de revisiter rapidement tout le projet: quelles donnees sont chargees, quels scripts construisent Neo4j, comment le scope NRT est calcule, et quels fichiers sont utilises en validation.

## 1) Point d'entree (lire en premier)
- README global: README.md
- Demarrage rapide: QUICK_START.md
- Setup securite: SETUP_INSTRUCTIONS.md
- Variables d'environnement: .env.example

## 2) Donnees sources (racine)
Ces fichiers representent les repositories MESX extraits et analyses.
- Pattern principal: source_mesx_*\.json
- UI (screens): source_mesx_*ui*.json
- WorkItems: work_items.json

## 3) Extraction et preparation (01_EXTRACTION)
Dossier: 01_EXTRACTION/
- Fusion/constitution graphe: graph_data.json
- Relations REST: appels_api.json
- Relations broker/ingestion: broker_ingestion_relations.json
- Relations COVERS brutes/enrichies: covers.json, covers_enhanced.json
- Scripts extraction pipeline:
  - step1_azure_extract.py
  - step2_parser.py
  - step3_broker_ingestion_extractor.py
  - step3_enriched_broker_ingestion_extractor.py

## 4) Construction Neo4j (02_NEO4J_DATABASE)
Dossier: 02_NEO4J_DATABASE/
- Script principal de build: build_graph_database.py
- Verification/diagnostic:
  - verify_graph_complete.py
  - verify_relations.py
  - verify_covers.py
  - check_database.py
  - test_covers.py

Ce build cree les noeuds/relations principales:
- Noeuds: WorkItem, Function, Microservice, Commit
- Relations: COVERS, IMPLEMENTS, MODIFIES, API_CALLS, MESSAGE_BROKER, INGESTION

## 5) Moteur Symbolic AI (03_SYMBOLIC_AI_ENGINE)
Dossier: 03_SYMBOLIC_AI_ENGINE/
- Moteur principal: symbolic_ai_engine.py
- Normalisation d'exemple: normalize_and_display_scope.py

## 6) Validation (04_VALIDATION)
Dossier: 04_VALIDATION/
- Validation scope NRT: validation_nrt_scope.py
- Normalisation microservices: normalize_microservices.py
- Rapports: normalization_report.json, validation_report_127835.json

## 7) Visualisation technique (04_VISUALIZATION)
Dossier: 04_VISUALIZATION/
- Visualisations HTML/graphes: architecture_visualization.html, graphe_complet.html
- Script graphe: graphe_complet.py
- Jeux de donnees visualisation: microservices.json, fonctions.json, covers.json, commits.json

## 8) Interface Web et API (05_WEB_INTERFACE)
Dossier: 05_WEB_INTERFACE/
- Backend principal: server.py
- App alternative: app.py
- Requetes de travail: REQUETEE
- Templates UI:
  - templates/dashboard.html
  - templates/commit_analysis.html
  - templates/workitem_scope.html
  - templates/graph_visualization.html
  - templates/reports.html

## 9) Fichiers de controle recents
- Rapport coherence User Story: nrt_userstory_consistency_report.json
- Script de verification globale API vs Neo4j: validate_nrt_userstory_consistency.py

## 10) Ordre de lecture conseille (from scratch)
1. QUICK_START.md
2. SETUP_INSTRUCTIONS.md
3. 02_NEO4J_DATABASE/build_graph_database.py
4. 05_WEB_INTERFACE/server.py
5. 05_WEB_INTERFACE/templates/workitem_scope.html
6. 03_SYMBOLIC_AI_ENGINE/symbolic_ai_engine.py
7. validate_nrt_userstory_consistency.py
8. nrt_userstory_consistency_report.json

## 11) Ce que tu dois verifier pour comprendre le scope
- Direct MS: WorkItem <- COVERS <- Function <- IMPLEMENTS <- Microservice
- Indirect MS: depuis Direct via API_CALLS/MESSAGE_BROKER/INGESTION
- Total functions: deduplication globale (distinct) pour coherence API/Neo4j
- Screens: extraits depuis les fichiers UI source_mesx_*ui*.json (pas depuis Neo4j)

## 12) Commandes utiles
- Build Neo4j:
  D:\Python\bin\python.exe 02_NEO4J_DATABASE\build_graph_database.py
- Start server:
  D:\Python\bin\python.exe 05_WEB_INTERFACE\server.py
- Check API:
  http://localhost:5000/api/statistics
  http://localhost:5000/api/nrt-scope/149015
