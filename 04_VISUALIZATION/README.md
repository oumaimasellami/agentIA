# 🎨 ÉTAPE 4 : VISUALISATION DU GRAPHE

## 🎯 Objectif
Visualiser le graphe complet (3,561 nœuds + 3,407 relations) de manière interactive dans Neo4j Browser.

---

## 📂 Fichiers

### **REQUETE_GRAPHE_COMPLET.txt**
Contient les meilleures requêtes Cypher prêtes à copier-coller.

---

## 🌐 Accès Neo4j Browser

**URL:** http://localhost:7474
**Credentials:** neo4j / forvia2025

---

## 🔥 REQUÊTE PRINCIPALE (⭐ LA MEILLEURE)

**Copie-colle cette requête pour voir le graphe COMPLET :**

```cypher
MATCH (m:Microservice)
OPTIONAL MATCH (m)-[api:API_CALLS]->(target:Microservice)
OPTIONAL MATCH (m)-[impl:IMPLEMENTS]->(func:Function)
OPTIONAL MATCH (func)-[cov:COVERS]->(uc:UseCase)
OPTIONAL MATCH (c:Commit)-[mod:MODIFIES]->(m)
RETURN m, api, target, impl, func, cov, uc, mod, c
```

**Résultat:** Graphe hiérarchique complet montrant :
- 81 Microservices (nœuds rouges)
- 178 Relations API_CALLS (flèches inter-services)
- 193 Fonctions (nœuds bleus)
- 193 Relations IMPLEMENTS
- 310 Use Cases (nœuds cyan)
- 59 Relations COVERS
- 2,977 Commits (nœuds oranges)
- 2,977 Relations MODIFIES

---

## 🎨 Autres Requêtes Utiles

### 1. Services critiques (Plus d'appels)
```cypher
MATCH (a:Microservice)-[r:API_CALLS]->(b:Microservice)
RETURN a.name, count(r) as appels_sortants
ORDER BY appels_sortants DESC
LIMIT 20
```

**Montre:** Services qui appellent le plus d'autres services

---

### 2. Services dépendants
```cypher
MATCH (a:Microservice)-[r:API_CALLS]->(b:Microservice)
RETURN b.name, count(r) as appels_entrants
ORDER BY appels_entrants DESC
LIMIT 20
```

**Montre:** Services appelés par le plus de services (critiques!)

---

### 3. Chaînes complètes Commit → Use Case
```cypher
MATCH (c:Commit)-[:MODIFIES]->(m:Microservice)
    -[:IMPLEMENTS]->(f:Function)
    -[:COVERS]->(u:UseCase)
RETURN c.commit_id, m.name, f.nom, u.titre
LIMIT 50
```

**Montre:** Impact complet d'un commit

---

### 4. Graphe avec tous les chemins
```cypher
MATCH path = (n)-[*..3]-(m)
RETURN path
LIMIT 500
```

**Montre:** Tous les chemins jusqu'à 3 profondeurs

---

### 5. Approfondir un microservice
```cypher
MATCH (m:Microservice {name: "mesx-admin-backend"})
OPTIONAL MATCH (m)-[:API_CALLS]->(target)
OPTIONAL MATCH (source)-[:API_CALLS]->(m)
OPTIONAL MATCH (m)-[:IMPLEMENTS]->(f:Function)
RETURN m, target, source, f
```

**Remplace le nom du service pour explorer!**

---

## 🖱️ Navigation dans Neo4j Browser

### Basics
- **Zoom:** Scroll de la souris
- **Pan:** Clique-glisse
- **Sélectionner un nœud:** Clique dessus
- **Info nœud:** Double-clique pour voir propriétés

### Affichage
- Clique sur **"Graph"** (à droite) = mode graphique
- Clique sur **"Table"** = mode tableau
- Clique sur **"Text"** = mode texte

### Contrôles
- **⚙️ Settings** (haut droite)
  - Augmente "Initial Node Display" à 3600
  - Configure les couleurs par type

---

## 📊 Statistiques du Graphe

### Total des nœuds
```cypher
MATCH (n) RETURN count(n) as total
```
**Résultat: 3,561**

