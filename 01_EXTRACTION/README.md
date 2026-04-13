# 📥 ÉTAPE 1 : EXTRACTION DES DONNÉES

## 📌 Objectif
Extraire les données de l'architecture MESX.0 depuis Azure DevOps et les structurer en JSON pour le graphe.

---

## 📂 Fichiers

### **step2_parser.py** (Script principal)
- **Entrée:** Données Azure DevOps (commits, métadonnées)
- **Sortie:** `graph_data.json` (7.2 MB)
- **Rôle:** Parser les données brutes et les structurer

**À faire:**
```bash
python step2_parser.py
```

---

## 📊 Sortie : graph_data.json

Structure de données complète :

```json
{
  "microservices": [
    {
      "name": "mesx-infrastructure",
      "repo_url": "...",
      "project": "MESX.0",
      "default_branch": "main",
      "id": "repo-123",
      "size": 450
    },
    ...  // 81 microservices
  ],
  
  "fonctions": [
    {
      "id": "func-001",
      "nom": "ApiOperation",
      "classe": "Controller",
      "package": "com.forvia",
      "type": "REST",
      "http_method": "GET",
      "path": "/api/v1/operations",
      "microservice": "mesx-admin-backend",
      "fichier": "AdminController.java"
    },
    ...  // 193 fonctions
  ],
  
  "appels_api": [
    {
      "source": "mesx-admin-backend",
      "cible": "mesx-infrastructure",
      "via": "REST",
      "detail": "GET /health",
      "communication_type": "HTTP",
      "confidence": 0.95
    },
    ...  // 178 appels API
  ],
  
  "use_cases": [
    {
      "id": "uc-001",
      "work_item_id": "123456",
      "titre": "Admin Dashboard Load",
      "description": "...",
      "type": "Feature",
      "statut": "Active",
      "priorite": "High",
      "tags": ["admin", "dashboard"],
      "area_path": "MESX\\Admin"
    },
    ...  // 310 use cases
  ],
  
  "relations_covers": [
    {
      "source": "func-001",
      "cible": "uc-001",
      "score": 0.85
    },
    ...  // 59 relations
  ],
  
  "commits": [
    {
      "commit_id": "abc123def456",
      "auteur": "John Doe",
      "email": "john@example.com",
      "date": "2024-03-20T10:15:00Z",
      "message": "Fix admin backend API",
      "microservice": "mesx-admin-backend"
    },
    ...  // 2,977 commits
  ],
  
  "pull_requests": [
    {
      "pr_id": "pr-001",
      "titre": "Feature admin dashboard",
      "auteur": "Jane Smith",
      "statut": "merged",
      "date_creation": "2024-03-15",
      "source_branch": "feature/admin-ui",
      "target_branch": "main",
      "microservice": "mesx-admin-ui"
    },
    ...
  ]
}
```

---

## 📈 Statistiques

| Type | Nombre |
|------|--------|
| Microservices | 81 |
| Fonctions | 193 |
| Use Cases | 310 |
| Appels API | 178 |
| Relations Function→UC | 59 |
| Commits | 2,977 |
| Pull Requests | ~500 |
| **TOTAL DONNÉES** | **~4,300 éléments** |

---

## ✅ Vérification

Après extraction, vérifier :

```bash
# Afficher les stats
python3 -c "
import json
with open('graph_data.json') as f:
    data = json.load(f)
    print(f'Microservices: {len(data.get(\"microservices\", []))}')
    print(f'Fonctions: {len(data.get(\"fonctions\", []))}')
    print(f'Use Cases: {len(data.get(\"use_cases\", []))}')
    print(f'Appels API: {len(data.get(\"appels_api\", []))}')
    print(f'Commits: {len(data.get(\"commits\", []))}')
"
```

✅ Vérifications essentielles :
- [ ] `graph_data.json` existe (7.2 MB)
- [ ] Tous les microservices sont listés
- [ ] Tous les commits ont un commit_id
- [ ] Les appels API ont source + cible

---

## 📋 Notes

- Les données sont **réelles** (extraites d'Azure DevOps production)
- Les commit_ids et messages sont **vrais**
- Les dépendances API reflètent l'**architecture réelle** de MESX.0
- Les use cases proviennent de **Azure DevOps Work Items**

**Prochaine étape:** [02_NEO4J_DATABASE](../02_NEO4J_DATABASE/README.md)
