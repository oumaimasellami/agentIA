# Quick Start Guide - MESX 0 NRT System

## 🚀 Démarrage rapide (5 minutes)

### **1️⃣ Démarrer Neo4j**
```powershell
docker start neo4j
Start-Sleep -Seconds 5
```

### **2️⃣ Charger les données**
```powershell
cd d:\agentIA\02_NEO4J_DATABASE
D:\Python\bin\python.exe build_graph_database.py
```

### **3️⃣ Lancer le serveur**
```powershell
cd d:\agentIA\05_WEB_INTERFACE
D:\Python\bin\python.exe server.py
```

### **4️⃣ Ouvrir le navigateur**
- 🌐 Dashboard: http://localhost:5000
- 📊 Neo4j: http://localhost:7474

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

**Réussi!** 🎉
