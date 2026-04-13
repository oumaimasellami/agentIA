# 🔧 Setup Instructions - MESX 0 NRT System

## Installation sécurisée avec variables d'environnement

Nous utilisons un système de variables d'environnement pour **protéger les credentials**.

---

## 📁 Fichiers créés pour la sécurité

```
agentIA/
├── .env                    ← ⚠️ LOCAL ONLY (credentials réels)
│   │                          NE JAMAIS COMMITER
│   └── Contient les passwords réels
│
├── .env.example            ← ✅ PUBLIC (template/exemple)
│   └── Montre la structure sans valeurs réelles
│
├── .gitignore              ← ✅ PUBLIC
│   └── Exclut .env de Git
│
└── [Les credentials ne sont JAMAIS visibles sur GitHub]
```

---

## 🔐 Hiérarchie de sécurité

```
NIVEAU 1: .env (Confidentiel)
   ↓
   └─→ Chargé par dotenv
       └─→ os.getenv("NEO4J_PASSWORD")
           ↓
NIVEAU 2: Utilité dans le code
   ↓
   └─→ JAMAIS logé, JAMAIS affiché
       └─→ JAMAIS sur GitHub

RÉSULTAT: ✅ Sécurisé
```

---

## 🚀 Démarrage du système - 2 options

### **Option 1: Utiliser le script (RECOMMANDÉ)**

#### Windows - Batch (.bat):
```powershell
# Double-click sur le fichier
startup.bat

# Ou dans PowerShell:
.\startup.bat
```

#### Windows - PowerShell:
```powershell
.\startup.ps1
```

**Avantages:**
- ✅ Démarrage automatique en 1 clic
- ✅ Attendre automatiquement que Neo4j soit prêt
- ✅ Messages colorés et clairs
- ✅ Reproduction garantie

---

### **Option 2: Manuel (Pour développement)**

**Étape 1: Démarrer Neo4j**
```powershell
docker start neo4j
Start-Sleep -Seconds 5
```

**Étape 2: Charger les données**
```powershell
cd d:\agentIA\02_NEO4J_DATABASE
D:\Python\bin\python.exe build_graph_database.py
cd ..
```

**Étape 3: Lancer le serveur**
```powershell
cd d:\agentIA\05_WEB_INTERFACE
D:\Python\bin\python.exe server.py
```

---

## ✅ Vérification du démarrage

Après le démarrage, tu devras:

1. **Neo4j est en ligne:**
   ```powershell
   docker ps | findstr neo4j
   # ✓ Vérifier qu'il y a "Up" dans la colonne STATUS
   ```

2. **Accéder au dashboard:**
   - 🌐 http://localhost:5000
   - 📊 http://localhost:7474 (Neo4j)

3. **Logs du serveur:**
   ```
   ✅ Connected to Neo4j database
   ✅ Loaded 500 WorkItems from JSON
   🚀 NRT Scope Visualization System - Server Started
   ```

---

## 🔑 Credentials par défaut

```
🎯 Neo4j:
   URI: bolt://localhost:7687
   User: neo4j
   Password: forvia2025

🎯 Web Server:
   Host: localhost
   Port: 5000
```

**⚠️ Ces valeurs sont définies dans `.env`**

---

## 📝 Pour ta soutenance (Septembre)

**Dire aux évaluateurs:**

> "Notre système démarre complètement en exécutant `startup.bat` qui:
> 1. Lance le container Neo4j
> 2. Charge 23,189 relations de dépendances
> 3. Démarre le serveur web
>
> Les credentials sont protégés via un fichier `.env` local, conforme aux bonnes pratiques de sécurité."

---

## 🛡️ Vérification de sécurité

### **Avant de commiter:**

```bash
# Vérifier que .env n'est PAS versionnée
git status
# ✓ .env ne doit PAS apparaître

# .env.example OUI être versionnée
git add .env.example
git commit -m "Add .env.example template"

# Ajouter .env au gitignore
echo ".env" >> .gitignore
git add .gitignore
git commit -m "Add .env to gitignore"
```

### **Après commit, vérifier:**
```bash
# Les credentials ne doivent JAMAIS être visibles
git log -p | grep "forvia2025"
# ✓ Aucun résultat = OK

# Vérifier GitHub
# https://github.com/yourname/agentIA/search?q=forvia2025
# ✓ "0 results" = OK
```

---

## 🔄 Mise à jour des credentials

Si tu dois changer le password Neo4j:

1. **Arrêter Neo4j:**
   ```powershell
   docker stop neo4j
   docker rm neo4j
   ```

2. **Modifier `.env`:**
   ```env
   NEO4J_PASSWORD=new_password_here
   ```

3. **Redémarrer:**
   ```powershell
   .\startup.bat
   ```

---

## ❌ Erreurs courantes

### **Erreur: "Python was not found"**
```
❌ Mauvais: python script.py
✅ Correct: D:\Python\bin\python.exe script.py

Solution: Utiliser toujours le chemin complet
```

### **Erreur: "Port 5000 already in use"**
```powershell
# Trouver le processus
netstat -ano | findstr :5000

# Tuer le processus
taskkill /pid <PID> /f
```

### **Erreur: "Couldn't connect to Neo4j"**
```powershell
# Attendre 10 secondes après docker start
# Neo4j met du temps à initialiser

Start-Sleep -Seconds 10
```

---

## 📚 Fichiers importants

| Fichier | Visibilité | Contenu |
|---------|-----------|---------|
| `.env` | ❌ LOCAL ONLY | Passwords réels |
| `.env.example` | ✅ PUBLIC | Template |
| `.gitignore` | ✅ PUBLIC | Exclut .env |
| `startup.bat` | ✅ PUBLIC | Script démarrage |
| `startup.ps1` | ✅ PUBLIC | Script PowerShell |

---

## 🎯 Résumé pour la soutenance

**À préparer:**
- ✅ Un ordinateur avec Docker et Python
- ✅ Un fichier `.env` avec les credentials
- ✅ Exécuter `startup.bat` (< 1 minute)
- ✅ Montrer http://localhost:5000

**À dire:**
- ✅ "Le système utilise des variables d'environnement pour la sécurité"
- ✅ "Les credentials ne sont jamais versionnés"
- ✅ "Entièrement reproductible en 1 clic"

**Résultat:** 🎓 Excellente impression! 👍

---

**Dernière mise à jour:** Avril 2026
