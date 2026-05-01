"""
NRT SCOPE VISUALIZATION SYSTEM - Lightweight Web Server
Pure Python HTTP Server without external dependencies (except neo4j)
Serves HTML templates and provides JSON API endpoints
"""

import http.server
import socketserver
import json
import os
import re
import string
import urllib.parse
from pathlib import Path
import mimetypes
from neo4j import GraphDatabase
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Neo4j Configuration from environment variables
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j2026!")
STRICT_CODE_ONLY = os.getenv("STRICT_CODE_ONLY", "true").strip().lower() in {"1", "true", "yes", "on"}
def _parse_indirect_cap(raw_value: str) -> int | None:
    """
    Parse indirect function cap from env.
    None means unlimited.
    """
    try:
        value = int((raw_value or "").strip())
    except Exception:
        return 20
    if value <= 0:
        return None
    return value


NRT_INDIRECT_FUNCTION_CAP = _parse_indirect_cap(os.getenv("NRT_INDIRECT_FUNCTION_CAP", "20"))
NRT_ALLOWED_DEP_TYPES = [
    x.strip().upper()
    for x in os.getenv("NRT_ALLOWED_DEP_TYPES", "API_CALLS,MESSAGE_BROKER,INGESTION").split(",")
    if x.strip()
]
NRT_INDIRECT_BUSINESS_ONLY = os.getenv("NRT_INDIRECT_BUSINESS_ONLY", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
BASE_DIR = Path(__file__).parent

class NRTAnalyzer:
    """Analyze NRT scopes from Neo4j"""
    
    def __init__(self):
        try:
            self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
            self.driver.verify_connectivity()
            print("[OK] Connected to Neo4j database")
        except Exception as e:
            print(f"[ERROR] Failed to connect to Neo4j: {e}")
            self.driver = None
        
        # Load workitems with parent-child relationships
        self.workitems_data = self._load_workitems()
        print(f"[OK] Loaded {len(self.workitems_data)} WorkItems from JSON")

        # Build an index of UI screens from source files to provide tester-friendly output
        self.ui_screen_index = self._build_ui_screen_index()
        print(f"[OK] Indexed UI screens for {len(self.ui_screen_index)} microservices")
    
    def _load_workitems(self):
        """Load WorkItems from JSON file with parent-child relationships"""
        try:
            json_path = Path(__file__).parent.parent / "01_EXTRACTION" / "work_items.json"
            with open(json_path, 'r', encoding='utf-8') as f:
                items = json.load(f)
            
            # Index by ID for fast lookup
            indexed = {}
            for item in items:
                indexed[item['id']] = item
            
            print(f"   - Loaded {len(indexed)} WorkItems")
            return indexed
        except Exception as e:
            print(f"[WARN] Could not load WorkItems JSON: {e}")
            return {}

    def _build_ui_screen_index(self):
        """Build a microservice -> confirmed screens index from UI route metadata."""
        index = {}
        repo_root = Path(__file__).parent.parent

        for source_file in repo_root.glob("source_mesx_*ui*.json"):
            try:
                ms_name = source_file.stem.replace("source_", "").replace("_", "-")
                with open(source_file, 'r', encoding='utf-8') as f:
                    entries = json.load(f)

                screens = set()
                for entry in entries:
                    content = entry.get('contenu', '')
                    file_path = entry.get('chemin', '')

                    # Strict mode: keep only titles explicitly declared in router meta.
                    if isinstance(content, str) and content and 'createRouter' in content:
                        for title in re.findall(r"title:\s*'([^']+)'", content):
                            clean_title = re.sub(r"\s+", " ", title).strip()
                            if clean_title and clean_title.lower() not in {'logout', 'unauthorized', 'loading', 'admin ui'}:
                                screens.add(clean_title)

                    # Do not derive names from route paths; keep only explicit UI titles for enterprise-grade accuracy.

                if screens:
                    index[ms_name] = sorted(screens)
            except Exception:
                # Keep startup resilient if one source file cannot be parsed
                continue

        return index

    def _extract_screens_from_test_cases(self, workitem_id):
        """Extract screen names from child test cases titles/tags."""
        screens = set()
        children = self.get_workitem_children(workitem_id)
        for child in children:
            if child.get('type') != 'Test Case':
                continue

            title = child.get('titre', '')
            tags = child.get('tags', '')

            for chunk in re.findall(r"\[([^\]]+)\]", title):
                candidate = re.sub(r"(?i)\bscreen\b", "", chunk).strip(" -_")
                if candidate and len(candidate) > 2:
                    screens.add(candidate)

            if isinstance(tags, str):
                for tag in [t.strip() for t in tags.split(';') if t.strip()]:
                    if any(key in tag.lower() for key in ['dashboard', 'screen', 'monitoring', 'status']):
                        screens.add(tag.title())

        return sorted(screens)

    def _derive_domains(self, screens, microservices):
        """Derive business domains from screen and microservice names."""
        domains = set()
        text = ' '.join(screens + microservices).lower()

        if any(k in text for k in ['dashboard', 'visual', 'status', 'page builder']):
            domains.add('Visual Management')
        if any(k in text for k in ['production', 'workcenter', 'routing', 'material']):
            domains.add('Production Management')
        if any(k in text for k in ['downtime']):
            domains.add('Downtime Management')
        if any(k in text for k in ['quality']):
            domains.add('Quality Management')
        if any(k in text for k in ['resource', 'plant', 'site']):
            domains.add('Resource Management')

        return sorted(domains) if domains else ['Core MES_X.0']

    def _build_tester_scope(self, workitem, direct_ms, indirect_ms):
        """Build tester-oriented scope with domains/screens and expected behavior."""
        all_ms = [ms.get('name') for ms in (direct_ms + indirect_ms) if ms.get('name')]
        unique_ms = sorted(set(all_ms))

        # Strict mode: only confirmed screens from UI source index.
        screens = set()

        # Expand probable UI counterparts (e.g. xxx-backend -> xxx-ui)
        expanded_ms = set(unique_ms)
        for ms in unique_ms:
            if ms.endswith('-backend'):
                expanded_ms.add(ms[:-8] + '-ui')
            if ms.endswith('-api'):
                expanded_ms.add(ms[:-4] + '-ui')

        for ms in sorted(expanded_ms):
            if ms in self.ui_screen_index:
                screens.update(self.ui_screen_index[ms])

        screens = sorted(screens)
        domains = self._derive_domains(screens, unique_ms)

        impacted_functionality = []
        for ms in direct_ms[:4]:
            fn_names = [f.get('name') for f in ms.get('functions', []) if f.get('name')][:3]
            if fn_names:
                impacted_functionality.append(f"{ms['name']}: {', '.join(fn_names)}")

        if not impacted_functionality:
            impacted_functionality.append('Validate end-to-end flows linked to impacted microservices')

        behavior_focus = ', '.join(screens[:5]) if screens else 'impacted business screens'

        return {
            'domains': domains,
            'screens': screens,
            'screen_source': 'confirmed_ui_routes_only',
            'microservices': unique_ms,
            'impacted_functionality': impacted_functionality,
            'nrt_expected_behavior': (
                f"Confirm there is no regression on {behavior_focus}, and existing workflows continue to behave as expected."
            )
        }

    def _indirect_function_score(self, dep_type, function_obj, inbound_ms):
        """Deterministic and explainable priority score for indirect functions."""
        score = 0
        reasons = []

        dep_weights = {
            "API_CALLS": 3,
            "MESSAGE_BROKER": 2,
            "INGESTION": 1,
        }
        dep_weight = dep_weights.get(dep_type, 0)
        score += dep_weight
        reasons.append(f"dependency_type={dep_type}(+{dep_weight})")

        http_method = (function_obj.get("http_method") or "").strip()
        path = (function_obj.get("path") or "").strip()
        if http_method or path:
            score += 4
            reasons.append("public_endpoint(+4)")

        if inbound_ms >= 5:
            score += 2
            reasons.append("high_fan_in(+2)")
        elif inbound_ms >= 2:
            score += 1
            reasons.append("medium_fan_in(+1)")

        # De-prioritize low-business-value generic health endpoints.
        fn_name = (function_obj.get("name") or "").lower()
        if fn_name in {"gethello", "health", "gethealth"}:
            score -= 1
            reasons.append("generic_endpoint(-1)")

        return score, reasons

    def _is_business_function(self, function_obj):
        """
        Determine whether a function is business-relevant for tester-facing indirect scope.
        This is deterministic filtering (no ML/no heuristic similarity).
        """
        name = (function_obj.get("name") or "").strip()
        name_l = name.lower()
        path = (function_obj.get("path") or "").strip()
        path_l = path.lower()
        fn_id = (function_obj.get("id") or "").lower()
        fn_type = (function_obj.get("type") or "").lower()

        technical_names = {
            "gethello", "gethealth", "health",
            "apioperation", "apibody", "header", "httpcode",
            "usefilters", "useinterceptors",
            "bootstrap", "initializapp", "initializeapp",
            "inittelemetry", "asynsleep", "asyncsleep",
            "debug", "warn", "error", "verbose", "log",
            "normalize", "formatdate", "tocamelcasekey", "tocamelcasedeep",
        }
        if name_l in technical_names:
            return False

        technical_id_markers = (
            "::main::",
            "::app_logger::",
            "::console_logger::",
            "::sleep::",
            "::telemetry::",
            "::environment::",
            "::helpers::",
            "::log_context_util::",
            "::redis_auth_helper::",
        )
        if any(marker in fn_id for marker in technical_id_markers):
            return False

        # Endpoint: keep only non-technical routes.
        if fn_type == "endpoint" or (function_obj.get("http_method") or "").strip():
            if not path_l or path_l in {"/", "/dapr/", "/dapr"}:
                return False
            technical_path_tokens = ("health", "probe", "metric", "metrics", "dapr", "swagger")
            if any(tok in path_l for tok in technical_path_tokens):
                return False
            return True

        # Non-endpoint: keep only action-oriented business operations.
        business_action_tokens = (
            "classify", "update", "create", "delete", "remove",
            "fetch", "find", "list", "search", "filter",
            "save", "apply", "publish", "subscribe", "receive",
            "assign", "unassign", "reschedule", "execute",
            "start", "stop", "close", "open",
            "authorize", "authenticate", "validate",
        )
        return any(token in name_l for token in business_action_tokens)

    def _build_prioritized_test_plan(self, workitem_id, direct_ms, indirect_ms):
        """
        Build a tester-readable plan with explicit priorities:
        - P1: direct traceability scope (must test)
        - P2/P3: ranked indirect risk-based scope (recommended)
        """
        must_test = []
        risk_based_candidates = []

        for ms in direct_ms:
            ms_name = ms.get("name")
            commit_count = ms.get("commit_count", 0)
            commit_ids = ms.get("commit_ids", []) or []
            functions = ms.get("functions", []) or []

            if functions:
                for f in functions:
                    if not isinstance(f, dict) or not f.get("id"):
                        continue
                    must_test.append({
                        "priority": "P1",
                        "scope": "DIRECT",
                        "microservice": ms_name,
                        "function_id": f.get("id"),
                        "function_name": f.get("name"),
                        "commit_count": commit_count,
                        "commit_ids_sample": commit_ids[:5],
                        "why": "Directly linked WorkItem commits touched this parsed function.",
                        "evidence": [
                            "WorkItem -> LINKED_TO_COMMIT / LINKED_TO_PULL_REQUEST -> CONTAINS_COMMIT",
                            "Commit -> TOUCHES_FUNCTION",
                        ],
                    })
            else:
                must_test.append({
                    "priority": "P1",
                    "scope": "DIRECT",
                    "microservice": ms_name,
                    "function_id": None,
                    "function_name": None,
                    "commit_count": commit_count,
                    "commit_ids_sample": commit_ids[:5],
                    "why": "Directly linked commits modify this microservice, but no parsed function match was found.",
                    "evidence": [
                        "WorkItem -> LINKED_TO_COMMIT / LINKED_TO_PULL_REQUEST -> CONTAINS_COMMIT",
                        "Commit -> MODIFIES -> Microservice",
                    ],
                })

        for ms in indirect_ms:
            dep_type = ms.get("dependency_type", "")
            inbound_ms = int(ms.get("inbound_ms_count", 0) or 0)
            ms_name = ms.get("name")
            source_ms = ms.get("source_microservice")
            for f in (ms.get("functions") or []):
                if not isinstance(f, dict) or not f.get("id"):
                    continue
                score, reasons = self._indirect_function_score(dep_type, f, inbound_ms)
                risk_based_candidates.append({
                    "scope": "INDIRECT",
                    "microservice": ms_name,
                    "source_microservice": source_ms,
                    "dependency_type": dep_type,
                    "inbound_ms_count": inbound_ms,
                    "function_id": f.get("id"),
                    "function_name": f.get("name"),
                    "http_method": f.get("http_method"),
                    "path": f.get("path"),
                    "score": score,
                    "score_reasons": reasons,
                    "why": "Potentially impacted through dependency propagation from direct scope.",
                })

        risk_based_candidates.sort(
            key=lambda x: (
                -x.get("score", 0),
                x.get("microservice") or "",
                x.get("function_name") or "",
            )
        )

        dedup = {}
        for item in risk_based_candidates:
            key = (item.get("microservice"), item.get("function_id"))
            if key not in dedup:
                dedup[key] = item
        ranked_unique = list(dedup.values())

        selected = ranked_unique if NRT_INDIRECT_FUNCTION_CAP is None else ranked_unique[:NRT_INDIRECT_FUNCTION_CAP]
        risk_based = []
        for item in selected:
            item["priority"] = "P2" if item.get("score", 0) >= 7 else "P3"
            risk_based.append(item)

        return {
            "workitem_id": workitem_id,
            "mode": "STRICT_CODE_ONLY" if STRICT_CODE_ONLY else "TRACEABILITY_PLUS_ENRICHMENT",
            "allowed_dependency_types": NRT_ALLOWED_DEP_TYPES,
            "indirect_function_cap": "unlimited" if NRT_INDIRECT_FUNCTION_CAP is None else NRT_INDIRECT_FUNCTION_CAP,
            "rules": {
                "direct_scope": "P1 direct traceability items are always included.",
                "indirect_scoring": {
                    "API_CALLS": 3,
                    "MESSAGE_BROKER": 2,
                    "INGESTION": 1,
                    "public_endpoint": 4,
                    "fan_in_high": 2,
                    "fan_in_medium": 1,
                    "generic_endpoint_penalty": -1,
                },
            },
            "must_test": must_test,
            "risk_based": risk_based,
            "excluded_indirect_count": max(0, len(ranked_unique) - len(risk_based)),
        }
    
    def get_workitem_children(self, workitem_id, commit_id=None):
        """Get child WorkItems for a parent (optionally filtered by commit)"""
        if workitem_id not in self.workitems_data:
            return []
        
        parent = self.workitems_data[workitem_id]
        
        # Si un commit_id est fourni, retourner les enfants spécifiques au commit
        if commit_id:
            commit_children = parent.get('children_by_commit', {})
            # Extraire les 8 premiers caractères du commit pour chercher dans le dictionnaire
            commit_short = commit_id[:8] if len(commit_id) > 8 else commit_id
            children_ids = commit_children.get(commit_short, parent.get('children_ids', []))
        else:
            # Sinon, retourner tous les enfants
            children_ids = parent.get('children_ids', [])
        
        children = []
        for child_id in children_ids:
            if child_id in self.workitems_data:
                child = self.workitems_data[child_id]
                children.append({
                    'id': child['id'],
                    'type': child['type'],
                    'titre': child['titre'],
                    'statut': child['statut'],
                    'priorite': child['priorite'],
                    'assigne_a': child.get('assigne_a', ''),
                    'tags': child.get('tags', '')
                })
        
        return children
    
    def get_statistics(self):
        """Get overall system statistics"""
        if not self.driver:
            return {}
        
        try:
            with self.driver.session() as session:
                # Basic counts
                result = session.run("""
                MATCH (n) RETURN COUNT(n) as nodes
                """)
                nodes_count = result.single()['nodes']
                
                result = session.run("""
                MATCH ()-[r]->() RETURN COUNT(r) as relations
                """)
                relations_count = result.single()['relations']
                
                result = session.run("""
                MATCH (c:Commit) RETURN COUNT(c) as count
                """)
                commit_count = result.single()['count'] or 0
                
                result = session.run("""
                MATCH (ms:Microservice) RETURN COUNT(ms) as count
                """)
                microservice_count = result.single()['count'] or 0
                
                result = session.run("""
                MATCH (f:Function) RETURN COUNT(f) as count
                """)
                function_count = result.single()['count'] or 0
                
                result = session.run("""
                MATCH (wi:WorkItem) RETURN COUNT(wi) as count
                """)
                workitem_count = result.single()['count'] or 0
                
                result = session.run("""
                MATCH ()-[r:MODIFIES]->() RETURN COUNT(r) as count
                """)
                modifies_count = result.single()['count'] or 0
                
                result = session.run("""
                MATCH ()-[r:IMPLEMENTS]->() RETURN COUNT(r) as count
                """)
                implements_count = result.single()['count'] or 0
                
                result = session.run("""
                MATCH ()-[r:TOUCHES_FUNCTION]->() RETURN COUNT(r) as count
                """)
                touches_function_count = result.single()['count'] or 0

                result = session.run("""
                MATCH ()-[r:RELATES_TO_COMMIT]->() RETURN COUNT(r) as count
                """)
                relates_to_commit_count = result.single()['count'] or 0

                result = session.run("""
                MATCH ()-[r:LINKED_TO_COMMIT]->() RETURN COUNT(r) as count
                """)
                linked_to_commit_count = result.single()['count'] or 0

                result = session.run("""
                MATCH ()-[r:LINKED_TO_PULL_REQUEST]->() RETURN COUNT(r) as count
                """)
                linked_to_pull_request_count = result.single()['count'] or 0

                result = session.run("""
                MATCH ()-[r:CONTAINS_COMMIT]->() RETURN COUNT(r) as count
                """)
                contains_commit_count = result.single()['count'] or 0
                
                return {
                    "total_nodes": nodes_count,
                    "total_relations": relations_count,
                    "commit_count": commit_count,
                    "microservice_count": microservice_count,
                    "function_count": function_count,
                    "workitem_count": workitem_count,
                    "modifies_count": modifies_count,
                    "implements_count": implements_count,
                    "touches_function_count": touches_function_count,
                    "relates_to_commit_count": relates_to_commit_count,
                    "linked_to_commit_count": linked_to_commit_count,
                    "linked_to_pull_request_count": linked_to_pull_request_count,
                    "contains_commit_count": contains_commit_count
                }
        except Exception as e:
            print(f"Error getting statistics: {e}")
            return {"error": str(e)}
    
    def get_commit_impact(self, commit_id, workitem_id=None):
        """Get impact of a specific commit - IDENTICAL to Neo4j queries"""
        if not self.driver:
            return {"error": "Database not connected"}
        
        try:
            with self.driver.session() as session:
                # MODE 1: Specific WorkItem (EXACT same logic as Neo4j graph)
                if workitem_id:
                    try:
                        workitem_id = int(workitem_id)
                    except:
                        return {"error": f"Invalid WorkItem ID: {workitem_id}"}
                    
                    result = session.run("""
                    MATCH (c:Commit {commit_id: $commit_id})
                    MATCH (wi:WorkItem {id: $workitem_id})
                    WHERE EXISTS { MATCH (wi)-[:LINKED_TO_COMMIT]->(c) }
                       OR EXISTS { MATCH (wi)-[:LINKED_TO_PULL_REQUEST]->(:PullRequest)-[:CONTAINS_COMMIT]->(c) }
                    OPTIONAL MATCH (c)-[:MODIFIES]->(ms:Microservice)
                    OPTIONAL MATCH (c)-[:TOUCHES_FUNCTION]->(f:Function)
                    RETURN
                        c.commit_id as commit_id,
                        c.auteur as author,
                        c.message as message,
                        c.date as date,
                        COLLECT(DISTINCT ms.name) as microservices,
                        COLLECT(DISTINCT {
                            id: f.id,
                            name: COALESCE(f.nom, f.name, f.classe, split(f.id, ':')[-1]),
                            microservice: COALESCE(f.microservice, ms.name, '')
                        }) as functions,
                        COUNT(DISTINCT f.id) as function_count,
                        COUNT(DISTINCT ms.name) as microservice_count,
                        COLLECT(DISTINCT {id: wi.id, titre: wi.titre}) as workitems,
                        COUNT(DISTINCT wi.id) as workitem_count
                    """, commit_id=commit_id, workitem_id=workitem_id)
                    
                    record = result.single()
                    if record:
                        functions = [f for f in (record['functions'] or []) if f and f.get('id')]
                        microservices = [ms for ms in record['microservices'] if ms] if record['microservices'] else []
                        workitems = record['workitems'] or []
                        
                        # Get children of this WorkItem (filtered by commit if available)
                        children_to_test = self.get_workitem_children(workitem_id, commit_id)
                        
                        return {
                            "commit_id": record['commit_id'],
                            "author": record['author'] or "Unknown",
                            "message": record['message'] or "No message",
                            "date": record['date'] or datetime.now().isoformat(),
                            "microservices": [{"name": ms} for ms in microservices],
                            "microservice_count": record['microservice_count'],
                            "functions": functions,
                            "function_count": record['function_count'],
                            "workitems": [{"id": wi['id'], "titre": wi['titre']} for wi in workitems if wi],
                            "workitem_count": record['workitem_count'],
                            "workitem_id": workitem_id,
                            "children_to_test": children_to_test,
                            "children_count": len(children_to_test),
                            "impact_path": f"Commit → Microservice → Function → WorkItem {workitem_id} ✓",
                            "logical_validation": "✓ IDENTICAL to Neo4j (Symbolic AI coherent)",
                            "mode": "SPECIFIC (Neo4j aligned)"
                        }
                    return {"error": f"No impact chain found for commit {commit_id[:8]}... with workitem {workitem_id}"}
                
                # MODE 2: All WorkItems (EXACT same logic as Neo4j, NO LIMIT)
                else:
                    result = session.run("""
                    MATCH (c:Commit {commit_id: $commit_id})
                    OPTIONAL MATCH (c)-[:MODIFIES]->(ms:Microservice)
                    OPTIONAL MATCH (c)-[:TOUCHES_FUNCTION]->(f:Function)
                    OPTIONAL MATCH (wi_direct:WorkItem)-[:LINKED_TO_COMMIT]->(c)
                    OPTIONAL MATCH (wi_pr:WorkItem)-[:LINKED_TO_PULL_REQUEST]->(:PullRequest)-[:CONTAINS_COMMIT]->(c)
                    RETURN
                        c.commit_id as commit_id,
                        c.auteur as author,
                        c.message as message,
                        c.date as date,
                        COLLECT(DISTINCT ms.name) as microservices,
                        COLLECT(DISTINCT {
                            id: f.id,
                            name: COALESCE(f.nom, f.name, f.classe, split(f.id, ':')[-1]),
                            microservice: COALESCE(f.microservice, ms.name, '')
                        }) as functions,
                        COUNT(DISTINCT f.id) as function_count,
                        COUNT(DISTINCT ms.name) as microservice_count,
                        COLLECT(DISTINCT {id: wi_direct.id, titre: wi_direct.titre}) +
                        COLLECT(DISTINCT {id: wi_pr.id, titre: wi_pr.titre}) as workitems
                    """, commit_id=commit_id)
                    
                    record = result.single()
                    if record:
                        functions = [f for f in (record['functions'] or []) if f and f.get('id')]
                        microservices = [ms for ms in record['microservices'] if ms] if record['microservices'] else []
                        workitems_raw = [wi for wi in (record['workitems'] or []) if wi and wi.get('id') is not None]
                        workitems_by_id = {wi['id']: wi for wi in workitems_raw}
                        workitems = list(workitems_by_id.values())
                        
                        return {
                            "commit_id": record['commit_id'],
                            "author": record['author'] or "Unknown",
                            "message": record['message'] or "No message",
                            "date": record['date'] or datetime.now().isoformat(),
                            "microservices": [{"name": ms} for ms in microservices],
                            "microservice_count": record['microservice_count'],
                            "functions": functions,
                            "function_count": record['function_count'],
                            "workitems": [{"id": wi['id'], "titre": wi['titre']} for wi in workitems if wi],
                            "workitem_count": len(workitems),
                            "impact_path": "Commit → Microservice → Function → All WorkItems",
                            "logical_validation": "✓ IDENTICAL to Neo4j (Symbolic AI coherent)",
                            "mode": "FULL (Neo4j aligned, NO LIMITS)"
                        }
                    return {"error": "Commit not found"}
        except Exception as e:
            return {"error": str(e)}
    
    def get_workitem_scope(self, workitem_id):
        """Get complete NRT scope for a WorkItem"""
        if not self.driver:
            return {"error": "Database not connected"}
        
        try:
            workitem_id = int(workitem_id)
            with self.driver.session() as session:
                # Get workitem basic info
                result = session.run("""
                MATCH (wi:WorkItem {id: $workitem_id})
                RETURN wi.titre as title, wi.id as id
                """, workitem_id=workitem_id)
                
                wi_record = result.single()
                if not wi_record:
                    return {"error": "WorkItem not found"}
                
                # Get all related commits through explicit traceability:
                # WorkItem -> Commit OR WorkItem -> PR -> Commit
                result = session.run("""
                MATCH (wi:WorkItem {id: $workitem_id})
                OPTIONAL MATCH (wi)-[:LINKED_TO_COMMIT]->(c_direct:Commit)
                OPTIONAL MATCH (wi)-[:LINKED_TO_PULL_REQUEST]->(:PullRequest)-[:CONTAINS_COMMIT]->(c_pr:Commit)
                WITH COLLECT(DISTINCT c_direct.commit_id) + COLLECT(DISTINCT c_pr.commit_id) as commit_ids
                RETURN [cid IN commit_ids WHERE cid IS NOT NULL] as commits
                """, workitem_id=workitem_id)
                
                record = result.single()
                if record:
                    commits = sorted(set([c for c in (record['commits'] or []) if c]))
                    if commits:
                        scope_result = session.run("""
                        MATCH (c:Commit)
                        WHERE c.commit_id IN $commit_ids
                        OPTIONAL MATCH (c)-[:MODIFIES]->(ms:Microservice)
                        OPTIONAL MATCH (c)-[:TOUCHES_FUNCTION]->(f:Function)
                        RETURN
                            COLLECT(DISTINCT ms.name) as microservices,
                            COLLECT(DISTINCT f.id) as functions,
                            COLLECT(DISTINCT c.auteur) as authors
                        """, commit_ids=commits).single()

                        microservices = sorted(set([ms for ms in (scope_result['microservices'] or []) if ms]))
                        functions = sorted(set([f for f in (scope_result['functions'] or []) if f]))
                        authors = sorted(set([a for a in (scope_result['authors'] or []) if a]))
                    else:
                        microservices = []
                        functions = []
                        authors = []
                    
                    return {
                        "workitem_id": workitem_id,
                        "title": wi_record['title'],
                        "commits": commits,
                        "microservices": microservices,
                        "functions": functions,
                        "authors": authors,
                        "commit_count": len(commits),
                        "microservice_count": len(microservices),
                        "function_count": len(functions),
                        "author_count": len(authors)
                    }
                
                return {"error": "No scope data found"}
        except Exception as e:
            return {"error": str(e)}
    
    def search_workitems(self, query):
        """Search workitems by title"""
        if not self.driver:
            return {"error": "Database not connected"}
        
        try:
            with self.driver.session() as session:
                result = session.run("""
                MATCH (wi:WorkItem)
                WHERE wi.titre CONTAINS $query
                RETURN wi.id as id, wi.titre as title
                LIMIT 10
                """, query=query)
                
                return {
                    "results": [{"id": record['id'], "title": record['title']} for record in result]
                }
        except Exception as e:
            return {"error": str(e)}
    
    def get_workitem_types(self):
        """Get only User Story and Bug types"""
        if not self.workitems_data:
            return {"error": "No WorkItems loaded"}
        
        try:
            types = {"User Story": 0, "Bug": 0}
            
            for wi in self.workitems_data.values():
                wi_type = wi.get('type', '')
                if wi_type in types:
                    types[wi_type] += 1
            
            return {
                "types": [{"name": k, "count": v} for k, v in sorted(types.items()) if v > 0]
            }
        except Exception as e:
            return {"error": str(e)}
    
    def get_workitems_by_type(self, workitem_type):
        """Get all WorkItems filtered by type"""
        if not self.workitems_data:
            return {"error": "No WorkItems loaded"}
        
        try:
            filtered = []
            for wi in self.workitems_data.values():
                if wi.get('type', '').lower() == workitem_type.lower():
                    filtered.append({
                        'id': wi['id'],
                        'titre': wi['titre'],
                        'type': wi.get('type', ''),
                        'statut': wi.get('statut', ''),
                        'priorite': wi.get('priorite', 0),
                        'assigne_a': wi.get('assigne_a', '')
                    })
            
            filtered.sort(key=lambda x: x['id'])
            
            return {
                "type": workitem_type,
                "count": len(filtered),
                "results": filtered
            }
        except Exception as e:
            return {"error": str(e)}
    
    def get_nrt_scope(self, workitem_id):
        """NEW ARCHITECTURE: Get complete NRT scope for a WorkItem
        INPUT: WorkItem ID only
        OUTPUT: Direct + Indirect Microservices with Functions"""
        if not self.driver:
            return {"error": "Database not connected"}
        
        try:
            workitem_id = int(workitem_id)
            
            # 1. Verify WorkItem exists
            with self.driver.session() as session:
                result = session.run("""
                MATCH (wi:WorkItem {id: $wi_id})
                RETURN wi.id as id, wi.titre as title, wi.type as type
                """, wi_id=workitem_id)
                
                wi_record = result.single()
                if not wi_record:
                    return {"error": f"WorkItem {workitem_id} not found"}
            
            # 2. Find DIRECT Microservices
            direct_ms = self.find_direct_microservices_with_functions(workitem_id)

            # 2b. Enrich direct scope from commit-level evidence to reduce false negatives.
            direct_before_ms = set(ms.get("name") for ms in direct_ms if ms.get("name"))
            direct_before_fn = set()
            for ms in direct_ms:
                for func in ms.get("functions", []) or []:
                    if isinstance(func, dict) and func.get("id"):
                        direct_before_fn.add(func["id"])

            if not STRICT_CODE_ONLY:
                direct_ms = self.enrich_direct_microservices_with_commit_evidence(workitem_id, direct_ms)

            direct_after_ms = set(ms.get("name") for ms in direct_ms if ms.get("name"))
            direct_after_fn = set()
            for ms in direct_ms:
                for func in ms.get("functions", []) or []:
                    if isinstance(func, dict) and func.get("id"):
                        direct_after_fn.add(func["id"])

            commit_evidence_added_ms = len(direct_after_ms - direct_before_ms)
            commit_evidence_added_functions = len(direct_after_fn - direct_before_fn)
            
            # 3. Indirect scope via architecture dependencies from direct microservices.
            indirect_ms = self.find_indirect_microservices_with_functions(workitem_id, direct_ms)
            
            # 4. Calculate statistics
            # Keep total_functions_raw aligned with Neo4j distinct semantics across the whole scope.
            function_ids = set()
            for ms in direct_ms + indirect_ms:
                for func in ms.get("functions", []) or []:
                    func_id = func.get("id") if isinstance(func, dict) else None
                    if func_id:
                        function_ids.add(func_id)
            total_functions_raw = len(function_ids)

            insufficient_reasons = []
            if not direct_ms:
                insufficient_reasons.append("no_direct_links_via_traceability_or_commit_trace")
            if direct_ms and not any(ms.get("functions") for ms in direct_ms):
                insufficient_reasons.append("direct_microservices_without_functions")
            insufficient_data = len(direct_ms) == 0 and len(indirect_ms) == 0
            empty_scope_diagnostics = self._build_empty_scope_diagnostics(workitem_id) if insufficient_data else {}
            tester_scope = self._build_tester_scope(wi_record, direct_ms, indirect_ms)
            test_plan = self._build_prioritized_test_plan(workitem_id, direct_ms, indirect_ms)

            direct_function_ids = set()
            for ms in direct_ms:
                for func in ms.get("functions", []) or []:
                    if isinstance(func, dict) and func.get("id"):
                        direct_function_ids.add(func["id"])
            indirect_ranked_count = len(test_plan.get("risk_based", []) or [])
            total_functions_for_testing = len(direct_function_ids) + indirect_ranked_count
            
            return {
                "status": "SUCCESS",
                "workitem": {
                    "id": wi_record['id'],
                    "title": wi_record['title'],
                    "type": wi_record['type']
                },
                "direct_microservices": direct_ms,
                "indirect_microservices": indirect_ms,
                "summary": {
                    "direct_ms_count": len(direct_ms),
                    "indirect_ms_count": len(indirect_ms),
                    "total_ms_count": len(direct_ms) + len(indirect_ms),
                    "total_functions": total_functions_for_testing,
                    "total_functions_raw": total_functions_raw,
                    "direct_touched_functions": len(direct_function_ids),
                    "indirect_ranked_functions": indirect_ranked_count,
                    "commit_evidence_added_ms": commit_evidence_added_ms,
                    "commit_evidence_added_functions": commit_evidence_added_functions,
                    "fallback_source": None,
                    "heuristics_enabled": False,
                    "insufficient_data": insufficient_data,
                    "insufficient_data_reasons": insufficient_reasons,
                    "empty_scope_diagnostics": empty_scope_diagnostics,
                    "estimated_test_cases": len(direct_ms) + len(indirect_ms) * 2 + total_functions_for_testing
                },
                "tester_scope": tester_scope,
                "test_plan": test_plan
            }
        except Exception as e:
            return {"error": str(e)}

    def find_direct_microservices_by_commit_message_reference(self, workitem_id):
        """Infer direct microservices/functions from commit messages referencing the WorkItem ID."""
        if not self.driver:
            return []

        try:
            wi_id = str(workitem_id).strip()
            if not wi_id:
                return []

            refs = [
                f"ab#{wi_id}",
                f"wi {wi_id}",
                f"work item {wi_id}",
                f"workitem {wi_id}",
            ]

            direct_ms = {}
            with self.driver.session() as session:
                result = session.run("""
                MATCH (c:Commit)-[:MODIFIES]->(ms:Microservice)-[:IMPLEMENTS]->(f:Function)
                WHERE any(ref IN $refs WHERE toLower(COALESCE(c.message, '')) CONTAINS ref)
                RETURN DISTINCT
                    ms.name as ms_name,
                    COLLECT(DISTINCT {
                        id: f.id,
                        name: COALESCE(f.name, f.nom, f.classe, split(f.id, ':')[-1])
                    }) as functions
                ORDER BY ms_name
                """, refs=refs)

                for record in result:
                    ms_name = record.get("ms_name")
                    if not ms_name:
                        continue

                    functions = [f for f in (record.get("functions") or []) if f.get("id")]

                    direct_ms[ms_name] = {
                        "name": ms_name,
                        "type": "DIRECT_COMMIT_MESSAGE_REF",
                        "functions": functions,
                        "function_count": len(functions),
                        "test_cases_to_generate": len(functions),
                    }

            return list(direct_ms.values())
        except Exception as e:
            print(f"Error in find_direct_microservices_by_commit_message_reference: {e}")
            return []

    def _extract_workitem_keywords(self, workitem):
        """Extract normalized keywords from WorkItem title/tags/description."""
        text_parts = [
            str(workitem.get('title') or ''),
            str(workitem.get('titre') or ''),
            str(workitem.get('description') or ''),
            str(workitem.get('tags') or ''),
            str(workitem.get('area_path') or ''),
        ]
        raw = ' '.join(text_parts).lower()
        raw = raw.translate(str.maketrans({c: ' ' for c in string.punctuation}))

        stopwords = {
            'the', 'and', 'for', 'with', 'from', 'into', 'when', 'where', 'then', 'that',
            'this', 'have', 'has', 'are', 'does', 'not', 'dont', 'cannot', 'cant', 'bug',
            'wk', 'workitem', 'work', 'item', 'section', 'screen', 'mesx', 'production',
            'story', 'user', 'issue', 'rows', 'job', 'new', 'old', 'data', 'displayed',
            'selected', 'appear', 'appears', 'empty', 'update', 'updated',
        }

        words = []
        for token in raw.split():
            token = token.strip()
            if not token or len(token) < 4:
                continue
            if token in stopwords:
                continue
            words.append(token)

        # Keep distinct keywords while preserving order, and bound query complexity.
        dedup = []
        seen = set()
        for w in words:
            if w not in seen:
                seen.add(w)
                dedup.append(w)
        return dedup[:8]

    def find_direct_microservices_by_workitem_text_signature(self, workitem):
        """Infer direct microservices/functions from WorkItem text (title/tags/description)."""
        if not self.driver:
            return []

        try:
            keywords = self._extract_workitem_keywords(workitem)
            if not keywords:
                return []

            ms_map = {}
            with self.driver.session() as session:
                # 1) Function-name signature match
                fn_result = session.run("""
                MATCH (ms:Microservice)-[:IMPLEMENTS]->(f:Function)
                WITH ms, f,
                     toLower(COALESCE(f.name, f.nom, f.classe, split(f.id, ':')[-1], '')) AS fn_name,
                     $keywords AS keywords
                WHERE any(k IN keywords WHERE fn_name CONTAINS k)
                RETURN ms.name AS ms_name,
                       COLLECT(DISTINCT {
                           id: f.id,
                           name: COALESCE(f.name, f.nom, f.classe, split(f.id, ':')[-1])
                       }) AS functions
                """, keywords=keywords)

                for record in fn_result:
                    ms_name = record.get('ms_name')
                    if not ms_name:
                        continue
                    functions = [f for f in (record.get('functions') or []) if f.get('id')]
                    ms_map[ms_name] = {
                        "name": ms_name,
                        "type": "DIRECT_TEXT_SIGNATURE",
                        "functions": functions,
                        "function_count": len(functions),
                        "test_cases_to_generate": len(functions),
                    }

                # 2) Commit-message keyword fallback (adds MS/functions when function text is too sparse)
                commit_result = session.run("""
                MATCH (c:Commit)-[:MODIFIES]->(ms:Microservice)-[:IMPLEMENTS]->(f:Function)
                WITH c, ms, f, toLower(COALESCE(c.message, '')) AS msg, $keywords AS keywords
                WHERE any(k IN keywords WHERE msg CONTAINS k)
                RETURN ms.name AS ms_name,
                       COLLECT(DISTINCT {
                           id: f.id,
                           name: COALESCE(f.name, f.nom, f.classe, split(f.id, ':')[-1])
                       }) AS functions
                """, keywords=keywords)

                for record in commit_result:
                    ms_name = record.get('ms_name')
                    if not ms_name:
                        continue
                    functions = [f for f in (record.get('functions') or []) if f.get('id')]

                    if ms_name not in ms_map:
                        ms_map[ms_name] = {
                            "name": ms_name,
                            "type": "DIRECT_TEXT_SIGNATURE",
                            "functions": [],
                            "function_count": 0,
                            "test_cases_to_generate": 0,
                        }

                    existing = {f.get('id') for f in ms_map[ms_name]['functions'] if f.get('id')}
                    for func in functions:
                        if func['id'] not in existing:
                            ms_map[ms_name]['functions'].append(func)
                            existing.add(func['id'])

                    ms_map[ms_name]['function_count'] = len(ms_map[ms_name]['functions'])
                    ms_map[ms_name]['test_cases_to_generate'] = len(ms_map[ms_name]['functions'])

            # Rank by number of matched functions and keep top plausible direct scope.
            ranked = sorted(ms_map.values(), key=lambda x: x.get('function_count', 0), reverse=True)
            return ranked[:5]
        except Exception as e:
            print(f"Error in find_direct_microservices_by_workitem_text_signature: {e}")
            return []

    def enrich_direct_microservices_with_commit_evidence(self, workitem_id, direct_ms_list):
        """Augment direct scope using WorkItem→Commit→Function evidence when available."""
        if not self.driver:
            return direct_ms_list

        try:
            ms_map = {}
            for ms in direct_ms_list:
                name = ms.get("name")
                if not name:
                    continue
                cloned = dict(ms)
                cloned["functions"] = list(ms.get("functions", []) or [])
                cloned["evidence_sources"] = ["traceability_links"]
                ms_map[name] = cloned

            with self.driver.session() as session:
                result = session.run("""
                MATCH (wi:WorkItem {id: $wi_id})
                OPTIONAL MATCH (wi)-[:LINKED_TO_COMMIT]->(:Commit)-[:TOUCHES_FUNCTION]->(f_direct:Function)
                OPTIONAL MATCH (wi)-[:LINKED_TO_PULL_REQUEST]->(:PullRequest)-[:CONTAINS_COMMIT]->(:Commit)-[:TOUCHES_FUNCTION]->(f_pr:Function)
                WITH COLLECT(DISTINCT f_direct) + COLLECT(DISTINCT f_pr) as touched_functions
                UNWIND touched_functions as f
                WITH DISTINCT f
                WHERE f IS NOT NULL AND coalesce(f.type, '') <> 'artifact'
                OPTIONAL MATCH (ms:Microservice)-[:IMPLEMENTS]->(f)
                RETURN DISTINCT
                    ms.name as ms_name,
                    COLLECT(DISTINCT {
                        id: f.id,
                        name: COALESCE(f.name, f.nom, f.classe, split(f.id, ':')[-1])
                    }) as functions
                """, wi_id=workitem_id)

                for record in result:
                    ms_name = record.get("ms_name")
                    if not ms_name:
                        continue

                    functions = [f for f in (record.get("functions") or []) if f.get("id")]

                    if ms_name not in ms_map:
                        ms_map[ms_name] = {
                            "name": ms_name,
                            "type": "DIRECT_COMMIT_EVIDENCE",
                            "functions": [],
                            "function_count": 0,
                            "test_cases_to_generate": 0,
                            "evidence_sources": ["commit_trace"]
                        }
                    elif "commit_trace" not in ms_map[ms_name].get("evidence_sources", []):
                        ms_map[ms_name].setdefault("evidence_sources", []).append("commit_trace")

                    existing = {f.get("id") for f in ms_map[ms_name].get("functions", []) if f.get("id")}
                    for func in functions:
                        if func["id"] not in existing:
                            ms_map[ms_name]["functions"].append(func)
                            existing.add(func["id"])

                    ms_map[ms_name]["function_count"] = len(ms_map[ms_name]["functions"])
                    ms_map[ms_name]["test_cases_to_generate"] = len(ms_map[ms_name]["functions"])

            return sorted(ms_map.values(), key=lambda x: x.get("name", ""))
        except Exception as e:
            print(f"Error in enrich_direct_microservices_with_commit_evidence: {e}")
            return direct_ms_list
    
    def find_direct_microservices_with_functions(self, workitem_id):
        """Find DIRECT microservices for a WorkItem from strict traceability links."""
        if not self.driver:
            return []
        
        try:
            direct_ms = {}
            
            with self.driver.session() as session:
                result = session.run("""
                MATCH (wi:WorkItem {id: $wi_id})
                OPTIONAL MATCH (wi)-[:LINKED_TO_COMMIT]->(c_direct:Commit)
                OPTIONAL MATCH (wi)-[:LINKED_TO_PULL_REQUEST]->(:PullRequest)-[:CONTAINS_COMMIT]->(c_pr:Commit)
                WITH COLLECT(DISTINCT c_direct) + COLLECT(DISTINCT c_pr) as commits
                UNWIND commits as c
                WITH DISTINCT c
                WHERE c IS NOT NULL
                MATCH (c)-[:MODIFIES]->(ms:Microservice)
                OPTIONAL MATCH (c)-[:TOUCHES_FUNCTION]->(f:Function)
                WITH c, ms, f
                WHERE f IS NULL OR coalesce(f.type, '') <> 'artifact'
                OPTIONAL MATCH (ms)-[:IMPLEMENTS]->(f)
                RETURN DISTINCT ms.name as ms_name,
                      COLLECT(DISTINCT {
                           id: f.id,
                           name: COALESCE(f.name, f.nom, f.classe, split(f.id, ':')[-1])
                       }) as functions,
                      COLLECT(DISTINCT c.commit_id) as commit_ids
                ORDER BY ms_name
                """, wi_id=workitem_id)
                
                for record in result:
                    ms_name = record['ms_name']
                    functions = [f for f in (record['functions'] or []) if f and f.get('id')]
                    commit_ids = [cid for cid in (record.get('commit_ids') or []) if cid]
                    
                    direct_ms[ms_name] = {
                        "name": ms_name,
                        "type": "DIRECT_TRACEABILITY",
                        "commit_ids": commit_ids,
                        "commit_count": len(commit_ids),
                        "functions": functions,
                        "function_count": len(functions),
                        "test_cases_to_generate": len(functions)
                    }
            
            return list(direct_ms.values())
        except Exception as e:
            print(f"Error in find_direct_microservices_with_functions: {e}")
            return []
    
    def find_indirect_microservices_with_functions(self, workitem_id, direct_ms_list):
        """Find INDIRECT microservices via dependencies (API_CALLS, MESSAGE_BROKER, INGESTION)"""
        if not self.driver:
            return []
        
        try:
            indirect_ms = {}
            
            # Get list of direct MS names
            direct_ms_names = [ms["name"] for ms in direct_ms_list]
            
            with self.driver.session() as session:
                for ms_name in direct_ms_names:
                    # Find dependencies from this MS
                    result = session.run("""
                    MATCH (ms1:Microservice {name: $ms_name})-[rel:API_CALLS|MESSAGE_BROKER|INGESTION]->(ms2:Microservice)
                    WITH ms2, type(rel) as dep_type
                    WHERE dep_type IN $allowed_dep_types
                    OPTIONAL MATCH (other:Microservice)-[:API_CALLS|MESSAGE_BROKER|INGESTION]->(ms2)
                    WITH ms2, dep_type, count(DISTINCT other) as inbound_ms_count
                    OPTIONAL MATCH (ms2)-[:IMPLEMENTS]->(f:Function)
                    WITH ms2, dep_type, inbound_ms_count, f
                    WHERE f IS NULL OR coalesce(f.type, '') <> 'artifact'
                    RETURN DISTINCT ms2.name as target_ms, 
                           dep_type as dep_type,
                           inbound_ms_count as inbound_ms_count,
                              COLLECT(DISTINCT {
                               id: f.id,
                               name: COALESCE(f.name, f.nom, f.classe, split(f.id, ':')[-1]),
                               http_method: coalesce(f.http_method, ''),
                               path: coalesce(f.path, ''),
                               type: coalesce(f.type, '')
                           }) as functions
                    """, ms_name=ms_name, allowed_dep_types=NRT_ALLOWED_DEP_TYPES)
                    
                    for record in result:
                        target_ms = record['target_ms']
                        
                        # Avoid duplicates: store only once
                        if target_ms not in indirect_ms:
                            functions = [f for f in (record['functions'] or []) if f['id']]
                            if NRT_INDIRECT_BUSINESS_ONLY:
                                functions = [f for f in functions if self._is_business_function(f)]
                            
                            indirect_ms[target_ms] = {
                                "name": target_ms,
                                "type": "INDIRECT",
                                "source_microservice": ms_name,
                                "dependency_type": record['dep_type'],
                                "inbound_ms_count": record.get('inbound_ms_count') or 0,
                                "functions": functions,
                                "function_count": len(functions),
                                "test_cases_to_generate": len(functions)
                            }
            
            return list(indirect_ms.values())
        except Exception as e:
            print(f"Error in find_indirect_microservices_with_functions: {e}")
            return []

    def _build_empty_scope_diagnostics(self, workitem_id):
        """
        Provide deterministic diagnostics when a WorkItem has zero direct/indirect scope.
        This helps detect ID confusion (same numeric ID used by PullRequest and WorkItem).
        """
        if not self.driver:
            return {}

        diagnostics = {
            "workitem_links": {
                "linked_commit_count": 0,
                "linked_pr_count": 0,
                "linked_pr_commit_count": 0,
            },
            "same_id_pull_request": None,
        }

        try:
            with self.driver.session() as session:
                wi_links = session.run(
                    """
                    MATCH (wi:WorkItem {id: $wi_id})
                    OPTIONAL MATCH (wi)-[:LINKED_TO_COMMIT]->(c:Commit)
                    OPTIONAL MATCH (wi)-[:LINKED_TO_PULL_REQUEST]->(pr:PullRequest)
                    OPTIONAL MATCH (pr)-[:CONTAINS_COMMIT]->(prc:Commit)
                    RETURN COUNT(DISTINCT c) AS linked_commit_count,
                           COUNT(DISTINCT pr) AS linked_pr_count,
                           COUNT(DISTINCT prc) AS linked_pr_commit_count
                    """,
                    wi_id=workitem_id,
                ).single()

                if wi_links:
                    diagnostics["workitem_links"] = {
                        "linked_commit_count": int(wi_links["linked_commit_count"] or 0),
                        "linked_pr_count": int(wi_links["linked_pr_count"] or 0),
                        "linked_pr_commit_count": int(wi_links["linked_pr_commit_count"] or 0),
                    }

                pr_same_id = session.run(
                    """
                    MATCH (pr:PullRequest {pr_id: $wi_id})
                    OPTIONAL MATCH (pr)<-[:LINKED_TO_PULL_REQUEST]-(wi:WorkItem)
                    OPTIONAL MATCH (pr)-[:CONTAINS_COMMIT]->(c:Commit)-[:MODIFIES]->(ms:Microservice)
                    RETURN pr.pr_id AS pr_id,
                           COLLECT(DISTINCT wi.id) AS linked_workitems,
                           COUNT(DISTINCT c) AS commit_count,
                           COLLECT(DISTINCT ms.name) AS microservices
                    """,
                    wi_id=workitem_id,
                ).single()

                if pr_same_id and pr_same_id["pr_id"] is not None:
                    diagnostics["same_id_pull_request"] = {
                        "pr_id": int(pr_same_id["pr_id"]),
                        "linked_workitems": sorted(
                            int(w) for w in (pr_same_id["linked_workitems"] or []) if w is not None
                        ),
                        "commit_count": int(pr_same_id["commit_count"] or 0),
                        "microservices": sorted(
                            ms for ms in (pr_same_id["microservices"] or []) if ms
                        ),
                        "hint": "This ID exists as PullRequest. Verify WI vs PR ID selection.",
                    }
        except Exception as e:
            diagnostics["error"] = str(e)

        return diagnostics

# Initialize analyzer
analyzer = NRTAnalyzer()

class NRTRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler for NRT system"""
    
    def do_GET(self):
        """Handle GET requests"""
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        query_params = urllib.parse.parse_qs(parsed_path.query)
        
        # API endpoints
        if path == '/api/statistics':
            self.send_json_response(analyzer.get_statistics())
        elif path == '/api/workitem-types':
            self.send_json_response(analyzer.get_workitem_types())
        elif path.startswith('/api/workitems-by-type/'):
            workitem_type = urllib.parse.unquote(path.split('/api/workitems-by-type/')[-1])
            self.send_json_response(analyzer.get_workitems_by_type(workitem_type))
        elif path == '/api/nrt-scope' or path.startswith('/api/nrt-scope/'):
            # NEW: NRT Scope by WorkItem ID only
            workitem_id = query_params.get('workitem_id', [None])[0]
            if not workitem_id:
                workitem_id = path.split('/api/nrt-scope/')[-1] if '/api/nrt-scope/' in path else None
            
            if workitem_id:
                self.send_json_response(analyzer.get_nrt_scope(workitem_id))
            else:
                self.send_json_response({"error": "Missing workitem_id parameter"})
        elif path.startswith('/api/commit/'):
            commit_id = path.split('/api/commit/')[-1]
            workitem_id = query_params.get('workitem', [None])[0]
            self.send_json_response(analyzer.get_commit_impact(commit_id, workitem_id))
        elif path.startswith('/api/workitem/'):
            workitem_id = path.split('/api/workitem/')[-1]
            self.send_json_response(analyzer.get_workitem_scope(workitem_id))
        elif path.startswith('/api/search'):
            query = query_params.get('q', [''])[0]
            self.send_json_response(analyzer.search_workitems(query))
        elif path == '/' or path == '/index.html':
            self.serve_template('templates/dashboard.html')
        elif path == '/commit-analysis':
            self.serve_template('templates/commit_analysis.html')
        elif path == '/workitem-scope':
            self.serve_template('templates/workitem_scope.html')
        elif path == '/graph-visualization':
            self.serve_template('templates/graph_visualization.html')
        elif path == '/reports':
            self.serve_template('templates/reports.html')
        elif path.startswith('/templates/'):
            file_path = path.lstrip('/')
            self.serve_file(file_path)
        elif path.startswith('/static/'):
            file_path = path.lstrip('/')
            self.serve_file(file_path)
        else:
            self.send_error(404, 'Not found')
    
    def serve_template(self, template_path):
        """Serve an HTML template"""
        try:
            full_path = (BASE_DIR / template_path).resolve()
            if full_path.exists():
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(content.encode('utf-8'))
            else:
                self.send_error(404, f'Template not found: {template_path}')
        except Exception as e:
            self.send_error(500, str(e))
    
    def serve_file(self, file_path):
        """Serve a static file"""
        try:
            full_path = (BASE_DIR / file_path).resolve()
            if not str(full_path).startswith(str(BASE_DIR.resolve())):
                self.send_error(403, 'Forbidden')
                return
            if full_path.exists() and full_path.is_file():
                mime_type, _ = mimetypes.guess_type(str(full_path))
                with open(full_path, 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-type', mime_type or 'application/octet-stream')
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_error(404)
        except Exception as e:
            self.send_error(500, str(e))
    
    def send_json_response(self, data):
        """Send JSON response"""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))
    
    def log_message(self, format, *args):
        """Custom logging"""
        print(f"[{self.log_date_time_string()}] {format % args}")
    
    def end_headers(self):
        """Add CORS headers"""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

if __name__ == '__main__':
    PORT = 5000
    Handler = NRTRequestHandler
    
    try:
        with socketserver.TCPServer(("", PORT), Handler) as httpd:
            print("")
            print("============================================================")
            print("[OK] NRT Scope Visualization System - Server Started")
            print("============================================================")
            print(f"Server: http://localhost:{PORT}")
            print("Pages:")
            print(f"  - Dashboard: http://localhost:{PORT}/")
            print(f"  - Commit Analysis: http://localhost:{PORT}/commit-analysis")
            print(f"  - WorkItem Scope: http://localhost:{PORT}/workitem-scope")
            print(f"  - Graph Visualization: http://localhost:{PORT}/graph-visualization")
            print(f"  - Reports: http://localhost:{PORT}/reports")
            print("API Endpoints:")
            print("  - GET /api/statistics")
            print("  - GET /api/commit/<commit_id>")
            print("  - GET /api/workitem/<workitem_id>")
            print("  - GET /api/search?q=<query>")
            print("Press Ctrl+C to stop the server")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Server stopped")
    except Exception as e:
        print(f"[ERROR] {e}")
