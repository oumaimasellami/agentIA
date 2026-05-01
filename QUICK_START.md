# Quick Start Guide - MESX 0 NRT System

## Démarrage stable quotidien (recommandé)

Utilise le script stable qui ne supprime ni conteneur ni image:

```powershell
cd d:\agentIA
powershell -ExecutionPolicy Bypass -File .\start_project.ps1
```

Ce script:
- démarre le conteneur `neo4j` s'il existe,
- attend la disponibilité Bolt,
- lance `server.py`.

## Démarrage manuel (alternative)

### 1) Démarrer Neo4j
```powershell
docker start neo4j
```

### 2) Vérifier la connectivité Neo4j
```powershell
D:\Python\bin\python.exe -c "from neo4j import GraphDatabase; d=GraphDatabase.driver('bolt://localhost:7687',auth=('neo4j','forvia2025')); d.verify_connectivity(); print('NEO4J_OK'); d.close()"
```

### 3) Lancer le serveur
```powershell
cd d:\agentIA
D:\Python\bin\python.exe .\05_WEB_INTERFACE\server.py
```

### 4) Ouvrir le navigateur
- Dashboard: http://localhost:5000
- Neo4j Browser: http://localhost:7474

---

## 📋 Identifiants Neo4j

- **User:** `neo4j`
- **Password:** `forvia2025`

---

## 🔗 URLs principales

| Service | URL | Description |
|---------|-----|-------------|
| Dashboard | http://localhost:5000 | Accueil principal |
| Workitem Scope | http://localhost:5000/workitem-scope | Analyse des dépendances |
| Neo4j Browser | http://localhost:7474 | Requêtes Cypher |
| API Stats | http://localhost:5000/api/statistics | Statistiques JSON |

---

## ✅ Vérification

```powershell
# Vérifier que Neo4j tourne
docker ps | findstr neo4j

# Vérifier que le serveur répond
Invoke-WebRequest http://localhost:5000/api/statistics
```

Réussi.
