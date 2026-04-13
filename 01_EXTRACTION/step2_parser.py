# -*- coding: utf-8 -*-
"""
step2_parser.py - MEX.0
ETAPE 2 : Analyser les fichiers source téléchargés
et détecter automatiquement :
  - Les fonctions/endpoints dans le code Java/TS
  - Les appels API entre microservices (Feign, RestTemplate)
  - Les Use Cases depuis les Work Items
  - Les relations COVERS (fonction → use case)

Lance ce fichier APRÈS step1_azure_extract.py
Génère : graph_data.json
"""

import json
import re
import os
from pathlib import Path

# ══════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════

FICHIER_SOURCE     = "tous_fichiers_source.json"
FICHIER_WORK_ITEMS = "work_items.json"
FICHIER_MICROS     = "microservices.json"
FICHIER_COMMITS    = "commits.json"
FICHIER_PRS        = "pull_requests.json"
FICHIER_GRAPH      = "graph_data.json"

# Extensions à ignorer pour le parsing
EXTENSIONS_IGNORER = {".md", ".yaml", ".yml", ".json", ".xml", ".txt",
                      ".lock", ".properties", ".gradle", ".png", ".jpg",
                      ".svg", ".css", ".html"}

# Fichiers à ignorer
NOMS_IGNORER = {"test", "spec", "mock", "index", "__init__",
                "package", "setup", "pom", "banner"}

# Tous les noms de services pour détecter les appels inter-services
# (sera rempli dynamiquement depuis microservices.json)
NOMS_SERVICES = []


# ══════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════

def charger(fichier, defaut=None):
    if defaut is None:
        defaut = []
    if not os.path.exists(fichier):
        print(f"  ATTENTION : {fichier} manquant — lance d'abord step1_azure_extract.py")
        return defaut
    with open(fichier, "r", encoding="utf-8") as f:
        return json.load(f)


