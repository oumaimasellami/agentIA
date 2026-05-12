#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build the Neo4j graph using real Azure DevOps traceability whenever available.

Target model:
  (:WorkItem)-[:LINKED_TO_PULL_REQUEST]->(:PullRequest)
  (:WorkItem)-[:LINKED_TO_COMMIT]->(:Commit)
  (:PullRequest)-[:CONTAINS_COMMIT]->(:Commit)
  (:Commit)-[:MODIFIES]->(:Microservice)
  (:Commit)-[:TOUCHES_FUNCTION]->(:Function)
  (:Microservice)-[:IMPLEMENTS]->(:Function)

No heuristic relation is used for NRT scope computation.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from neo4j import GraphDatabase

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent

# Always load the project-level .env, independent of the current working directory.
load_dotenv(dotenv_path=REPO_ROOT / ".env")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j2026!")
STRICT_CODE_ONLY = os.getenv("STRICT_CODE_ONLY", "true").strip().lower() in {"1", "true", "yes", "on"}
STRICT_AZURE_ONLY = os.getenv("STRICT_AZURE_ONLY", "true").strip().lower() in {"1", "true", "yes", "on"}
GRAPH_DATA_CANDIDATES = [
    REPO_ROOT / "01_EXTRACTION" / "graph_data.json",
    BASE_DIR / "graph_data.json",
    REPO_ROOT / "04_VISUALIZATION" / "graph_data.json",
]
COMMITS_FILE = REPO_ROOT / "01_EXTRACTION" / "commits.json"
PULL_REQUESTS_FILE = REPO_ROOT / "01_EXTRACTION" / "pull_requests.json"
BROKER_INGESTION_FILE = REPO_ROOT / "01_EXTRACTION" / "broker_ingestion_relations.json"
WORK_ITEMS_FILE = REPO_ROOT / "01_EXTRACTION" / "work_items.json"
WORKITEM_DEV_LINKS_FILE = REPO_ROOT / "01_EXTRACTION" / "workitem_dev_links.json"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_modified_files(items: List[Dict[str, Any]]) -> List[str]:
    paths = []
    for item in items or []:
        path = item.get("fichier", "") if isinstance(item, dict) else ""
        if path:
            paths.append(path)
    return sorted(set(paths))


def canonicalize_path(path: str) -> str:
    """
    Deterministic normalization for cross-style path matching.
    Example: downtimeCauseStore.ts == downtime-cause-store.ts
    """
    if not path:
        return ""
    return re.sub(r"[^a-z0-9]", "", path.lower())