### Total des relations
```cypher
MATCH ()-[r]->() RETURN count(r) as total
```
**Résultat: 3,407**

### Densité
```cypher
WITH 3407.0 / 3561 as densité
RETURN round(densité, 2) as densité_graph
```
**Résultat: 0.96** (relations par nœud)

### Répartition par type
```cypher
MATCH (n)
RETURN labels(n)[0] as Type, count(n) as Count
ORDER BY Count DESC
```

**Résultat:**
```
Commit        | 2,977
Microservice  |    81
UseCase       |   310
Function      |   193
```

---

## 🎯 Cas d'Usage : Explorer un Impact

### Scénario: Un commit modifie mesx-infrastructure

1. **Trouve le commit:**
   ```cypher
   MATCH (c:Commit {microservice: "mesx-infrastructure"})
   RETURN c.commit_id, c.message, c.date
   LIMIT 5
   ```

2. **Vois son impact complet:**
   ```cypher
   MATCH (c:Commit {commit_id: "abc123"})-[:MODIFIES]->(m)
   OPTIONAL MATCH (m)-[:API_CALLS]->(target)
   OPTIONAL MATCH (m)-[:IMPLEMENTS]->(f:Function)-[:COVERS]->(u:UseCase)
   RETURN c, m, target, f, u
   ```

3. **Compter les affectés:**
   ```cypher
   MATCH (c:Commit {commit_id: "abc123"})-[:MODIFIES]->(m)
           -[:IMPLEMENTS]->(f:Function)-[:COVERS]->(u:UseCase)
   RETURN count(DISTINCT u) as use_cases_affectés
   ```

---

## 🖼️ Interprétation Visuelle

### Couleurs dans le graphe
- 🔴 **Rouge** = Microservices
- 🔵 **Bleu** = Fonctions
- 🔷 **Cyan** = Use Cases
- 🟠 **Orange** = Commits

### Flèches
- **Rouge→Rouge** = API_CALLS
- **Rouge→Bleu** = IMPLEMENTS
- **Bleu→Cyan** = COVERS
- **Orange→Rouge** = MODIFIES

### Clusters
- **Cluster dense** = Services fortement interconnectés
- **Nœud isolé 🟠** = Commit qui n'affecte peut-être rien?
- **Hub central** = Service très appelé (critique!)

---

## ⚙️ Tips & Tricks

### Requête lente?
```cypher
-- Ajoute une limite
LIMIT 500
```

### Trop de nœuds affichés?
- Retouche "Initial Node Display" dans Settings
- Ou ajoute des LIMIT aux requêtes

### Veux exporter les données?
```cypher
-- En JSON-compatible
MATCH (n)-[r]->(m)
RETURN {node: n, relation: r, target: m} as result
```

### Veux voir des statistiques?
```cypher
MATCH (n)
WITH labels(n)[0] as type, count(n) as count
RETURN type, count
ORDER BY count DESC
```

---

## 📋 Checklist Visualisation

- [ ] Neo4j Browser accessible (http://localhost:7474)
- [ ] Login possible
- [ ] Requête principale retourne résultats
- [ ] Mode "Graph" affiche le graphe
- [ ] Au moins 1 nœud visible
- [ ] Zooming et panning fonctionnent

---

## 🎬 Démo Interactive Suggérée

### Pour la présentation (5-8 min):

1. **Lancer requête complète** (2 min)
   → Montre le graphe en entier

2. **Zoom sur un cluster** (1 min)
   → Montre les relations complexes

3. **Cliquer sur un nœud** (1 min)
   → Affiche ses propriétés

4. **Tester un service critique** (1 min)
   → `mesx-master-api` par exemple

5. **Montrer stats** (1 min)
   → "3,561 nœuds, 3,407 relations"

---

## 🔗 Documentation Neo4j

- **Neo4j Browser Guide:** https://neo4j.com/docs/browser-manual/
- **Cypher Syntax:** https://neo4j.com/docs/cypher-manual/current/
- **Best Practices:** https://neo4j.com/developer/guide-data-modeling/

---

**Retour au guide complet:** [README_PRESENTATION.md](../_PRESENTATION/README_PRESENTATION.md)
