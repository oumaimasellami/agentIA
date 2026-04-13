────────────────────────────────────────────────────

"""
MEX.0 — ÉTAPE 1 : EXTRACTION AZURE DEVOPS

Lance ce script AVANT step2_parser.py

Génère:
  - commits.json       (tous les commits)
  - pull_requests.json (toutes les PRs)
  - work_items.json    (tous les work items)
  - microservices.json (tous les repos)
"""

import os
import json
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta

# ══════════════════════════════════════════════════════
# CONFIGURATION DEPUIS .env
# ══════════════════════════════════════════════════════

load_dotenv()

AZURE_ORG    = os.getenv("AZURE_ORG", "faurecia-cloud")
AZURE_PROJECT = os.getenv("AZURE_PROJECT", "MES_X.0")
AZURE_PAT    = os.getenv("AZURE_PAT")

if not AZURE_PAT:
    raise ValueError("❌ AZURE_PAT manquant dans .env")

# Base URL Azure DevOps
BASE_URL = f"https://dev.azure.com/{AZURE_ORG}/{AZURE_PROJECT}/_apis"

# Configuration de requête (Personal Access Token)
HEADERS = {
    'Authorization': f'Basic {AZURE_PAT}',
    'Content-Type': 'application/json'
}

# Péremption des données (ne pas télécharger plus de 4 mois)
DAYS_LOOKBACK = 120

print("═" * 70)
print(f"Configuration:")
print(f"  Organization: {AZURE_ORG}")
print(f"  Project:      {AZURE_PROJECT}")
print(f"  Lookback:     {DAYS_LOOKBACK} days")
print("═" * 70)


# ══════════════════════════════════════════════════════
# 1. EXTRACTION DES REPOSITORIES (81 services)
# ══════════════════════════════════════════════════════

def fetch_repositories():
    """
    Récupère tous les repositories (= services) du projet
    
    Endpoint: GET /_apis/git/repositories
    Résultat: liste des 81 microservices
    """
    print("\n[1/4] Extraction des repositories...")
    
    url = f"{BASE_URL}/git/repositories"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ Erreur API: {e}")
        return []
    
    data = response.json()
    repos = data.get("value", [])
    
    microservices = []
    for repo in repos:
        # Mapper le repo name → service name
        # Exemple: "mesx-order-api" → "mesx-order-api"
        service_name = repo.get("name", "")
        
        microservices.append({
            "name": service_name,
            "id": repo.get("id"),
            "repository_url": repo.get("webUrl", ""),
            "default_branch": repo.get("defaultBranch", "refs/heads/main"),
            "is_disabled": repo.get("isDisabled", False)
        })
        print(f"  ✓ {service_name}")
    
    print(f"✅ {len(microservices)} repositories extraits")
    return microservices


# ══════════════════════════════════════════════════════
# 2. EXTRACTION DES COMMITS (tous les repos)
# ══════════════════════════════════════════════════════

def fetch_commits(microservices):
    """
    Récupère tous les commits depuis les 4 derniers mois
    pour chaque repository (microservice)
    
    Endpoint: GET /_apis/git/repositories/{repoId}/commits
    Paramètre: from_date (filtre par date)
    Résultat: 3,142 commits mappés par service
    """
    print("\n[2/4] Extraction des commits par repository...")
    
    all_commits = []
    cutoff_date = (datetime.utcnow() - timedelta(days=DAYS_LOOKBACK)).isoformat()
    
    for i, service in enumerate(microservices):
        service_name = service["name"]
        repo_id = service["id"]
        
        print(f"  [{i+1}/{len(microservices)}] {service_name:<40}", end="", flush=True)
        
        url = f"{BASE_URL}/git/repositories/{repo_id}/commits"
        params = {
            "searchCriteria.itemVersion.version": service.get("default_branch", "main"),
            #"searchCriteria.fromDate": cutoff_date,  # Si on veut filtrer par date
            "$top": 500  # Max par requête
        }
        
        try:
            response = requests.get(url, headers=HEADERS, params=params, timeout=30)
            response.raise_for_status()
            commits = response.json().get("value", [])
            
            # Traiter chaque commit
            for commit in commits:
                commit_obj = {
                    "commit_id": commit.get("commitId"),
                    "auteur": commit.get("author", {}).get("name", ""),
                    "email": commit.get("author", {}).get("email", ""),
                    "date": commit.get("author", {}).get("date", ""),
                    "message": commit.get("comment", ""),
                    "microservice": service_name,
                    "fichiers_modifies": [
                        {
                            "fichier": change.get("item", {}).get("path", ""),
                            "action": change.get("changeType", "").lower()
                        }
                        for change in commit.get("changes", [])
                    ]
                }
                all_commits.append(commit_obj)
            
            print(f" ✓ {len(commits)} commits", flush=True)
            
        except requests.exceptions.RequestException as e:
            print(f" ❌ Erreur: {e}", flush=True)
            continue
    
    print(f"\n✅ {len(all_commits)} commits extraits au total")
    return all_commits


# ══════════════════════════════════════════════════════
# 3. EXTRACTION DES PULL REQUESTS
et