def sauver(fichier, data):
    with open(fichier, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    nb = len(data) if isinstance(data, list) else "objet"
    print(f"  [OK] {fichier}  ({nb} éléments)")


def nettoyer_nom(nom):
    """Transforme un chemin en nom de fonction lisible"""
    nom = Path(nom).stem  # enlève l'extension
    # CamelCase → snake_case lisible
    nom = re.sub(r'([A-Z])', r'_\1', nom).strip('_').lower()
    return nom


def normaliser_service(nom):
    """Normalise un nom pour matcher les noms de services"""
    return re.sub(r"[^a-z0-9]", "", (nom or "").lower())


def extraire_service_depuis_url(url_fragment):
    """Extrait le nom d'un service depuis une URL ou un nom Feign"""
    if not url_fragment:
        return ""
    url_low = url_fragment.lower()
    for svc in NOMS_SERVICES:
        svc_norm = normaliser_service(svc)
        if len(svc_norm) > 4 and svc_norm in normaliser_service(url_low):
            return svc
    return ""


# ══════════════════════════════════════════════════════════
# PARSER JAVA (Spring Boot)
# ══════════════════════════════════════════════════════════

class JavaParser:

    # Endpoints REST
    RE_MAPPING = re.compile(
        r'@(Get|Post|Put|Delete|Patch|Request)Mapping\s*'
        r'(?:\(\s*(?:value\s*=\s*)?["\']([^"\']*)["\'])?',
        re.IGNORECASE
    )
    # Méthodes publiques
    RE_METHOD = re.compile(
        r'(?:public|protected)\s+(?:static\s+)?(?:final\s+)?'
        r'[\w<>\[\],\s]+\s+(\w+)\s*\([^)]*\)',
        re.MULTILINE
    )
    # FeignClient
    RE_FEIGN = re.compile(
        r'@FeignClient\s*\(\s*(?:name\s*=\s*)?["\']([^"\']+)["\']',
        re.IGNORECASE
    )
    # RestTemplate
    RE_REST = re.compile(
        r'(?:restTemplate|restClient)\s*\.\s*\w+\s*\(\s*["\']?([^,)"\']{5,})',
        re.IGNORECASE
    )
    # WebClient
    RE_WEBCLIENT = re.compile(
        r'WebClient\s*\.\s*(?:create|builder)\s*\(\s*["\']([^"\']+)["\']',
        re.IGNORECASE
    )
    # Nom de classe
    RE_CLASS = re.compile(r'(?:public\s+)?class\s+(\w+)', re.MULTILINE)
    # Package
    RE_PACKAGE = re.compile(r'^package\s+([\w.]+)\s*;', re.MULTILINE)

    def parse(self, contenu, microservice, chemin):
        fonctions  = []
        appels_api = []

        nom_fichier = Path(chemin).stem
        classes     = self.RE_CLASS.findall(contenu)
        classe      = classes[0] if classes else nom_fichier
        pkg         = (self.RE_PACKAGE.search(contenu) or type('', (), {'group': lambda s, x: ''})()).group(1)

        # ── Endpoints ──
        for m in self.RE_MAPPING.finditer(contenu):
            http   = m.group(1).upper()
            path   = m.group(2) or "/"
            # Cherche la méthode Java après le décorateur
            pos    = m.end()
            suite  = contenu[pos:pos + 250]
            nm     = re.search(r'(?:public|private)\s+\S+\s+(\w+)\s*\(', suite)
            nom_fn = nm.group(1) if nm else f"{http}_{path.replace('/','_').strip('_')}"
            fn_id  = f"{microservice}::{classe}::{nom_fn}"
            fonctions.append({
                "id":           fn_id,
                "nom":          nom_fn,
                "classe":       classe,
                "package":      pkg,
                "type":         "endpoint",
                "http_method":  http,
                "path":         path,
                "microservice": microservice,
                "fichier":      chemin
            })

        # ── Méthodes publiques (non-endpoint, non-triviales) ──
        ids_existants = {f["nom"] for f in fonctions}
        for m in self.RE_METHOD.finditer(contenu):
            nom_fn = m.group(1)
            if nom_fn == classe:
                continue  # constructeur
            if re.match(r'^(get|set|is)[A-Z]', nom_fn):
                continue  # getter/setter
            if nom_fn in ids_existants:
                continue
            ids_existants.add(nom_fn)
            fonctions.append({
                "id":           f"{microservice}::{classe}::{nom_fn}",
                "nom":          nom_fn,
                "classe":       classe,
                "package":      pkg,
                "type":         "method",
                "http_method":  "",
                "path":         "",
                "microservice": microservice,
                "fichier":      chemin
            })

        # ── Appels API sortants ──
        for m in self.RE_FEIGN.finditer(contenu):
            cible = extraire_service_depuis_url(m.group(1))
            if cible and cible != microservice:
                appels_api.append({
                    "source": microservice, "cible": cible,
                    "via": "FeignClient", "fichier": chemin,
                    "detail": m.group(1)[:80]
                })

        for m in self.RE_REST.finditer(contenu):
            cible = extraire_service_depuis_url(m.group(1))
            if cible and cible != microservice:
                appels_api.append({
                    "source": microservice, "cible": cible,
                    "via": "RestTemplate", "fichier": chemin,
                    "detail": m.group(1)[:80]
                })

        for m in self.RE_WEBCLIENT.finditer(contenu):
            cible = extraire_service_depuis_url(m.group(1))
            if cible and cible != microservice:
                appels_api.append({
                    "source": microservice, "cible": cible,
                    "via": "WebClient", "fichier": chemin,
                    "detail": m.group(1)[:80]
                })

        return fonctions, appels_api


# ══════════════════════════════════════════════════════════
# PARSER TYPESCRIPT / JAVASCRIPT (NestJS / Angular)
# ══════════════════════════════════════════════════════════

class TSParser:

    RE_DECO = re.compile(
        r'@(Get|Post|Put|Delete|Patch)\s*\(\s*["\']?([^"\')\s]*)["\']?\s*\)',
        re.IGNORECASE
    )
    RE_HTTP = re.compile(
        r'(?:axios|http|this\.http)\s*\.'
        r'(?:get|post|put|delete|patch)\s*\(\s*[`"\']([^`"\']+)',
        re.IGNORECASE
    )
    RE_BASE = re.compile(
        r'(?:baseURL|baseUrl|API_URL|apiUrl)\s*[=:]\s*[`"\']([^`"\']+)',
        re.IGNORECASE
    )

    def parse(self, contenu, microservice, chemin):
        fonctions  = []
        appels_api = []
        nom_fic    = Path(chemin).stem

        for m in self.RE_DECO.finditer(contenu):
            http   = m.group(1).upper()
            path   = m.group(2) or "/"
            pos    = m.end()
            suite  = contenu[pos:pos + 150]
            nm     = re.search(r'(\w+)\s*\(', suite)
            nom_fn = nm.group(1) if nm else f"{http}_{path.replace('/','_').strip('_')}"
            fonctions.append({
                "id":           f"{microservice}::{nom_fic}::{nom_fn}",
                "nom":          nom_fn,
                "classe":       nom_fic,
                "package":      "",
                "type":         "endpoint",
                "http_method":  http,
                "path":         path,
                "microservice": microservice,
                "fichier":      chemin
            })

        for m in self.RE_HTTP.finditer(contenu):
            cible = extraire_service_depuis_url(m.group(1))
            if cible and cible != microservice:
                appels_api.append({
                    "source": microservice, "cible": cible,
                    "via": "HTTP", "fichier": chemin,
                    "detail": m.group(1)[:80]
                })

        for m in self.RE_BASE.finditer(contenu):
            cible = extraire_service_depuis_url(m.group(1))
            if cible and cible != microservice:
                appels_api.append({
                    "source": microservice, "cible": cible,
                    "via": "BaseURL", "fichier": chemin,
                    "detail": m.group(1)[:80]
                })

        return fonctions, appels_api


# ══════════════════════════════════════════════════════════
# PARSER CONFIG (YAML / Properties)
# ══════════════════════════════════════════════════════════

def parse_config(contenu, microservice, chemin):
    """Détecte des URLs de services dans les fichiers de configuration"""
    appels = []
    for m in re.finditer(
        r'(?:url|host|endpoint|service)\s*[=:]\s*([^\n#]+)',
        contenu, re.IGNORECASE
    ):
        val   = m.group(1).strip().strip('"\'')
        cible = extraire_service_depuis_url(val)
        if cible and cible != microservice:
            appels.append({
                "source": microservice, "cible": cible,
                "via": "Config", "fichier": chemin,
                "detail": val[:80]
            })
    return [], appels


# ══════════════════════════════════════════════════════════
# EXTRACTION USE CASES DEPUIS COMMITS/PRS
# (fallback si pas de work items)
# ══════════════════════════════════════════════════════════

def extraire_fonctions_depuis_commits(commits, microservices_set):
    """
    Extrait les fonctions depuis les noms de fichiers modifiés dans les commits.
    Utilisé comme fallback si les fichiers source ne sont pas disponibles.
    """
    fonctions  = []
    vus        = set()

    for c in commits:
        ms = c.get("microservice", "")
        if not ms:
            continue

        for fichier_info in c.get("fichiers_modifies", []):
            chemin = fichier_info.get("fichier", "") if isinstance(fichier_info, dict) else fichier_info
            if not chemin:
                continue

            ext      = Path(chemin).suffix.lower()
            nom_fic  = Path(chemin).stem

            if ext in EXTENSIONS_IGNORER:
                continue
            if any(bad in nom_fic.lower() for bad in NOMS_IGNORER):
                continue
            if len(nom_fic) < 3:
                continue

            fn_id = f"{ms}::commit::{nom_fic}"
            if fn_id in vus:
                continue
            vus.add(fn_id)

            fonctions.append({
                "id":           fn_id,
                "nom":          nom_fic,
                "classe":       nom_fic,
                "package":      "",
                "type":         "file",
                "http_method":  "",
                "path":         "",
                "microservice": ms,
                "fichier":      chemin
            })

    return fonctions


# ══════════════════════════════════════════════════════════
# EXTRACTION APPELS API DEPUIS PULL REQUESTS
# ══════════════════════════════════════════════════════════

def extraire_appels_depuis_prs(prs):
    """Détecte les dépendances inter-services depuis les chemins de fichiers des PRs"""
    appels = []
    vus    = set()

    for pr in prs:
        ms_source = pr.get("microservice", "")
        if not ms_source:
            continue

        # Cherche dans les fichiers modifiés
        for finfo in pr.get("fichiers_modifies", []):
            chemin    = finfo.get("fichier", "") if isinstance(finfo, dict) else finfo
            chemin_lw = normaliser_service(chemin)
            for svc in NOMS_SERVICES:
                if svc == ms_source:
                    continue
                svc_norm = normaliser_service(svc.replace("mesx-", ""))
                if len(svc_norm) > 4 and svc_norm in chemin_lw:
                    cle = f"{ms_source}→{svc}"
                    if cle not in vus:
                        vus.add(cle)
                        appels.append({
                            "source": ms_source, "cible": svc,
                            "via": "PR_file", "fichier": chemin,
                            "detail": chemin[:80]
                        })

        # Cherche dans titre/description/branches
        texte = " ".join([
            pr.get("titre", ""),
            pr.get("description", ""),
            pr.get("source_branch", ""),
            pr.get("target_branch", "")
        ]).lower()
        texte_norm = normaliser_service(texte)
        for svc in NOMS_SERVICES:
            if svc == ms_source:
                continue
            svc_norm = normaliser_service(svc.replace("mesx-", ""))
            if len(svc_norm) > 4 and svc_norm in texte_norm:
                cle = f"{ms_source}→{svc}_text"
                if cle not in vus:
                    vus.add(cle)
                    appels.append({
                        "source": ms_source, "cible": svc,
                        "via": "PR_text", "fichier": "",
                        "detail": texte[:80]
                    })

    return appels


# ══════════════════════════════════════════════════════════
# EXTRACTION USE CASES DEPUIS WORK ITEMS
# ══════════════════════════════════════════════════════════

def extraire_use_cases(work_items):
    use_cases = []
    types_valides = {"epic", "feature", "user story", "bug", "task"}

    for wi in work_items:
        wi_type = wi.get("type", "").lower()
        titre   = wi.get("titre", "")
        desc    = wi.get("description", "") or ""

        if not titre:
            continue
        if wi_type not in types_valides:
            continue

        mots = re.findall(r'\b[a-zA-Z]{4,}\b', (titre + " " + desc).lower())
        stop = {"with", "this", "that", "from", "pour", "dans", "avec", "les", "des"}
        mots_cles = list({m for m in mots if m not in stop})[:20]

        use_cases.append({
            "id":           f"UC_{wi['id']}",
            "work_item_id": wi["id"],
            "titre":        titre,
            "description":  desc[:400],
            "type":         wi.get("type", ""),
            "statut":       wi.get("statut", ""),
            "priorite":     str(wi.get("priorite", "MEDIUM")),
            "tags":         wi.get("tags", ""),
            "area_path":    wi.get("area_path", ""),
            "mots_cles":    mots_cles
        })

    return use_cases


# ══════════════════════════════════════════════════════════
# ASSOCIATIONS FUNCTION → USE CASE (COVERS)
# ══════════════════════════════════════════════════════════

def associer_covers(fonctions, use_cases):
    """Crée les relations COVERS par similarité de mots-clés"""
    relations = []

    for fn in fonctions:
        mots_fn = set(re.findall(r'\b[a-zA-Z]{4,}\b',
                                  (fn["nom"] + " " + fn.get("path", "")).lower()))
        if not mots_fn:
            continue

        for uc in use_cases:
            mots_uc = set(uc.get("mots_cles", []))
            if not mots_uc:
                continue

            inter = mots_fn & mots_uc
            union = mots_fn | mots_uc
            score = len(inter) / len(union) if union else 0

            if score > 0.08 or len(inter) >= 2:
                relations.append({
                    "source":       fn["id"],
                    "cible":        uc["id"],
                    "type":         "COVERS",
                    "score":        round(score, 3),
                    "mots_communs": list(inter)[:5]
                })

    relations.sort(key=lambda x: -x["score"])
    return relations


# ══════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("MEX.0 — ÉTAPE 2 : PARSING CODE SOURCE")
    print("=" * 60)

    # ── Chargement ──
    print("\n[1/5] Chargement des données...")
    microservices   = charger(FICHIER_MICROS)
    fichiers_source = charger(FICHIER_SOURCE)
    work_items      = charger(FICHIER_WORK_ITEMS)
    commits         = charger(FICHIER_COMMITS)
    pull_requests   = charger(FICHIER_PRS)

    # Alimente la liste globale des noms de services
    NOMS_SERVICES = [s["name"] for s in microservices if s.get("name")]

    print(f"  Microservices   : {len(microservices)}")
    print(f"  Fichiers source : {len(fichiers_source)}")
    print(f"  Work Items      : {len(work_items)}")
    print(f"  Commits         : {len(commits)}")
    print(f"  Pull Requests   : {len(pull_requests)}")

    # ── Parsing des fichiers source ──
    print("\n[2/5] Parsing des fichiers source...")
    java_p = JavaParser()
    ts_p   = TSParser()

    toutes_fonctions = []
    tous_appels_api  = []

    for i, finfo in enumerate(fichiers_source):
        ms      = finfo.get("microservice", "")
        chemin  = finfo.get("chemin", "")
        contenu = finfo.get("contenu", "")
        ext     = Path(chemin).suffix.lower()

        print(f"\r  [{i+1}/{len(fichiers_source)}] {chemin[-50:]:<50}", end="", flush=True)

        if not contenu or len(contenu) < 50:
            continue

        if ext == ".java":
            fns, appels = java_p.parse(contenu, ms, chemin)
        elif ext in (".ts", ".js"):
            fns, appels = ts_p.parse(contenu, ms, chemin)
        elif ext in (".yml", ".yaml", ".properties"):
            fns, appels = parse_config(contenu, ms, chemin)
        else:
            continue

        toutes_fonctions.extend(fns)
        tous_appels_api.extend(appels)

    print(f"\n  Fonctions depuis source   : {len(toutes_fonctions)}")
    print(f"  Appels API depuis source  : {len(tous_appels_api)}")

    # ── Fallback : fonctions depuis commits si peu de source ──
    if len(toutes_fonctions) < 10:
        print("\n  Peu de fonctions détectées depuis source → fallback commits...")
        fns_commits = extraire_fonctions_depuis_commits(commits, set(NOMS_SERVICES))
        print(f"  + {len(fns_commits)} fonctions extraites depuis commits")
        toutes_fonctions.extend(fns_commits)

    # ── Appels API depuis PRs (en complément) ──
    print("\n[3/5] Détection des appels API depuis les PRs...")
    appels_prs = extraire_appels_depuis_prs(pull_requests)
    tous_appels_api.extend(appels_prs)
    print(f"  + {len(appels_prs)} appels détectés depuis PRs")

    # ── Dédoublonnage ──
    print("\n[4/5] Dédoublonnage...")
    fns_uniques    = {f["id"]: f for f in toutes_fonctions}
    toutes_fonctions = list(fns_uniques.values())

    appels_uniques  = {}
    for a in tous_appels_api:
        cle = f"{a['source']}→{a['cible']}→{a['via']}"
        appels_uniques[cle] = a
    tous_appels_api = list(appels_uniques.values())

    print(f"  Fonctions uniques  : {len(toutes_fonctions)}")
    print(f"  Appels API uniques : {len(tous_appels_api)}")

    # ── Use Cases ──
    print("\n[5/5] Extraction Use Cases + relations COVERS...")
    use_cases        = extraire_use_cases(work_items)
    relations_covers = associer_covers(toutes_fonctions, use_cases)
    print(f"  Use Cases  : {len(use_cases)}")
    print(f"  COVERS     : {len(relations_covers)}")

    # ── Résumé par service ──
    print("\n  Résumé par microservice :")
    services_vus = sorted(set(f["microservice"] for f in toutes_fonctions))
    for ms in services_vus:
        nb_fn = len([f for f in toutes_fonctions if f["microservice"] == ms])
        nb_ep = len([f for f in toutes_fonctions if f["microservice"] == ms and f["type"] == "endpoint"])
        nb_ap = len([a for a in tous_appels_api if a["source"] == ms])
        print(f"    {ms:<40} : {nb_fn} fns ({nb_ep} endpoints), {nb_ap} appels")

    # ── Sauvegarde ──
    graph_data = {
        "microservices":    microservices,
        "fonctions":        toutes_fonctions,
        "appels_api":       tous_appels_api,
        "use_cases":        use_cases,
        "relations_covers": relations_covers,
        "commits":          commits,
        "pull_requests":    pull_requests,
        "work_items":       work_items
    }
    sauver(FICHIER_GRAPH,         graph_data)
    sauver("fonctions.json",      toutes_fonctions)
    sauver("appels_api.json",     tous_appels_api)
    sauver("use_cases.json",      use_cases)
    sauver("covers.json",         relations_covers)

    print("\n" + "=" * 60)
    print("PARSING TERMINÉ → graph_data.json prêt")
    print("Prochaine étape : py -3.11 step3_neo4j.py")
    print("=" * 60)