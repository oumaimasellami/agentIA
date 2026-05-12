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
REPO_ROOT = BASE_DIR.parent
EXTRACTION_DIR = REPO_ROOT / "01_EXTRACTION"
CODE_FILE_EXTENSIONS = {".ts", ".js", ".java", ".vue", ".tsx", ".jsx", ".py"}
INFRA_FILE_EXTENSIONS = {".tf", ".bicep"}
CONFIG_FILE_EXTENSIONS = {".yml", ".yaml", ".json", ".toml", ".ini", ".env", ".xml", ".properties"}

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
        self.commits_index = self._load_commits_index()
        self.function_index_by_ms_file = self._load_function_index()
        self.known_microservices = self._load_known_microservices()
        self.source_snapshot_cache = {}
        print(f"[OK] Loaded commit index: {len(self.commits_index)} commits")
        print(f"[OK] Loaded function file index: {len(self.function_index_by_ms_file)} keys")
        print(f"[OK] Loaded known microservices: {len(self.known_microservices)}")
    
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

    @staticmethod
    def _normalize_repo_path(path_value):
        path = str(path_value or "").replace("\\", "/").strip()
        if not path:
            return ""
        if not path.startswith("/"):
            path = "/" + path
        while "//" in path:
            path = path.replace("//", "/")
        return path

    def _load_commits_index(self):
        try:
            path = EXTRACTION_DIR / "commits.json"
            with path.open("r", encoding="utf-8") as f:
                commits = json.load(f)
            index = {}
            for c in commits or []:
                cid = c.get("commit_id")
                if cid:
                    index[cid] = c
            return index
        except Exception as e:
            print(f"[WARN] Could not load commits index: {e}")
            return {}

    def _load_function_index(self):
        try:
            path = EXTRACTION_DIR / "graph_data.json"
            with path.open("r", encoding="utf-8") as f:
                graph_data = json.load(f)
            out = {}
            for fn in graph_data.get("fonctions", []) or []:
                ms = fn.get("microservice")
                file_path = self._normalize_repo_path(fn.get("fichier"))
                fn_id = fn.get("id")
                if not ms or not file_path or not fn_id:
                    continue
                # Keep only real code files in function index to avoid false positives
                # from config/package files.
                if Path(file_path).suffix.lower() not in CODE_FILE_EXTENSIONS:
                    continue
                key = (ms, file_path)
                out.setdefault(key, []).append({
                    "id": fn_id,
                    "name": fn.get("nom") or fn.get("name") or fn.get("classe") or fn_id,
                    "type": fn.get("type") or "",
                    "http_method": fn.get("http_method") or "",
                    "path": fn.get("path") or "",
                    "line_start": int(fn.get("line_start") or 0),
                    "line_end": int(fn.get("line_end") or 0),
                })
            return out
        except Exception as e:
            print(f"[WARN] Could not load function index: {e}")
            return {}

    def _load_known_microservices(self):
        names = set()
        try:
            path = EXTRACTION_DIR / "microservices.json"
            with path.open("r", encoding="utf-8") as f:
                repos = json.load(f)
            for repo in repos or []:
                name = str(repo.get("name", "")).strip()
                if name:
                    names.add(name)
        except Exception:
            pass
        if not names:
            for c in self.commits_index.values():
                ms = str(c.get("microservice", "")).strip()
                if ms:
                    names.add(ms)
        return names

    def _load_source_snapshot_for_microservice(self, microservice):
        if microservice in self.source_snapshot_cache:
            return self.source_snapshot_cache[microservice]
        try:
            file_name = f"source_{microservice.replace('-', '_')}.json"
            path = REPO_ROOT / file_name
            with path.open("r", encoding="utf-8") as f:
                entries = json.load(f)
            index = {}
            for entry in entries or []:
                p = self._normalize_repo_path(entry.get("chemin"))
                content = entry.get("contenu")
                if p and isinstance(content, str):
                    index[p] = content
            self.source_snapshot_cache[microservice] = index
            return index
        except Exception:
            self.source_snapshot_cache[microservice] = {}
            return {}

    def _get_source_file_content(self, microservice, file_path):
        if not microservice or not file_path:
            return ""
        index = self._load_source_snapshot_for_microservice(microservice)
        return index.get(self._normalize_repo_path(file_path), "")

    @staticmethod
    def _extract_changed_lines(change_item):
        if not isinstance(change_item, dict):
            return []
        raw = (
            change_item.get("changed_lines")
            or change_item.get("changed_new_lines")
            or change_item.get("touched_lines")
            or []
        )
        if not isinstance(raw, list):
            return []
        lines = []
        for value in raw:
            try:
                line = int(value)
            except (TypeError, ValueError):
                continue
            if line > 0:
                lines.append(line)
        return sorted(set(lines))

    @staticmethod
    def _filter_functions_by_changed_lines(functions, changed_lines):
        if not functions:
            return []
        if not changed_lines:
            return list(functions)

        out = []
        for fn in functions:
            start = int(fn.get("line_start") or 0)
            end = int(fn.get("line_end") or 0)
            if start <= 0 or end <= 0 or end < start:
                continue
            if any(start <= line <= end for line in changed_lines):
                out.append(fn)
        return out

    def _extract_infra_blocks_from_content(self, file_path, content):
        if not isinstance(content, str) or not content.strip():
            return []
        ext = Path(file_path).suffix.lower()
        blocks = []

        if ext == ".tf":
            for m in re.finditer(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"', content, flags=re.MULTILINE):
                blocks.append(f'resource.{m.group(1)}.{m.group(2)}')
            for m in re.finditer(r'^\s*module\s+"([^"]+)"', content, flags=re.MULTILINE):
                blocks.append(f'module.{m.group(1)}')
            for m in re.finditer(r'^\s*variable\s+"([^"]+)"', content, flags=re.MULTILINE):
                blocks.append(f'variable.{m.group(1)}')
            if re.search(r'^\s*locals\s*\{', content, flags=re.MULTILINE):
                blocks.append("locals")
            for m in re.finditer(r'^\s*output\s+"([^"]+)"', content, flags=re.MULTILINE):
                blocks.append(f'output.{m.group(1)}')
            for m in re.finditer(r'\b(minReplicas|maxReplicas|appId|appPort|targetPort|ingress|dapr)\b', content):
                blocks.append(f"signal.{m.group(1)}")
            for m in re.finditer(r'name\s*=\s*"([A-Z0-9_]+)"', content):
                blocks.append(f'envvar.{m.group(1)}')
        elif ext in {".yml", ".yaml"}:
            for m in re.finditer(r'^\s*([A-Za-z0-9_.-]+)\s*:', content, flags=re.MULTILINE):
                blocks.append(f'key.{m.group(1)}')
        elif ext == ".json":
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    for key in parsed.keys():
                        blocks.append(f'key.{key}')
            except Exception:
                pass

        dedup = []
        seen = set()
        for b in blocks:
            if b not in seen:
                seen.add(b)
                dedup.append(b)
        return dedup[:40]

    @staticmethod
    def _normalize_service_token(token):
        raw = str(token or "").strip().lower().replace("_", "-")
        if not raw:
            return ""
        for prefix in ("key-", "app-id-", "state-store-name-", "topic-", "topics-"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
        raw = re.sub(r"[^a-z0-9-]", "", raw)
        return raw.strip("-")

    def _resolve_target_microservices(self, commit_ms, file_path, infra_blocks, changed_lines=None):
        targets = set()
        known = self.known_microservices or set()
        if commit_ms:
            targets.add(commit_ms)

        # 1) File-name evidence (infra files like r-ca-admin-ui.tf)
        base = Path(file_path or "").name.lower()
        file_tokens = []
        m = re.match(r"r-ca-([a-z0-9_-]+)\.tf$", base)
        if m:
            file_tokens.append(m.group(1))
        m2 = re.match(r"r-([a-z0-9_-]+)\.tf$", base)
        if m2:
            file_tokens.append(m2.group(1))

        # 2) Block evidence (key.admin_ui, envvar.APP_ID_ADMIN_UI, resource.*.ca_admin_ui)
        # Strict guard: very broad config maps should not fan-out targets without line evidence.
        if base == "_service_versions.json" and not (changed_lines or []):
            return sorted(targets)

        block_tokens = []
        for block in infra_blocks or []:
            b = str(block or "")
            if b.startswith("key."):
                block_tokens.append(b.split(".", 1)[1])
            elif b.startswith("envvar."):
                block_tokens.append(b.split(".", 1)[1])
            elif b.startswith("resource."):
                parts = b.split(".")
                if len(parts) >= 3:
                    block_tokens.append(parts[2])

        for token in file_tokens + block_tokens:
            norm = self._normalize_service_token(token)
            if not norm:
                continue
            candidates = [norm] if norm.startswith("mesx-") else [f"mesx-{norm}"]
            for candidate in candidates:
                if candidate in known:
                    targets.add(candidate)

        return sorted(targets)

    def _extract_code_symbols_from_content(self, file_path, content):
        if not isinstance(content, str) or not content.strip():
            return []
        ext = Path(file_path).suffix.lower()
        symbols = []

        if ext == ".vue":
            script_blocks = re.findall(r"<script\b[^>]*>(.*?)</script>", content, flags=re.IGNORECASE | re.DOTALL)
            content = "\n".join(script_blocks) if script_blocks else content
            ext = ".ts"

        if ext in {".ts", ".js", ".tsx", ".jsx"}:
            patterns = [
                r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)\s*\(",
                r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>",
                r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:async\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*\{",
            ]
            for pat in patterns:
                symbols.extend(re.findall(pat, content, flags=re.MULTILINE))
        elif ext == ".java":
            symbols.extend(re.findall(r"(?:public|protected)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],\s]+\s+([A-Za-z_]\w*)\s*\(", content, flags=re.MULTILINE))

        blacklist = {"if", "for", "while", "switch", "catch", "constructor", "return", "else", "try"}
        dedup = []
        seen = set()
        for s in symbols:
            if not s:
                continue
            n = s.strip()
            if not n or n.lower() in blacklist:
                continue
            if n not in seen:
                seen.add(n)
                dedup.append(n)
        return dedup[:30]

    def _classify_file_type(self, file_path):
        ext = Path(file_path).suffix.lower()
        if ext in CODE_FILE_EXTENSIONS:
            return "code"
        if ext in INFRA_FILE_EXTENSIONS:
            return "infra"
        if ext in CONFIG_FILE_EXTENSIONS:
            return "config"
        return "other"

    def _get_workitem_scope_commits(self, workitem_id):
        if not self.driver:
            return []
        with self.driver.session() as session:
            result = session.run("""
            MATCH (wi:WorkItem {id: $wi_id})
            OPTIONAL MATCH (wi)-[:LINKED_TO_COMMIT]->(c_direct:Commit)
            OPTIONAL MATCH (wi)-[:LINKED_TO_PULL_REQUEST]->(:PullRequest)-[:CONTAINS_COMMIT]->(c_pr:Commit)
            WITH COLLECT(DISTINCT c_direct) + COLLECT(DISTINCT c_pr) as commits
            UNWIND commits as c
            WITH DISTINCT c
            WHERE c IS NOT NULL
            OPTIONAL MATCH (c)-[:MODIFIES]->(ms:Microservice)
            RETURN c.commit_id as commit_id,
                   c.auteur as author,
                   c.date as date,
                   c.message as message,
                   COLLECT(DISTINCT ms.name) as modified_microservices
            ORDER BY c.date DESC
            """, wi_id=workitem_id)
            rows = []
            for r in result:
                cid = r.get("commit_id")
                if cid:
                    rows.append({
                        "commit_id": cid,
                        "author": r.get("author") or "",
                        "date": r.get("date") or "",
                        "message": r.get("message") or "",
                        "modified_microservices": [m for m in (r.get("modified_microservices") or []) if m],
                    })
            return rows

    def _build_commit_trace_for_workitem(self, workitem_id):
        trace_rows = []
        commit_rows = self._get_workitem_scope_commits(workitem_id)
        for row in commit_rows:
            commit_id = row.get("commit_id")
            commit_record = self.commits_index.get(commit_id, {})
            commit_ms = commit_record.get("microservice") or (row.get("modified_microservices") or [None])[0]
            changed_files = []

            for item in commit_record.get("fichiers_modifies", []) or []:
                file_path = self._normalize_repo_path(item.get("fichier"))
                if not file_path:
                    continue
                file_type = self._classify_file_type(file_path)
                file_functions = list(self.function_index_by_ms_file.get((commit_ms, file_path), []))
                changed_lines = self._extract_changed_lines(item)
                line_mode = "line_diff" if changed_lines else "no_line_evidence"
                if file_type == "code":
                    functions = self._filter_functions_by_changed_lines(file_functions, changed_lines)
                    # Strict mode: do not claim touched code functions without changed line evidence.
                    if not changed_lines:
                        functions = []
                else:
                    functions = []
                content = self._get_source_file_content(commit_ms, file_path)
                code_symbols = self._extract_code_symbols_from_content(file_path, content)
                infra_blocks = self._extract_infra_blocks_from_content(file_path, content)
                target_microservices = self._resolve_target_microservices(
                    commit_ms, file_path, infra_blocks, changed_lines=changed_lines
                )
                changed_files.append({
                    "path": file_path,
                    "action": item.get("action") or "edit",
                    "type": file_type,
                    "line_match_mode": line_mode,
                    "changed_lines": changed_lines,
                    "changed_lines_count": len(changed_lines),
                    "all_file_functions_count": len(file_functions),
                    "all_file_functions": file_functions,
                    "touched_functions": functions,
                    "touched_function_count": len(functions),
                    "touched_code_symbols": code_symbols,
                    "touched_code_symbol_count": len(code_symbols),
                    "touched_infra_blocks": infra_blocks,
                    "touched_infra_block_count": len(infra_blocks),
                    "target_microservices": target_microservices,
                    "target_microservice_count": len(target_microservices),
                })

            target_ms = set()
            target_functions = set()
            for f in changed_files:
                for ms in f.get("target_microservices", []) or []:
                    target_ms.add(ms)
                for fn in f.get("touched_functions", []) or []:
                    fn_id = str(fn.get("id") or fn.get("name") or "").strip()
                    if fn_id:
                        target_functions.add(f"{commit_ms}::{fn_id}" if commit_ms else fn_id)

            trace_rows.append({
                "commit_id": commit_id,
                "author": row.get("author") or commit_record.get("auteur") or "",
                "date": row.get("date") or commit_record.get("date") or "",
                "message": row.get("message") or commit_record.get("message") or "",
                "microservice": commit_ms or "",
                "modified_microservices": row.get("modified_microservices") or [],
                "files_changed_count": len(changed_files),
                "files": changed_files,
                "target_microservices": sorted(target_ms),
                "target_microservice_count": len(target_ms),
                "target_functions": sorted(target_functions),
                "target_function_count": len(target_functions),
                "direct_touched_functions_count": sum(f.get("touched_function_count", 0) for f in changed_files),
                "direct_touched_code_symbols_count": sum(f.get("touched_code_symbol_count", 0) for f in changed_files),
                "direct_touched_infra_blocks_count": sum(f.get("touched_infra_block_count", 0) for f in changed_files),
            })
        return trace_rows

    def _build_direct_retest_from_commit_trace(self, commit_trace):
        by_ms = {}
        for commit in commit_trace or []:
            ms_name = (commit.get("microservice") or "").strip()
            if not ms_name:
                continue
            ms_entry = by_ms.setdefault(
                ms_name,
                {
                    "microservice": ms_name,
                    "commit_ids": set(),
                    "files": set(),
                    "code_functions": {},
                    "code_symbols": set(),
                    "infra_blocks": set(),
                },
            )
            if commit.get("commit_id"):
                ms_entry["commit_ids"].add(commit["commit_id"])
            for file_entry in commit.get("files", []) or []:
                path = file_entry.get("path") or ""
                if path:
                    ms_entry["files"].add(path)
                for fn in file_entry.get("touched_functions", []) or []:
                    fn_id = fn.get("id")
                    if not fn_id:
                        continue
                    ms_entry["code_functions"][fn_id] = {
                        "id": fn_id,
                        "name": fn.get("name") or fn_id,
                        "line_start": int(fn.get("line_start") or 0),
                        "line_end": int(fn.get("line_end") or 0),
                    }
                for symbol in file_entry.get("touched_code_symbols", []) or []:
                    if symbol:
                        ms_entry["code_symbols"].add(str(symbol))
                for block in file_entry.get("touched_infra_blocks", []) or []:
                    if block:
                        ms_entry["infra_blocks"].add(str(block))

        out = []
        for ms_name in sorted(by_ms.keys()):
            item = by_ms[ms_name]
            code_functions = sorted(item["code_functions"].values(), key=lambda x: x.get("id", ""))
            out.append(
                {
                    "microservice": ms_name,
                    "commit_count": len(item["commit_ids"]),
                    "files_count": len(item["files"]),
                    "code_functions": code_functions,
                    "code_function_count": len(code_functions),
                    "code_symbols": sorted(item["code_symbols"]),
                    "code_symbol_count": len(item["code_symbols"]),
                    "infra_blocks": sorted(item["infra_blocks"]),
                    "infra_block_count": len(item["infra_blocks"]),
                }
            )
        return out

    def _build_target_retest_from_commit_trace(self, commit_trace):
        by_target = {}
        for commit in commit_trace or []:
            commit_id = (commit.get("commit_id") or "").strip()
            commit_message = str(commit.get("message") or "").strip()
            for file_entry in commit.get("files", []) or []:
                path = (file_entry.get("path") or "").strip()
                touched_functions = file_entry.get("touched_functions", []) or []
                touched_infra_blocks = file_entry.get("touched_infra_blocks", []) or []
                for target_ms in file_entry.get("target_microservices", []) or []:
                    if not target_ms:
                        continue
                    entry = by_target.setdefault(
                        target_ms,
                        {
                            "microservice": target_ms,
                            "commit_ids": set(),
                            "files": set(),
                            "target_functions": {},
                            "code_files": set(),
                            "code_files_without_lines": set(),
                            "infra_blocks": set(),
                            "commit_messages": set(),
                        },
                    )
                    if commit_id:
                        entry["commit_ids"].add(commit_id)
                    if commit_message:
                        entry["commit_messages"].add(commit_message)
                    if path:
                        entry["files"].add(path)
                    if (file_entry.get("type") or "").strip().lower() == "code":
                        entry["code_files"].add(path)
                        if not (file_entry.get("changed_lines") or []):
                            entry["code_files_without_lines"].add(path)
                    # File-level code evidence: only for real code files.
                    if (file_entry.get("type") or "").strip().lower() == "code":
                        for fn in file_entry.get("all_file_functions", []) or []:
                            fn_id = str(fn.get("id") or fn.get("name") or "").strip()
                            if not fn_id:
                                continue
                            fn_entry = entry["target_functions"].setdefault(
                                fn_id,
                                {
                                    "id": fn_id,
                                    "name": fn.get("name") or fn_id,
                                    "line_start": int(fn.get("line_start") or 0),
                                    "line_end": int(fn.get("line_end") or 0),
                                    "_evidence_commits": set(),
                                    "_evidence_files": set(),
                                    "_evidence_lines": set(),
                                },
                            )
                            if commit_id:
                                fn_entry["_evidence_commits"].add(commit_id)
                            if path:
                                fn_entry["_evidence_files"].add(path)
                    for fn in touched_functions:
                        fn_id = str(fn.get("id") or fn.get("name") or "").strip()
                        if not fn_id:
                            continue
                        fn_entry = entry["target_functions"].setdefault(
                            fn_id,
                            {
                                "id": fn_id,
                                "name": fn.get("name") or fn_id,
                                "line_start": int(fn.get("line_start") or 0),
                                "line_end": int(fn.get("line_end") or 0),
                                "_evidence_commits": set(),
                                "_evidence_files": set(),
                                "_evidence_lines": set(),
                            },
                        )
                        if commit_id:
                            fn_entry["_evidence_commits"].add(commit_id)
                        if path:
                            fn_entry["_evidence_files"].add(path)
                        for ln in file_entry.get("changed_lines", []) or []:
                            try:
                                iln = int(ln)
                            except (TypeError, ValueError):
                                continue
                            if iln > 0:
                                fn_entry["_evidence_lines"].add(iln)
                    for block in touched_infra_blocks:
                        if block:
                            entry["infra_blocks"].add(str(block))

        out = []
        for ms_name in sorted(by_target.keys()):
            item = by_target[ms_name]
            fn_values = []
            for raw_fn in item["target_functions"].values():
                norm = dict(raw_fn)
                norm["_evidence_commits"] = sorted(raw_fn.get("_evidence_commits", set()))
                norm["_evidence_files"] = sorted(raw_fn.get("_evidence_files", set()))
                norm["_evidence_lines"] = sorted(raw_fn.get("_evidence_lines", set()))
                fn_values.append(norm)
            fn_values = sorted(fn_values, key=lambda x: x.get("id", ""))
            retest_items = self._build_retest_items_for_target(
                microservice=ms_name,
                files=sorted(item["files"]),
                commit_ids=sorted(item["commit_ids"]),
                commit_messages=sorted(item["commit_messages"]),
                target_functions=fn_values,
                infra_blocks=sorted(item["infra_blocks"]),
            )
            proven_line_count = sum(1 for r in retest_items if r.get("evidence_level") == "TOUCHEE_PROUVEE_LIGNE")
            potential_count = sum(1 for r in retest_items if r.get("evidence_level") == "IMPACT_POTENTIEL_CANDIDATE")
            infra_count = sum(1 for r in retest_items if r.get("evidence_level") == "INFRA_PREUVE_FICHIER")
            top_priority = min([int(r.get("priority_order", 99)) for r in retest_items], default=99)
            out.append(
                {
                    "microservice": ms_name,
                    "commit_count": len(item["commit_ids"]),
                    "files_count": len(item["files"]),
                    "commit_ids": sorted(item["commit_ids"]),
                    "commit_messages": sorted(item["commit_messages"]),
                    "files": sorted(item["files"]),
                    "target_functions": fn_values,
                    "target_function_count": len(fn_values),
                    "code_files_count": len(item["code_files"]),
                    "code_files_without_lines_count": len(item["code_files_without_lines"]),
                    "has_code_without_line_evidence": len(item["code_files_without_lines"]) > 0,
                    "infra_blocks": sorted(item["infra_blocks"]),
                    "infra_block_count": len(item["infra_blocks"]),
                    "proven_line_count": proven_line_count,
                    "potential_candidate_count": potential_count,
                    "infra_retest_count": infra_count,
                    "top_priority_order": top_priority,
                    "retest_items": retest_items,
                }
            )
        out.sort(
            key=lambda x: (
                int(x.get("top_priority_order", 99)),
                -int(x.get("proven_line_count", 0)),
                -int(x.get("potential_candidate_count", 0)),
                str(x.get("microservice", "")),
            )
        )
        return out

    def _describe_function_from_source(self, microservice, file_path, fn, changed_lines):
        content = self._get_source_file_content(microservice, file_path)
        if not content:
            return "Fonction impactée dans ce fichier (source snapshot indisponible pour description fine)."
        lines = content.splitlines()
        start = int(fn.get("line_start") or 0)
        end = int(fn.get("line_end") or 0)
        if start <= 0 or end <= 0 or end < start:
            return "Fonction impactée (bornes ligne invalides)."

        zone_start = max(1, start - 4)
        zone_end = min(len(lines), end + 6)
        zone_lines = lines[zone_start - 1:zone_end]
        zone_text = "\n".join(zone_lines)

        route_match = re.search(r'@(Get|Post|Put|Patch|Delete)\(([^)]*)\)', zone_text)
        api_part = ""
        if route_match:
            method = route_match.group(1).upper()
            route = route_match.group(2).strip().strip("'\"")
            api_part = f"Endpoint {method} {route or '(route non littérale)'}."

        service_call = ""
        svc_match = re.search(r'\bthis\.(\w+)\.(\w+)\s*\(', zone_text)
        if svc_match:
            service_call = f" Appelle {svc_match.group(1)}.{svc_match.group(2)}(...)."

        data_contract = ""
        dto_match = re.search(r'Api(?:Ok|Created|Response)\w*\([^)]*type\s*:\s*([A-Za-z_]\w*)', zone_text)
        if dto_match:
            data_contract = f" Réponse typée via {dto_match.group(1)}."

        # Source-based functional hints from real code body (not commit message heuristics).
        behavior_parts = []
        lower_zone = zone_text.lower()
        if "localstorage" in lower_zone:
            behavior_parts.append("Gère la persistance locale des données utilisateur.")
        if "filter" in lower_zone or "search" in lower_zone:
            behavior_parts.append("Gère la logique de filtre/recherche.")
        if "modal" in lower_zone or "dialog" in lower_zone:
            behavior_parts.append("Pilote l'ouverture/fermeture d'une fenêtre de dialogue.")
        if "delete" in lower_zone or "remove" in lower_zone:
            behavior_parts.append("Prend en charge la suppression d'éléments.")
        if "save" in lower_zone or "persist" in lower_zone:
            behavior_parts.append("Prend en charge l'enregistrement des modifications.")
        if "add" in lower_zone or "create" in lower_zone:
            behavior_parts.append("Prend en charge l'ajout/création d'éléments.")
        if "dispatch(" in lower_zone or "commit(" in lower_zone:
            behavior_parts.append("Met à jour l'état applicatif.")
        if "axios" in lower_zone or "fetch(" in lower_zone or ".get(" in lower_zone or ".post(" in lower_zone:
            behavior_parts.append("Déclenche un appel API.")

        line_part = f"Lignes changées: {', '.join(map(str, changed_lines[:8]))}" if changed_lines else "Lignes changées non disponibles."
        behavior_part = " ".join(behavior_parts[:2]).strip()
        description = " ".join([p for p in [api_part, service_call, data_contract, behavior_part, line_part] if p]).strip()
        return description or "Fonction impactée par les lignes modifiées."

    @staticmethod
    def _extract_first_quoted_v2(value):
        if not isinstance(value, str):
            return ""
        m = re.search(r"""['"]([^'"]+)['"]""", value)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_swagger_field_v2(block, field):
        if not isinstance(block, str):
            return ""
        m = re.search(rf"{field}\s*:\s*['\"]([^'\"]+)['\"]", block, flags=re.IGNORECASE)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_controller_prefix_v2(content):
        if not isinstance(content, str):
            return ""
        m = re.search(r"@Controller\s*\(([^)]*)\)", content, flags=re.IGNORECASE | re.MULTILINE)
        if not m:
            return ""
        return NRTAnalyzer._extract_first_quoted_v2(m.group(1))

    @staticmethod
    def _derive_behavior_from_fn_v2(function_id, zone_text):
        text = f"{function_id} {zone_text}".lower()
        hints = []
        if any(k in text for k in ["click", "onclick", "handleclick"]):
            hints.append("gere une interaction utilisateur (clic/action)")
        if any(k in text for k in ["filter", "search", "query"]):
            hints.append("gere le filtrage/recherche metier")
        if any(k in text for k in ["modal", "dialog", "popup"]):
            hints.append("gere l'ouverture/fermeture de modal")
        if any(k in text for k in ["image", "upload", "file", "blob"]):
            hints.append("gere des donnees media/fichier")
        if any(k in text for k in ["transcription", "speech", "voice", "mic", "record"]):
            hints.append("gere la capture/traitement vocal")
        if any(k in text for k in ["delete", "remove"]):
            hints.append("supprime des elements")
        if any(k in text for k in ["save", "update", "edit", "patch"]):
            hints.append("met a jour des donnees")
        if any(k in text for k in ["add", "create", "insert"]):
            hints.append("cree/ajoute des donnees")
        if "localstorage" in text:
            hints.append("synchronise le stockage local")
        if any(k in text for k in ["axios", "fetch(", ".get(", ".post(", ".put(", ".patch("]):
            hints.append("declenche un appel API")
        return ", ".join(hints[:2]) if hints else ""

    @staticmethod
    def _extract_function_short_name(function_id):
        raw = str(function_id or "").strip()
        if not raw:
            return ""
        # ms::class::method -> method
        parts = raw.split("::")
        return parts[-1].strip() if parts else raw

    @staticmethod
    def _extract_function_container(function_id):
        raw = str(function_id or "").strip()
        parts = raw.split("::")
        if len(parts) >= 3:
            return parts[-2].strip()
        return ""

    def _describe_function_from_source_v2(self, microservice, file_path, fn, changed_lines):
        content = self._get_source_file_content(microservice, file_path)
        if not content:
            return "Fonction impactee (source snapshot indisponible)."

        lines = content.splitlines()
        start = int(fn.get("line_start") or 0)
        end = int(fn.get("line_end") or 0)
        if start <= 0 or end <= 0 or end < start:
            return "Fonction impactee (bornes de lignes invalides)."

        fn_id = str(fn.get("id") or fn.get("name") or "").strip()
        fn_name = self._extract_function_short_name(fn_id)
        fn_container = self._extract_function_container(fn_id)
        controller_prefix = self._extract_controller_prefix_v2(content)
        zone_start = max(1, start - 20)
        zone_end = min(len(lines), end + 20)
        zone_text = "\n".join(lines[zone_start - 1:zone_end])

        route_match = re.search(r"@(Get|Post|Put|Patch|Delete)\s*\(([^)]*)\)", zone_text, flags=re.IGNORECASE)
        method = ""
        route = ""
        if route_match:
            method = route_match.group(1).upper()
            route = self._extract_first_quoted_v2(route_match.group(2))

        api_operation_match = re.search(r"@ApiOperation\s*\(\s*\{(.*?)\}\s*\)", zone_text, flags=re.IGNORECASE | re.DOTALL)
        api_summary = ""
        api_description = ""
        if api_operation_match:
            op_body = api_operation_match.group(1)
            api_summary = self._extract_swagger_field_v2(op_body, "summary")
            api_description = self._extract_swagger_field_v2(op_body, "description")

        response_type = ""
        dto_match = re.search(r"@Api(?:Ok|Created|Response)\s*\(\s*\{(.*?)\}\s*\)", zone_text, flags=re.IGNORECASE | re.DOTALL)
        if dto_match:
            type_match = re.search(r"type\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", dto_match.group(1))
            if type_match:
                response_type = type_match.group(1)

        service_call = ""
        svc_match = re.search(r"\bthis\.(\w+)\.(\w+)\s*\(", zone_text)
        if svc_match:
            service_call = f"appelle {svc_match.group(1)}.{svc_match.group(2)}(...)"

        endpoint_part = ""
        if method:
            full_route = "/".join([p.strip("/") for p in [controller_prefix, route] if p])
            full_route = f"/{full_route}" if full_route else "/"
            endpoint_part = f"Endpoint {method} {full_route}"

        behavior_part = self._derive_behavior_from_fn_v2(fn_name, zone_text)
        line_part = f"lignes changees: {', '.join(map(str, changed_lines[:8]))}" if changed_lines else "lignes changees non disponibles"
        swagger_part = api_summary or api_description

        parts = []
        if fn_name:
            if fn_container:
                parts.append(f"Fonction {fn_name} ({fn_container})")
            else:
                parts.append(f"Fonction {fn_name}")
        if endpoint_part:
            parts.append(endpoint_part)
        if swagger_part:
            parts.append(swagger_part)
        if response_type:
            parts.append(f"reponse typee {response_type}")
        if service_call:
            parts.append(service_call)
        if behavior_part:
            parts.append(behavior_part)
        parts.append(line_part)
        return " | ".join(parts)

    def _describe_function_test_focus_v2(self, function_id, file_path, function_description):
        text = f"{function_id} {file_path} {function_description}".lower()
        checks = []
        if "endpoint get " in text:
            checks.append("Verifier chargement nominal + cas vide + erreurs API.")
        if any(k in text for k in ["endpoint post ", "endpoint put ", "endpoint patch ", "endpoint delete "]):
            checks.append("Verifier validation input, succes de mutation, et gestion erreur.")
        if any(k in text for k in ["filter", "search", "query"]):
            checks.append("Verifier filtres/recherche: criteres, reset, coherence des resultats.")
        if any(k in text for k in ["modal", "dialog", "popup"]):
            checks.append("Verifier ouverture/fermeture modal et actions associees.")
        if any(k in text for k in ["delete", "remove"]):
            checks.append("Verifier suppression: confirmation, succes, et rollback en erreur.")
        if any(k in text for k in ["save", "update", "edit", "patch"]):
            checks.append("Verifier mise a jour: validation, persistence, erreurs.")
        if any(k in text for k in ["image", "upload", "file", "blob"]):
            checks.append("Verifier upload/selection fichier-image: format, taille, rendu, erreur.")
        if any(k in text for k in ["transcription", "voice", "speech", "mic", "record"]):
            checks.append("Verifier parcours vocal: start/stop, transcription, copie/remarque.")
        if any(k in text for k in ["modal", "dialog", "popup"]):
            checks.append("Verifier ouverture/fermeture modal + actions associees.")
        if "localstorage" in text:
            checks.append("Verifier persistence locale et rechargement des donnees.")
        if not checks:
            checks.append("Verifier le parcours metier principal lie a cette fonction.")
        return " ".join(checks[:2])

    def _describe_front_impact_v2(self, function_id, file_path, function_description, microservice):
        text = f"{function_id} {file_path} {function_description} {microservice}".lower()
        impacts = []
        if any(k in text for k in ["endpoint post ", "::create", " add", " save", "insert"]):
            impacts.append("UI ciblee: formulaire de creation/enregistrement (validation des champs, message succes/erreur).")
        if any(k in text for k in ["endpoint put ", "endpoint patch ", "::update", " edit"]):
            impacts.append("UI ciblee: parcours de modification (edition, sauvegarde, rafraichissement des donnees).")
        if any(k in text for k in ["endpoint delete ", "::delete", " remove"]):
            impacts.append("UI ciblee: suppression (confirmation, succes, annulation, gestion erreur).")
        if any(k in text for k in ["endpoint get ", "::find", "::list", "fetch", "query"]):
            impacts.append("UI ciblee: chargement liste/table/cards (nominal, vide, erreur API).")
        if any(k in text for k in ["filter", "search"]):
            impacts.append("UI ciblee: filtre/recherche (criteres, reset, coherence du resultat).")
        if any(k in text for k in ["modal", "dialog", "popup"]):
            impacts.append("UI ciblee: ouverture/fermeture modal et actions associees.")
        if any(k in text for k in ["image", "upload", "file", "blob"]):
            impacts.append("UI ciblee: import/selection fichier-image (format, taille, rendu, erreurs).")
        if any(k in text for k in ["transcription", "voice", "speech", "mic", "record"]):
            impacts.append("UI ciblee: parcours vocal (start/stop, transcription, copie vers remarque).")
        if not impacts:
            if str(microservice or "").endswith("-ui"):
                return "UI ciblee: ecran et parcours principal lies a cette fonction."
            return "UI ciblee: parcours front qui consomme cette API/fonction backend."
        return " ".join(impacts[:2])

    def _describe_infra_front_impact_v2(self, microservice, infra_desc):
        ms = str(microservice or "").lower()
        desc = str(infra_desc or "").lower()
        if ms.endswith("-ui"):
            return "UI ciblee: ouverture ecran, chargement initial, navigation et actions critiques apres changement de config/deploiement."
        if ms.endswith("-backend") or ms.endswith("-api"):
            if "readiness" in desc or "liveness" in desc or "disponibilite" in desc:
                return "UI ciblee: verifier disponibilite du parcours front dependant (chargement, appels API, absence d'erreur 5xx)."
            return "UI ciblee: verifier les parcours front relies a cette API (appel, reponse, message erreur)."
        return "UI ciblee: verifier le parcours nominal de bout en bout lie a ce microservice."

    @staticmethod
    def _extract_first_quoted(value):
        if not isinstance(value, str):
            return ""
        m = re.search(r"""['"]([^'"]+)['"]""", value)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_swagger_field(block, field):
        if not isinstance(block, str):
            return ""
        m = re.search(rf"{field}\s*:\s*['\"]([^'\"]+)['\"]", block, flags=re.IGNORECASE)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_controller_prefix(content):
        if not isinstance(content, str):
            return ""
        m = re.search(r"@Controller\s*\(([^)]*)\)", content, flags=re.IGNORECASE | re.MULTILINE)
        if not m:
            return ""
        return NRTAnalyzer._extract_first_quoted(m.group(1))

    @staticmethod
    def _derive_behavior_from_fn(function_id, zone_text):
        text = f"{function_id} {zone_text}".lower()
        hints = []
        if any(k in text for k in ["click", "onclick", "handleclick"]):
            hints.append("gere une interaction utilisateur (clic/action)")
        if any(k in text for k in ["filter", "search", "query"]):
            hints.append("gere la logique de filtre/recherche")
        if any(k in text for k in ["modal", "dialog", "popup"]):
            hints.append("gere l'ouverture/fermeture de modal")
        if any(k in text for k in ["image", "upload", "file", "blob"]):
            hints.append("gere des donnees media/fichier")
        if any(k in text for k in ["transcription", "speech", "voice", "mic", "record"]):
            hints.append("gere la capture/traitement vocal")
        if any(k in text for k in ["delete", "remove"]):
            hints.append("supprime des elements")
        if any(k in text for k in ["save", "update", "edit", "patch"]):
            hints.append("met a jour des donnees")
        if any(k in text for k in ["add", "create", "insert"]):
            hints.append("cree/ajoute des donnees")
        if "localstorage" in text:
            hints.append("synchronise le stockage local")
        if any(k in text for k in ["axios", "fetch(", ".get(", ".post(", ".put(", ".patch("]):
            hints.append("declenche un appel API")
        return ", ".join(hints[:2]) if hints else ""

    def _describe_function_from_source_v2(self, microservice, file_path, fn, changed_lines):
        content = self._get_source_file_content(microservice, file_path)
        if not content:
            return "Fonction impactee (source snapshot indisponible pour description detaillee)."

        lines = content.splitlines()
        start = int(fn.get("line_start") or 0)
        end = int(fn.get("line_end") or 0)
        if start <= 0 or end <= 0 or end < start:
            return "Fonction impactee (bornes de lignes invalides)."

        fn_id = str(fn.get("id") or fn.get("name") or "").strip()
        fn_name = self._extract_function_short_name(fn_id)
        fn_container = self._extract_function_container(fn_id)
        controller_prefix = self._extract_controller_prefix(content)
        zone_start = max(1, start - 20)
        zone_end = min(len(lines), end + 20)
        zone_text = "\n".join(lines[zone_start - 1:zone_end])

        route_match = re.search(r"@(Get|Post|Put|Patch|Delete)\s*\(([^)]*)\)", zone_text, flags=re.IGNORECASE)
        method = ""
        route = ""
        if route_match:
            method = route_match.group(1).upper()
            route = self._extract_first_quoted(route_match.group(2))

        api_operation_match = re.search(r"@ApiOperation\s*\(\s*\{(.*?)\}\s*\)", zone_text, flags=re.IGNORECASE | re.DOTALL)
        api_summary = ""
        api_description = ""
        if api_operation_match:
            op_body = api_operation_match.group(1)
            api_summary = self._extract_swagger_field(op_body, "summary")
            api_description = self._extract_swagger_field(op_body, "description")

        response_type = ""
        dto_match = re.search(r"@Api(?:Ok|Created|Response)\s*\(\s*\{(.*?)\}\s*\)", zone_text, flags=re.IGNORECASE | re.DOTALL)
        if dto_match:
            type_match = re.search(r"type\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", dto_match.group(1))
            if type_match:
                response_type = type_match.group(1)

        service_call = ""
        svc_match = re.search(r"\bthis\.(\w+)\.(\w+)\s*\(", zone_text)
        if svc_match:
            service_call = f"appelle {svc_match.group(1)}.{svc_match.group(2)}(...)"

        endpoint_part = ""
        if method:
            full_route = "/".join([p.strip("/") for p in [controller_prefix, route] if p])
            full_route = f"/{full_route}" if full_route else "/"
            endpoint_part = f"Endpoint {method} {full_route}"

        behavior_part = self._derive_behavior_from_fn(fn_name, zone_text)
        line_part = f"lignes changees: {', '.join(map(str, changed_lines[:8]))}" if changed_lines else "lignes changees non disponibles"
        swagger_part = api_summary or api_description

        parts = []
        if fn_name:
            if fn_container:
                parts.append(f"Fonction {fn_name} ({fn_container})")
            else:
                parts.append(f"Fonction {fn_name}")
        if endpoint_part:
            parts.append(endpoint_part)
        if swagger_part:
            parts.append(swagger_part)
        if response_type:
            parts.append(f"reponse typee {response_type}")
        if service_call:
            parts.append(service_call)
        if behavior_part:
            parts.append(behavior_part)
        parts.append(line_part)
        return " | ".join(parts)

    def _build_retest_items_for_target(self, microservice, files, commit_ids, commit_messages, target_functions, infra_blocks):
        items = []
        file_hint = ", ".join(files[:2]) if files else ""
        commit_hint = ", ".join([c[:8] for c in commit_ids[:2]]) if commit_ids else ""

        for fn in target_functions or []:
            fn_id = str(fn.get("id") or fn.get("name") or "").strip()
            if not fn_id:
                continue
            evidence_files = fn.get("_evidence_files", [])
            evidence_commits = fn.get("_evidence_commits", [])
            changed_lines = fn.get("_evidence_lines", [])
            file_for_desc = evidence_files[0] if evidence_files else ""
            what = self._describe_function_from_source_v2(
                microservice=microservice,
                file_path=file_for_desc,
                fn=fn,
                changed_lines=changed_lines,
            )
            has_line_evidence = bool(changed_lines)
            why_text = (
                "Touchée prouvée (preuve ligne du diff commit)."
                if has_line_evidence
                else "Impact potentiel (candidate liée au fichier commit, sans preuve ligne)."
            )
            evidence_level = "TOUCHEE_PROUVEE_LIGNE" if has_line_evidence else "IMPACT_POTENTIEL_CANDIDATE"
            priority_order = 1 if has_line_evidence else 2
            test_focus = self._describe_function_test_focus_v2(fn_id, file_for_desc, what)
            front_impact = self._describe_front_impact_v2(fn_id, file_for_desc, what, microservice)

            items.append(
                {
                    "type": "CODE-RETEST",
                    "evidence_level": evidence_level,
                    "priority_order": priority_order,
                    "element": fn_id,
                    "why": why_text,
                    "description": what,
                    "impact_front": front_impact,
                    "what_to_test": test_focus,
                    "evidence": (
                        f"commit(s): {', '.join([c[:8] for c in evidence_commits[:4]])} | "
                        f"file(s): {', '.join(evidence_files[:2])}"
                    ),
                }
            )

        if not target_functions:
            code_files = [f for f in files if Path(f).suffix.lower() in CODE_FILE_EXTENSIONS]
            for file_path in code_files[:6]:
                items.append(
                    {
                        "type": "FILE-RETEST",
                        "evidence_level": "IMPACT_POTENTIEL_CANDIDATE",
                        "priority_order": 2,
                        "element": file_path,
                        "why": "Impact potentiel (fichier code touché, sans preuve ligne->fonction).",
                        "description": "Impact fichier code confirmé par Azure commit+fichier, sans preuve ligne->fonction.",
                        "what_to_test": self._describe_file_impact(file_path),
                        "evidence": f"commit(s): {commit_hint} | file(s): {file_path}",
                    }
                )

        infra_candidate_files = [f for f in files if Path(f).suffix.lower() in {".tf", ".hcl", ".yaml", ".yml", ".json"}]
        if infra_blocks or infra_candidate_files:
            block_text = ", ".join(infra_blocks[:3]) if infra_blocks else "config/deploiement"
            infra_desc, infra_test = self._describe_infra_target_plan(
                microservice=microservice,
                files=files,
                infra_blocks=infra_blocks,
                commit_messages=commit_messages or [],
            )

            items.append(
                {
                    "type": "INFRA-RETEST",
                    "evidence_level": "INFRA_PREUVE_FICHIER",
                    "priority_order": 3,
                    "element": microservice,
                    "why": f"Blocs infra/config touchés: {block_text}",
                    "description": infra_desc,
                    "impact_front": self._describe_infra_front_impact_v2(microservice, infra_desc),
                    "what_to_test": infra_test,
                    "evidence": f"commit(s): {commit_hint} | file(s): {file_hint}",
                }
            )

        items.sort(
            key=lambda r: (
                int(r.get("priority_order", 99)),
                str(r.get("type", "")),
                str(r.get("element", "")),
            )
        )
        return items

    @staticmethod
    def _describe_file_impact(file_path):
        p = (file_path or "").lower()
        name = Path(p).name
        if "store" in p and ("filter" in p or "search" in p):
            return "Valider le filtrage/recherche: saisie critères, reset, persistance, cohérence des résultats."
        if "service" in p or name in {"api.ts", "api.js"}:
            return "Valider les appels API du module: succès, vide, erreur et rendu des données."
        if "component" in p or name.endswith(".vue"):
            return "Valider le rendu UI du composant, les interactions et la navigation liée."
        if "controller" in p:
            return "Valider les endpoints exposés: payload, codes HTTP et gestion d'erreurs."
        if "interceptor" in p:
            return "Valider les traitements transverses (transformations, format/timezone, erreurs)."
        if "module" in p:
            return "Valider le parcours fonctionnel principal du module impacté."
        return "Valider le comportement fonctionnel lié à ce fichier touché (preuve commit+fichier)."

    @staticmethod
    def _describe_function_test_focus(function_id, file_path):
        text = f"{function_id} {file_path}".lower()
        checks = []
        if any(k in text for k in ["filter", "search", "query"]):
            checks.append("Vérifier filtres/recherche: critères, reset, cohérence des résultats.")
        if any(k in text for k in ["modal", "popup", "dialog"]):
            checks.append("Vérifier ouverture/fermeture de modale et actions associées.")
        if any(k in text for k in ["delete", "remove"]):
            checks.append("Vérifier suppression: confirmation, succès, gestion d'erreur.")
        if any(k in text for k in ["save", "update", "edit", "patch"]):
            checks.append("Vérifier mise à jour/enregistrement: validation, succès, rollback en erreur.")
        if any(k in text for k in ["add", "create"]):
            checks.append("Vérifier création/ajout: contrôles de saisie et insertion en liste/table.")
        if any(k in text for k in ["auth", "login", "token"]):
            checks.append("Vérifier authentification/session: accès, refresh token, déconnexion.")
        if any(k in text for k in ["api", "service", "axios", "fetch", "http"]):
            checks.append("Vérifier appel API nominal, cas vide, timeout/erreur, affichage.")
        if any(k in text for k in ["date", "time", "timezone"]):
            checks.append("Vérifier formats date/heure, timezone et bornes.")
        if not checks:
            checks.append("Vérifier le parcours métier lié à cette fonction (entrée, sortie, erreurs).")
        return " ".join(checks[:2])

    def _describe_infra_target_plan(self, microservice, files, infra_blocks, commit_messages):
        low_blocks = " ".join([str(x).lower() for x in (infra_blocks or [])])
        low_msgs = " ".join([str(x).lower() for x in (commit_messages or [])])
        low_files = " ".join([str(x).lower() for x in (files or [])])

        impact = "Configuration de déploiement du microservice modifiée."
        if any(k in low_blocks or k in low_msgs for k in ["minreplicas", "maxreplicas", "scale"]):
            impact = "Risque de disponibilité/élasticité (paramètres scale modifiés)."
        elif any(k in low_blocks for k in ["probe", "readiness", "liveness"]):
            impact = "Risque sur readiness/liveness (santé et redémarrage)."
        elif any(k in low_blocks for k in ["dapr", "appid", "appport"]):
            impact = "Risque de connectivité service (Dapr/appId/appPort)."
        elif any(k in low_blocks or k in low_msgs for k in ["env", "variable", "key."]):
            impact = "Risque de comportement lié aux variables de configuration."
        elif ".tf" in low_files or ".hcl" in low_files:
            impact = "Paramètres Terraform modifiés pour ce service."

        if microservice.endswith("-admin-ui") or "admin-ui" in microservice:
            retest = "Retester accès écran Admin, chargement initial, login/navigation de base et actions critiques UI."
        elif microservice.endswith("-admin-backend") or "admin-backend" in microservice:
            retest = "Retester API health/readiness, login API, endpoint Admin principal et gestion des erreurs."
        elif microservice.endswith("-ui"):
            retest = "Retester écran principal, chargement initial, navigation et actions critiques UI."
        elif microservice.endswith("-backend") or microservice.endswith("-api"):
            retest = "Retester API health/readiness, endpoint principal et gestion des erreurs."
        else:
            retest = "Retester disponibilité du service et parcours nominal de bout en bout."

        return impact, retest

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
            commit_trace = self._build_commit_trace_for_workitem(workitem_id)
            direct_retest_from_commit_trace = self._build_direct_retest_from_commit_trace(commit_trace)
            target_retest_from_commit_trace = self._build_target_retest_from_commit_trace(commit_trace)
            commit_trace_function_count = sum(
                int(c.get("direct_touched_functions_count", 0) or 0) for c in commit_trace
            )
            commit_trace_code_symbol_count = sum(
                int(c.get("direct_touched_code_symbols_count", 0) or 0) for c in commit_trace
            )
            commit_trace_infra_count = sum(
                int(c.get("direct_touched_infra_blocks_count", 0) or 0) for c in commit_trace
            )

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
                    "commit_count": len(commit_trace),
                    "commit_trace_functions": commit_trace_function_count,
                    "commit_trace_code_symbols": commit_trace_code_symbol_count,
                    "commit_trace_infra_blocks": commit_trace_infra_count,
                    "commit_trace_direct_retest_ms_count": len(direct_retest_from_commit_trace),
                    "commit_trace_target_retest_ms_count": len(target_retest_from_commit_trace),
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
                "test_plan": test_plan,
                "commit_trace": commit_trace,
                "direct_retest_from_commit_trace": direct_retest_from_commit_trace,
                "target_retest_from_commit_trace": target_retest_from_commit_trace
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