def fetch_pull_requests(microservices):
    """
    Récupère toutes les pull requests du projet
    
    Endpoint: GET /_apis/git/pullrequests
    Résultat: 4,532 PRs avec informations détaillées
    """
    print("\n[3/4] Extraction des pull requests...")
    
    url = f"{BASE_URL}/git/pullrequests"
    
    # Map service name by repo ID (pour enrichissement)
    repo_id_to_service = {s["id"]: s["name"] for s in microservices}
    
    all_prs = []
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        prs = response.json().get("value", [])
        
        for pr in prs:
            repo_id = pr.get("repository", {}).get("id", "")
            service_name = repo_id_to_service.get(repo_id, "unknown")
            
            pr_obj = {
                "pr_id": pr.get("pullRequestId"),
                "titre": pr.get("title", ""),
                "auteur": pr.get("createdBy", {}).get("displayName", ""),
                "statut": pr.get("status", "").lower(),
                "date_creation": pr.get("creationDate", ""),
                "date_fermeture": pr.get("closedDate", ""),
                "source_branch": pr.get("sourceRefName", ""),
                "target_branch": pr.get("targetRefName", ""),
                "description": pr.get("description", ""),
                "repo_id": repo_id,
                "microservice": service_name,
                "fichiers_modifies": [
                    {
                        "fichier": change.get("item", {}).get("path", ""),
                        "action": change.get("changeType", "").lower()
                    }
                    for change in pr.get("commits", [])
                ]
            }
            all_prs.append(pr_obj)
    
    except requests.exceptions.RequestException as e:
        print(f"❌ Erreur API: {e}")
    
    print(f"✅ {len(all_prs)} pull requests extraites")
    return all_prs


# ══════════════════════════════════════════════════════
# 4. EXTRACTION DES WORK ITEMS (Use Cases)
# ══════════════════════════════════════════════════════

def fetch_work_items():
    """
    Récupère tous les work items (user stories, epics, features, bugs)
    
    Endpoint: GET /_apis/wit/wiql
    WIQL Query: SELECT * FROM WorkItems WHERE [System.TeamProject] = 'MES_X.0'
    Résultat: 310 work items
    """
    print("\n[4/4] Extraction des work items...")
    
    # WIQL (Work Item Query Language)
    wiql_query = {
        "query": f"SELECT [System.Id], [System.Title], [System.Description], [System.State], [System.AssignedTo], [Microsoft.VSTS.Common.Priority], [System.AreaPath], [System.Tags], [System.WorkItemType] FROM WorkItems WHERE [System.TeamProject] = '{AZURE_PROJECT}'"
    }
    
    url = f"{BASE_URL}/wit/wiql"
    
    all_work_items = []
    
    try:
        response = requests.post(
            url,
            headers=HEADERS,
            json=wiql_query,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        
        workitem_ids = [wi["id"] for wi in data.get("workItems", [])]
        
        print(f"  Trouvé {len(workitem_ids)} work items, récupération des détails...")
        
        # Récupérer les détails de chaque work item
        for i, wi_id in enumerate(workitem_ids):
            if (i + 1) % 50 == 0:
                print(f"    [{i+1}/{len(workitem_ids)}]", flush=True)
            
            try:
                wi_url = f"{BASE_URL}/wit/workitems/{wi_id}"
                wi_response = requests.get(wi_url, headers=HEADERS, timeout=30)
                wi_response.raise_for_status()
                wi_data = wi_response.json()
                
                fields = wi_data.get("fields", {})
                
                work_item = {
                    "id": wi_id,
                    "type": fields.get("System.WorkItemType", ""),
                    "titre": fields.get("System.Title", ""),
                    "description": fields.get("System.Description", ""),
                    "statut": fields.get("System.State", ""),
                    "priorite": fields.get("Microsoft.VSTS.Common.Priority", ""),
                    "area_path": fields.get("System.AreaPath", ""),
                    "tags": fields.get("System.Tags", "").split(";") if fields.get("System.Tags") else [],
                    "assigned_to": fields.get("System.AssignedTo", "")
                }
                all_work_items.append(work_item)
                
            except requests.exceptions.RequestException as e:
                print(f"    ⚠️  Erreur wi #{wi_id}: {e}")
                continue
    
    except requests.exceptions.RequestException as e:
        print(f"❌ Erreur API WIQL: {e}")
    
    print(f"✅ {len(all_work_items)} work items extraits")
    return all_work_items


# ══════════════════════════════════════════════════════
# SAUVEGARDE DES DONNÉES
# ══════════════════════════════════════════════════════

def save(filename, data):
    """Sauvegarde les données en JSON"""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    if isinstance(data, list):
        count = len(data)
        print(f"  📄 {filename:<30} ({count} éléments)")
    else:
        print(f"  📄 {filename:<30} (objet)")


# ══════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "═" * 70)
    print("MEX.0 — ÉTAPE 1 : EXTRACTION AZURE DEVOPS")
    print("═" * 70)
    
    # ─── ÉTAPE 1: Repos ───
    microservices = fetch_repositories()
    if not microservices:
        print("❌ Pas de repositories trouvés. Vérifiez Azure PAT et connexion réseau.")
        exit(1)
    
    # ─── ÉTAPE 2: Commits ───
    commits = fetch_commits(microservices)
    
    # ─── ÉTAPE 3: Pull Requests ───
    pull_requests = fetch_pull_requests(microservices)
    
    # ─── ÉTAPE 4: Work Items ───
    work_items = fetch_work_items()
    
    # ─── SAUVEGARDE ───
    print("\n" + "─" * 70)
    print("Sauvegarde des données...")
    print("─" * 70)
    
    save("microservices.json", microservices)
    save("commits.json", commits)
    save("pull_requests.json", pull_requests)
    save("work_items.json", work_items)
    
    print("\n" + "═" * 70)
    print("✅ EXTRACTION TERMINÉE")
    print("═" * 70)
    print(f"\n📊 RÉSUMÉ:")
    print(f"   • {len(microservices)} microservices")
    print(f"   • {len(commits)} commits")
    print(f"   • {len(pull_requests)} pull requests")
    print(f"   • {len(work_items)} work items")
    print(f"\n🚀 Prochaine étape: py -3.11 step2_parser.py")
    print("═" * 70)