class GraphDatabaseBuilder:
    def __init__(self) -> None:
        print("\n" + "=" * 80)
        print("Connecting to Neo4j...")
        print("=" * 80)
        try:
            self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
            self._wait_for_neo4j_ready()
            print(f"Connected to {NEO4J_URI}")
        except Exception as exc:
            print(f"Connection error: {exc}")
            print("Troubleshooting:")
            print("  1. Start Neo4j container: docker start neo4j")
            print("  2. Check logs: docker logs --tail 100 neo4j")
            print("  3. Verify URI/user/password in .env")
            sys.exit(1)

    def _wait_for_neo4j_ready(self, attempts: int = 15, delay_seconds: int = 2) -> None:
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                self.driver.verify_connectivity()
                return
            except Exception as exc:
                last_error = exc
                time.sleep(delay_seconds)
        if last_error is not None:
            raise last_error

    def close(self) -> None:
        self.driver.close()

    def execute_query(self, query: str, params: Dict[str, Any] | None = None) -> List[Any]:
        with self.driver.session() as session:
            return list(session.run(query, params or {}))

    def load_data(self) -> None:
        print("\n" + "=" * 80)
        print("Loading JSON sources...")
        print("=" * 80)

        graph_data_path = next((p for p in GRAPH_DATA_CANDIDATES if p.exists()), None)
        if not graph_data_path:
            raise FileNotFoundError("graph_data.json not found in expected locations")

        graph_data = read_json(graph_data_path, {})
        self.graph_data_path = graph_data_path
        self.microservices = graph_data.get("microservices", [])
        self.functions = graph_data.get("fonctions", [])
        self.api_calls = graph_data.get("appels_api", [])
        self.use_cases = graph_data.get("use_cases", [])
        self.covers = graph_data.get("relations_covers", [])
        self.pull_requests = read_json(PULL_REQUESTS_FILE, graph_data.get("pull_requests", []))
        self.commits = read_json(COMMITS_FILE, graph_data.get("commits", []))
        self.work_items = read_json(WORK_ITEMS_FILE, graph_data.get("work_items", []))
        self.workitem_dev_links = read_json(WORKITEM_DEV_LINKS_FILE, [])

        broker_data = read_json(BROKER_INGESTION_FILE, [])
        self.message_broker_relations = [r for r in broker_data if r.get("type") == "MESSAGE_BROKER"]
        self.ingestion_relations = [
            r for r in broker_data if r.get("type") in {"INGESTION", "DATABASE_INGESTION"}
        ]

        # Strict Azure mode:
        # - keep only broker/ingestion relations explicitly marked STRICT_SOURCE
        # - drop legacy enriched relations lacking strong source evidence
        if STRICT_AZURE_ONLY:
            self.message_broker_relations = [
                r for r in self.message_broker_relations if r.get("evidence_mode") == "STRICT_SOURCE"
            ]
            self.ingestion_relations = [
                r for r in self.ingestion_relations if r.get("evidence_mode") == "STRICT_SOURCE"
            ]

        print(f"Graph data source: {self.graph_data_path}")
        print(f"Microservices: {len(self.microservices)}")
        print(f"Functions: {len(self.functions)}")
        print(f"API calls: {len(self.api_calls)}")
        print(f"Use cases: {len(self.use_cases)}")
        print(f"Commits: {len(self.commits)}")
        print(f"Pull requests: {len(self.pull_requests)}")
        print(f"Work items: {len(self.work_items)}")
        print(f"Work item dev links: {len(self.workitem_dev_links)}")
        if STRICT_AZURE_ONLY:
            print(
                "STRICT_AZURE_ONLY: ON "
                f"(MESSAGE_BROKER={len(self.message_broker_relations)}, "
                f"INGESTION={len(self.ingestion_relations)} strict-source only)"
            )

    def cleanup_database(self) -> None:
        print("\nCleaning database...")
        self.execute_query("MATCH (n) DETACH DELETE n")
        print("Database cleared")

    def create_constraints_and_indexes(self) -> None:
        constraints = [
            ("Microservice", "name"),
            ("Function", "id"),
            ("UseCase", "id"),
            ("Commit", "commit_id"),
            ("PullRequest", "pr_id"),
            ("WorkItem", "id"),
        ]
        for label, prop in constraints:
            self.execute_query(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE")

        indexes = [
            ("Commit", "microservice"),
            ("PullRequest", "microservice"),
            ("Function", "microservice"),
            ("WorkItem", "type"),
        ]
        for label, prop in indexes:
            self.execute_query(f"CREATE INDEX IF NOT EXISTS FOR (n:{label}) ON (n.{prop})")

    def load_microservices(self) -> None:
        query = """
        UNWIND $items AS ms
        CREATE (:Microservice {
            name: ms.name,
            repo_id: ms.id,
            repo_url: coalesce(ms.repo_url, ms.repository_url, ''),
            project: coalesce(ms.project, ''),
            default_branch: coalesce(ms.default_branch, ''),
            size: coalesce(ms.size, 0),
            created_at: $timestamp
        })
        """
        self.execute_query(query, {"items": self.microservices, "timestamp": datetime.now().isoformat()})

    def load_functions(self) -> None:
        function_items = []
        for func in self.functions:
            item = dict(func)
            item["file_key"] = canonicalize_path(str(func.get("fichier", "")))
            function_items.append(item)

        query = """
        UNWIND $items AS func
        CREATE (:Function {
            id: func.id,
            nom: coalesce(func.nom, ''),
            classe: coalesce(func.classe, ''),
            package: coalesce(func.package, ''),
            type: coalesce(func.type, 'unknown'),
            http_method: coalesce(func.http_method, ''),
            path: coalesce(func.path, ''),
            microservice: coalesce(func.microservice, ''),
            fichier: coalesce(func.fichier, ''),
            file_key: coalesce(func.file_key, ''),
            description: coalesce(func.description, ''),
            created_at: $timestamp
        })
        """
        self.execute_query(query, {"items": function_items, "timestamp": datetime.now().isoformat()})

    def load_use_cases(self) -> None:
        if not self.use_cases:
            return
        query = """
        UNWIND $items AS uc
        CREATE (:UseCase {
            id: uc.id,
            work_item_id: uc.work_item_id,
            titre: coalesce(uc.titre, ''),
            description: coalesce(uc.description, ''),
            type: coalesce(uc.type, ''),
            statut: coalesce(uc.statut, ''),
            priorite: coalesce(uc.priorite, ''),
            tags: uc.tags,
            area_path: coalesce(uc.area_path, ''),
            created_at: $timestamp
        })
        """
        self.execute_query(query, {"items": self.use_cases, "timestamp": datetime.now().isoformat()})

    def load_workitems(self) -> None:
        query = """
        UNWIND $items AS wi
        CREATE (:WorkItem {
            id: wi.id,
            titre: coalesce(wi.titre, ''),
            description: coalesce(wi.description, ''),
            type: coalesce(wi.type, ''),
            statut: coalesce(wi.statut, ''),
            priorite: coalesce(wi.priorite, 0),
            area_path: coalesce(wi.area_path, ''),
            iteration_path: coalesce(wi.iteration_path, ''),
            tags: coalesce(wi.tags, ''),
            assigne_a: coalesce(wi.assigne_a, ''),
            date_creation: coalesce(wi.date_creation, ''),
            created_at: $timestamp
        })
        """
        self.execute_query(query, {"items": self.work_items, "timestamp": datetime.now().isoformat()})

    def load_commits(self) -> None:
        commit_items = []
        for commit in self.commits:
            modified_files = normalize_modified_files(commit.get("fichiers_modifies", []))
            commit_items.append({
                "commit_id": commit.get("commit_id", ""),
                "repo_id": commit.get("repo_id", ""),
                "microservice": commit.get("microservice", ""),
                "auteur": commit.get("auteur", ""),
                "email": commit.get("email", ""),
                "date": commit.get("date", ""),
                "message": commit.get("message", ""),
                "url": commit.get("url", ""),
                "remote_url": commit.get("remote_url", ""),
                "modified_files": modified_files,
                "modified_file_keys": sorted(set(canonicalize_path(p) for p in modified_files if p)),
            })

        query = """
        UNWIND $items AS c
        CREATE (:Commit {
            commit_id: c.commit_id,
            repo_id: coalesce(c.repo_id, ''),
            microservice: coalesce(c.microservice, ''),
            auteur: coalesce(c.auteur, ''),
            email: coalesce(c.email, ''),
            date: coalesce(c.date, ''),
            message: coalesce(c.message, ''),
            url: coalesce(c.url, ''),
            remote_url: coalesce(c.remote_url, ''),
            modified_files: coalesce(c.modified_files, []),
            modified_file_keys: coalesce(c.modified_file_keys, [])
        })
        """
        self.execute_query(query, {"items": commit_items})

    def load_pull_requests(self) -> None:
        query = """
        UNWIND $items AS pr
        CREATE (:PullRequest {
            pr_id: pr.pr_id,
            repo_id: coalesce(pr.repo_id, ''),
            microservice: coalesce(pr.microservice, ''),
            titre: coalesce(pr.titre, ''),
            description: coalesce(pr.description, ''),
            auteur: coalesce(pr.auteur, ''),
            statut: coalesce(pr.statut, ''),
            date_creation: coalesce(pr.date_creation, ''),
            date_fermeture: coalesce(pr.date_fermeture, ''),
            source_branch: coalesce(pr.source_branch, ''),
            target_branch: coalesce(pr.target_branch, ''),
            url: coalesce(pr.url, ''),
            merge_status: coalesce(pr.merge_status, ''),
            is_draft: coalesce(pr.is_draft, false)
        })
        """
        self.execute_query(query, {"items": self.pull_requests})

    def create_api_calls_relations(self) -> None:
        if not self.api_calls:
            return
        query = """
        UNWIND $items AS call
        MATCH (source:Microservice {name: call.source})
        MATCH (target:Microservice {name: call.cible})
        MERGE (source)-[r:API_CALLS]->(target)
        ON CREATE SET
            r.via = coalesce(call.via, ''),
            r.detail = coalesce(call.detail, ''),
            r.communication_type = coalesce(call.communication_type, ''),
            r.confidence = coalesce(call.confidence, 0.0),
            r.created_at = $timestamp
        """
        self.execute_query(query, {"items": self.api_calls, "timestamp": datetime.now().isoformat()})

    def create_message_broker_relations(self) -> None:
        if not self.message_broker_relations:
            return
        query = """
        UNWIND $items AS rel
        MATCH (source:Microservice {name: rel.source})
        MATCH (target:Microservice {name: rel.cible})
        MERGE (source)-[r:MESSAGE_BROKER]->(target)
        ON CREATE SET
            r.broker_type = coalesce(rel.broker_type, ''),
            r.topics = coalesce(rel.topics, []),
            r.direction = coalesce(rel.direction, ''),
            r.communication_mode = coalesce(rel.communication_mode, ''),
            r.service_namespace = coalesce(rel.service_namespace, ''),
            r.created_at = $timestamp
        """
        self.execute_query(query, {"items": self.message_broker_relations, "timestamp": datetime.now().isoformat()})

    def create_ingestion_relations(self) -> None:
        if not self.ingestion_relations:
            return
        query = """
        UNWIND $items AS rel
        MATCH (source:Microservice {name: rel.source})
        MATCH (target:Microservice {name: rel.cible})
        MERGE (source)-[r:INGESTION]->(target)
        ON CREATE SET
            r.ingestion_type = coalesce(rel.type, ''),
            r.broker_type = coalesce(rel.broker_type, ''),
            r.topics = coalesce(rel.topics, []),
            r.tables_consumed = coalesce(rel.tables_consumed, []),
            r.direction = coalesce(rel.direction, ''),
            r.communication_mode = coalesce(rel.communication_mode, ''),
            r.frequency = coalesce(rel.frequency, ''),
            r.created_at = $timestamp
        """
        self.execute_query(query, {"items": self.ingestion_relations, "timestamp": datetime.now().isoformat()})

    def create_implements_relations(self) -> None:
        query = """
        UNWIND $items AS func
        MATCH (ms:Microservice {name: func.microservice})
        MATCH (f:Function {id: func.id})
        MERGE (ms)-[r:IMPLEMENTS]->(f)
        ON CREATE SET r.created_at = $timestamp
        """
        self.execute_query(query, {"items": self.functions, "timestamp": datetime.now().isoformat()})

    def create_artifact_functions_for_microservices_without_functions(self) -> None:
        """
        Create function-like artifacts from commit modified files for microservices
        that have no extracted functions. This keeps traceability strict and avoids
        heuristic inference while giving infra/config repos executable scope units.
        """
        if STRICT_CODE_ONLY:
            return
        query = """
        MATCH (ms:Microservice)
        WHERE NOT (ms)-[:IMPLEMENTS]->(:Function)
        WITH collect(ms.name) as ms_without_functions
        MATCH (c:Commit)-[:MODIFIES]->(ms:Microservice)
        WHERE ms.name IN ms_without_functions
        UNWIND coalesce(c.modified_files, []) AS file_path
        WITH ms.name as ms_name, trim(toString(file_path)) as raw_path
        WHERE raw_path <> ''
        WITH ms_name, replace(raw_path, '\\\\', '/') as normalized_path
        WITH ms_name,
             CASE
               WHEN normalized_path STARTS WITH '/' THEN normalized_path
               ELSE '/' + normalized_path
             END as canonical_path
        WITH ms_name, canonical_path, split(canonical_path, '/') as parts
        WITH ms_name, canonical_path, parts[size(parts) - 1] as filename
        MERGE (f:Function {id: ms_name + '::artifact::' + canonical_path})
        ON CREATE SET
            f.nom = filename,
            f.classe = 'Artifact',
            f.package = 'artifact',
            f.type = 'artifact',
            f.http_method = '',
            f.path = canonical_path,
            f.microservice = ms_name,
            f.fichier = canonical_path,
            f.description = 'Function-like artifact derived from commit modified files',
            f.created_at = $timestamp
        WITH ms_name, f
        MATCH (ms:Microservice {name: ms_name})
        MERGE (ms)-[r:IMPLEMENTS]->(f)
        ON CREATE SET
            r.created_at = $timestamp,
            r.source = 'artifact_from_commit_modified_files'
        """
        self.execute_query(query, {"timestamp": datetime.now().isoformat()})

    def create_covers_relations(self) -> None:
        # Explicitly disabled: NRT scope must be driven only by traceability links
        # WorkItem -> PullRequest/Commit -> Commit -> Microservice -> Function.
        return

    def create_modifies_relations(self) -> None:
        query = """
        UNWIND $items AS c
        MATCH (commit:Commit {commit_id: c.commit_id})
        MATCH (ms:Microservice {name: c.microservice})
        MERGE (commit)-[r:MODIFIES]->(ms)
        ON CREATE SET
            r.repo_id = coalesce(c.repo_id, ''),
            r.created_at = $timestamp
        """
        self.execute_query(query, {"items": self.commits, "timestamp": datetime.now().isoformat()})

    def create_contains_commit_relations(self) -> None:
        query = """
        UNWIND $items AS pr
        MATCH (pull:PullRequest {pr_id: pr.pr_id})
        UNWIND coalesce(pr.commit_ids, []) AS commit_id
        MATCH (commit:Commit {commit_id: commit_id})
        MERGE (pull)-[r:CONTAINS_COMMIT]->(commit)
        ON CREATE SET r.created_at = $timestamp
        """
        self.execute_query(query, {"items": self.pull_requests, "timestamp": datetime.now().isoformat()})

    def create_workitem_link_relations(self) -> None:
        if not self.workitem_dev_links:
            return

        pr_query = """
        UNWIND $items AS link
        MATCH (wi:WorkItem {id: link.workitem_id})
        UNWIND coalesce(link.linked_pull_request_ids, []) AS pr_id
        MATCH (pr:PullRequest {pr_id: pr_id})
        MERGE (wi)-[r:LINKED_TO_PULL_REQUEST]->(pr)
        ON CREATE SET r.created_at = $timestamp
        """
        self.execute_query(pr_query, {"items": self.workitem_dev_links, "timestamp": datetime.now().isoformat()})

        commit_query = """
        UNWIND $items AS link
        MATCH (wi:WorkItem {id: link.workitem_id})
        UNWIND coalesce(link.linked_commit_ids, []) AS commit_id
        MATCH (c:Commit {commit_id: commit_id})
        MERGE (wi)-[r:LINKED_TO_COMMIT]->(c)
        ON CREATE SET r.created_at = $timestamp
        """
        self.execute_query(commit_query, {"items": self.workitem_dev_links, "timestamp": datetime.now().isoformat()})

    def create_touches_function_relations(self) -> None:
        query = """
        MATCH (c:Commit)-[:MODIFIES]->(ms:Microservice)
        MATCH (ms)-[:IMPLEMENTS]->(f:Function)
        WHERE size(coalesce(c.modified_files, [])) > 0
          AND (
            f.fichier IN c.modified_files
            OR (
              coalesce(f.file_key, '') <> ''
              AND f.file_key IN coalesce(c.modified_file_keys, [])
            )
          )
        MERGE (c)-[r:TOUCHES_FUNCTION]->(f)
        ON CREATE SET
            r.created_at = $timestamp,
            r.source = 'file_path_match'
        """
        self.execute_query(query, {"timestamp": datetime.now().isoformat()})

    def verify_graph(self) -> bool:
        stats = {
            "Microservice": self.execute_query("MATCH (n:Microservice) RETURN count(n) AS c")[0]["c"],
            "Function": self.execute_query("MATCH (n:Function) RETURN count(n) AS c")[0]["c"],
            "UseCase": self.execute_query("MATCH (n:UseCase) RETURN count(n) AS c")[0]["c"],
            "WorkItem": self.execute_query("MATCH (n:WorkItem) RETURN count(n) AS c")[0]["c"],
            "Commit": self.execute_query("MATCH (n:Commit) RETURN count(n) AS c")[0]["c"],
            "PullRequest": self.execute_query("MATCH (n:PullRequest) RETURN count(n) AS c")[0]["c"],
        }
        relations = {
            "API_CALLS": self.execute_query("MATCH ()-[r:API_CALLS]->() RETURN count(r) AS c")[0]["c"],
            "MESSAGE_BROKER": self.execute_query("MATCH ()-[r:MESSAGE_BROKER]->() RETURN count(r) AS c")[0]["c"],
            "INGESTION": self.execute_query("MATCH ()-[r:INGESTION]->() RETURN count(r) AS c")[0]["c"],
            "IMPLEMENTS": self.execute_query("MATCH ()-[r:IMPLEMENTS]->() RETURN count(r) AS c")[0]["c"],
            "MODIFIES": self.execute_query("MATCH ()-[r:MODIFIES]->() RETURN count(r) AS c")[0]["c"],
            "CONTAINS_COMMIT": self.execute_query("MATCH ()-[r:CONTAINS_COMMIT]->() RETURN count(r) AS c")[0]["c"],
            "LINKED_TO_PULL_REQUEST": self.execute_query("MATCH ()-[r:LINKED_TO_PULL_REQUEST]->() RETURN count(r) AS c")[0]["c"],
            "LINKED_TO_COMMIT": self.execute_query("MATCH ()-[r:LINKED_TO_COMMIT]->() RETURN count(r) AS c")[0]["c"],
            "TOUCHES_FUNCTION": self.execute_query("MATCH ()-[r:TOUCHES_FUNCTION]->() RETURN count(r) AS c")[0]["c"],
        }

        print("\n" + "=" * 80)
        print("Graph verification")
        print("=" * 80)
        for name, count in stats.items():
            print(f"{name:22} {count}")
        for name, count in relations.items():
            print(f"{name:22} {count}")

        return stats["Microservice"] > 0 and stats["Commit"] > 0 and stats["PullRequest"] >= 0

    def build(self) -> None:
        try:
            self.load_data()
            self.cleanup_database()
            self.create_constraints_and_indexes()

            self.load_microservices()
            self.load_functions()
            self.load_use_cases()
            self.load_workitems()
            self.load_commits()
            self.load_pull_requests()

            self.create_api_calls_relations()
            self.create_message_broker_relations()
            self.create_ingestion_relations()
            self.create_implements_relations()
            self.create_modifies_relations()
            self.create_artifact_functions_for_microservices_without_functions()
            self.create_contains_commit_relations()
            self.create_workitem_link_relations()
            self.create_touches_function_relations()

            success = self.verify_graph()

            print("\n" + "=" * 80)
            if success:
                print("Neo4j graph rebuilt successfully")
                print("=" * 80)
                print("Suggested entry queries:")
                print("MATCH (wi:WorkItem)-[:LINKED_TO_PULL_REQUEST]->(pr:PullRequest)-[:CONTAINS_COMMIT]->(c:Commit)-[:MODIFIES]->(ms:Microservice) RETURN wi, pr, c, ms LIMIT 25")
                print("MATCH (wi:WorkItem)-[:LINKED_TO_COMMIT]->(c:Commit)-[:MODIFIES]->(ms:Microservice) RETURN wi, c, ms LIMIT 25")
            else:
                print("Graph build finished with warnings")
                print("=" * 80)
        except Exception as exc:
            print(f"\nBuild failed: {exc}")
            raise
        finally:
            self.close()


if __name__ == "__main__":
    builder = GraphDatabaseBuilder()
    builder.build()
