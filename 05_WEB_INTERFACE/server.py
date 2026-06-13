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
import secrets
import urllib.parse
import threading
import subprocess
import traceback
from pathlib import Path
import mimetypes
from http import cookies
from neo4j import GraphDatabase
from datetime import datetime, timedelta, timezone
import time
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
PYTHON_BIN = os.getenv("NRT_PYTHON_BIN", r"D:\Python\bin\python.exe")
CODE_FILE_EXTENSIONS = {".ts", ".js", ".java", ".vue", ".tsx", ".jsx", ".py"}
INFRA_FILE_EXTENSIONS = {".tf", ".bicep"}
CONFIG_FILE_EXTENSIONS = {".yml", ".yaml", ".json", ".toml", ".ini", ".env", ".xml", ".properties"}
AUTH_ALLOWED_DOMAIN = str(os.getenv("NRT_LOGIN_ALLOWED_DOMAIN", "forvia.com")).strip().lower()
AUTH_COOKIE_NAME = str(os.getenv("NRT_AUTH_COOKIE_NAME", "nrt_scope_auth")).strip() or "nrt_scope_auth"
AUTH_SESSION_HOURS = max(1, int(str(os.getenv("NRT_AUTH_SESSION_HOURS", "12")).strip() or "12"))
AUTH_SESSIONS_LOCK = threading.Lock()
AUTH_SESSIONS = {}


def _env_flag(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _utc_now():
    return datetime.now(timezone.utc)


def _normalize_auth_email(email):
    value = str(email or "").strip().lower()
    value = re.sub(r"\s+", "", value)
    return value


def _is_allowed_auth_email(email):
    value = _normalize_auth_email(email)
    if not value or "@" not in value:
        return False
    pattern = rf"^[a-z0-9._%+\-]+@{re.escape(AUTH_ALLOWED_DOMAIN)}$"
    return re.match(pattern, value, flags=re.IGNORECASE) is not None


def _prune_auth_sessions():
    now = _utc_now()
    with AUTH_SESSIONS_LOCK:
        expired = [
            token for token, row in AUTH_SESSIONS.items()
            if not isinstance(row, dict) or row.get("expires_at") <= now
        ]
        for token in expired:
            AUTH_SESSIONS.pop(token, None)


def _create_auth_session(email):
    _prune_auth_sessions()
    token = secrets.token_urlsafe(32)
    expires_at = _utc_now() + timedelta(hours=AUTH_SESSION_HOURS)
    with AUTH_SESSIONS_LOCK:
        AUTH_SESSIONS[token] = {
            "email": _normalize_auth_email(email),
            "expires_at": expires_at,
        }
    return token, expires_at


def _get_auth_session_email(token):
    if not token:
        return None
    _prune_auth_sessions()
    with AUTH_SESSIONS_LOCK:
        row = AUTH_SESSIONS.get(str(token))
        if not isinstance(row, dict):
            return None
        if row.get("expires_at") <= _utc_now():
            AUTH_SESSIONS.pop(str(token), None)
            return None
        return str(row.get("email") or "").strip() or None


def _clear_auth_session(token):
    if not token:
        return
    with AUTH_SESSIONS_LOCK:
        AUTH_SESSIONS.pop(str(token), None)


AZURE_WEBHOOK_TOKEN = str(os.getenv("AZURE_WEBHOOK_TOKEN", "")).strip()
WEBHOOK_AUTO_REFRESH_ENABLED = _env_flag("WEBHOOK_AUTO_REFRESH_ENABLED", "true")
WEBHOOK_REFRESH_MODE = str(os.getenv("WEBHOOK_REFRESH_MODE", "backfill")).strip().lower()
WEBHOOK_INCLUDE_STEP3 = _env_flag("WEBHOOK_INCLUDE_STEP3", "true")
WEBHOOK_AZURE_INSECURE_TLS = _env_flag("WEBHOOK_AZURE_INSECURE_TLS", os.getenv("AZURE_INSECURE_TLS", "true"))
WEBHOOK_AZURE_DISABLE_ENV_PROXY = _env_flag("WEBHOOK_AZURE_DISABLE_ENV_PROXY", os.getenv("AZURE_DISABLE_ENV_PROXY", "true"))
WEBHOOK_AZURE_MAX_WORKERS = str(os.getenv("WEBHOOK_AZURE_MAX_WORKERS", os.getenv("AZURE_MAX_WORKERS", "2"))).strip() or "2"
WEBHOOK_AZURE_MAX_RETRIES = str(os.getenv("WEBHOOK_AZURE_MAX_RETRIES", os.getenv("AZURE_MAX_RETRIES", "8"))).strip() or "8"
WEBHOOK_AZURE_REQUEST_DELAY_MS = str(os.getenv("WEBHOOK_AZURE_REQUEST_DELAY_MS", os.getenv("AZURE_REQUEST_DELAY_MS", "250"))).strip() or "250"

WEBHOOK_STATE_LOCK = threading.Lock()
WEBHOOK_STATE = {
    "running": False,
    "last_status": "idle",
    "last_started_at": None,
    "last_finished_at": None,
    "last_trigger": None,
    "last_event_type": None,
    "last_error": None,
    "last_log_tail": [],
}


def _set_webhook_state(**kwargs):
    with WEBHOOK_STATE_LOCK:
        WEBHOOK_STATE.update(kwargs)


def _snapshot_webhook_state():
    with WEBHOOK_STATE_LOCK:
        return dict(WEBHOOK_STATE)


def _run_refresh_command(args, env_vars):
    result = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env_vars,
        timeout=60 * 60,
    )
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    return result.returncode, out, err


def _refresh_pipeline_worker(trigger, event_type, payload):
    _set_webhook_state(
        running=True,
        last_status="running",
        last_started_at=datetime.utcnow().isoformat() + "Z",
        last_finished_at=None,
        last_trigger=trigger,
        last_event_type=event_type,
        last_error=None,
        last_log_tail=[],
    )

    logs = []
    try:
        env_vars = os.environ.copy()
        env_vars["AZURE_EXTRACT_COMMIT_DIFF_LINES"] = "true"
        env_vars["AZURE_INSECURE_TLS"] = "true" if WEBHOOK_AZURE_INSECURE_TLS else "false"
        env_vars["AZURE_DISABLE_ENV_PROXY"] = "true" if WEBHOOK_AZURE_DISABLE_ENV_PROXY else "false"
        env_vars["AZURE_MAX_WORKERS"] = WEBHOOK_AZURE_MAX_WORKERS
        env_vars["AZURE_MAX_RETRIES"] = WEBHOOK_AZURE_MAX_RETRIES
        env_vars["AZURE_REQUEST_DELAY_MS"] = WEBHOOK_AZURE_REQUEST_DELAY_MS
        if WEBHOOK_REFRESH_MODE == "backfill":
            env_vars["BACKFILL_ONLY"] = "true"
        else:
            env_vars.pop("BACKFILL_ONLY", None)

        commands = [
            [PYTHON_BIN, str(EXTRACTION_DIR / "step1_azure_extract.py")],
            [PYTHON_BIN, str(EXTRACTION_DIR / "populate_graph_data.py")],
        ]

        step3_path = EXTRACTION_DIR / "step3_strict_broker_ingestion_extractor.py"
        if WEBHOOK_INCLUDE_STEP3 and step3_path.exists():
            commands.append([PYTHON_BIN, str(step3_path)])

        commands.append([PYTHON_BIN, str(REPO_ROOT / "02_NEO4J_DATABASE" / "build_graph_database.py")])

        for cmd in commands:
            rc, out, err = _run_refresh_command(cmd, env_vars)
            logs.append({
                "command": " ".join(cmd),
                "returncode": rc,
                "stdout_tail": out[-3000:],
                "stderr_tail": err[-3000:],
            })
            if rc != 0:
                raise RuntimeError(f"Refresh command failed ({rc}): {' '.join(cmd)}")

        _set_webhook_state(
            running=False,
            last_status="ok",
            last_finished_at=datetime.utcnow().isoformat() + "Z",
            last_log_tail=logs[-6:],
        )
    except Exception as e:
        logs.append({"exception": str(e), "traceback": traceback.format_exc()[-4000:]})
        _set_webhook_state(
            running=False,
            last_status="error",
            last_finished_at=datetime.utcnow().isoformat() + "Z",
            last_error=str(e),
            last_log_tail=logs[-8:],
        )


def _trigger_webhook_refresh(trigger, event_type, payload):
    if not WEBHOOK_AUTO_REFRESH_ENABLED:
        return False, "WEBHOOK_AUTO_REFRESH_ENABLED=false"

    current = _snapshot_webhook_state()
    if current.get("running"):
        return False, "refresh already running"

    worker = threading.Thread(
        target=_refresh_pipeline_worker,
        args=(trigger, event_type, payload),
        daemon=True,
    )
    worker.start()
    return True, "refresh started"

class NRTAnalyzer:
    """Analyze NRT scopes from Neo4j"""
    
    def __init__(self):
        self.driver = None
        # Neo4j in Docker can expose the port a few seconds before Bolt handshake is fully ready.
        # Retry to avoid noisy false-negative startup failures.
        max_attempts = 8
        retry_delay_sec = 2
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
                driver.verify_connectivity()
                self.driver = driver
                print(f"[OK] Connected to Neo4j database (attempt {attempt}/{max_attempts})")
                break
            except Exception as e:
                last_error = e
                if attempt < max_attempts:
                    print(f"[WARN] Neo4j not ready yet (attempt {attempt}/{max_attempts}): {e}")
                    time.sleep(retry_delay_sec)
                else:
                    print(f"[ERROR] Failed to connect to Neo4j after {max_attempts} attempts: {e}")
                    self.driver = None
        
        # Load workitems with parent-child relationships
        self.workitems_data = self._load_workitems()
        print(f"[OK] Loaded {len(self.workitems_data)} WorkItems from JSON")
        self.workitem_dev_links_index = self._load_workitem_dev_links_index()

        # Build an index of UI screens from source files to provide tester-friendly output
        self.ui_screen_index = self._build_ui_screen_index()
        print(f"[OK] Indexed UI screens for {len(self.ui_screen_index)} microservices")
        self.commits_index = self._load_commits_index()
        self.pull_requests_index = self._load_pull_requests_index()
        self.function_index_by_ms_file = self._load_function_index()
        self.function_code_index = self._load_function_code_index()
        self.repo_inventory_index = self._load_repo_inventory_index()
        self.known_microservices = self._load_known_microservices()
        self.source_snapshot_cache = {}
        self.front_endpoint_usage_index = self._build_front_endpoint_usage_index()
        print(f"[OK] Loaded commit index: {len(self.commits_index)} commits")
        print(f"[OK] Loaded PR index: {len(self.pull_requests_index)} pull requests")
        print(f"[OK] Loaded workitem-dev links index: {len(self.workitem_dev_links_index)} workitems")
        print(f"[OK] Loaded function file index: {len(self.function_index_by_ms_file)} keys")
        print(f"[OK] Loaded function code index: {len(self.function_code_index)} functions")
        print(f"[OK] Loaded repo inventory index: {len(self.repo_inventory_index)} repos")
        print(f"[OK] Loaded known microservices: {len(self.known_microservices)}")
        print(f"[OK] Indexed front endpoint usage: {len(self.front_endpoint_usage_index)} endpoints")
    
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

    def _load_workitem_dev_links_index(self):
        """Load WorkItem -> PR/Commit links index from extraction output."""
        candidate_paths = [
            EXTRACTION_DIR / "workitem_dev_links.json",
            REPO_ROOT / "workitem_dev_links.json",
        ]
        for path in candidate_paths:
            try:
                with path.open("r", encoding="utf-8") as f:
                    items = json.load(f)
                out = {}
                for row in items or []:
                    wi_id = row.get("workitem_id")
                    if wi_id is None:
                        continue
                    try:
                        wi_int = int(wi_id)
                    except Exception:
                        continue
                    out[wi_int] = {
                        "linked_pull_request_ids": [int(x) for x in (row.get("linked_pull_request_ids") or []) if x is not None],
                        "linked_commit_ids": [str(x) for x in (row.get("linked_commit_ids") or []) if x],
                    }
                return out
            except Exception:
                continue
        return {}

    def _load_pull_requests_index(self):
        """Load PR index by PR id for date-based filtering."""
        try:
            path = EXTRACTION_DIR / "pull_requests.json"
            with path.open("r", encoding="utf-8") as f:
                prs = json.load(f)
            out = {}
            for pr in prs or []:
                pr_id = pr.get("pr_id")
                if pr_id is None:
                    continue
                try:
                    pr_int = int(pr_id)
                except Exception:
                    continue
                out[pr_int] = pr
            return out
        except Exception as e:
            print(f"[WARN] Could not load pull requests index: {e}")
            return {}

    @staticmethod
    def _parse_iso_datetime(value):
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _normalize_iteration_path(value):
        text = str(value or "").strip().replace("/", "\\")
        text = re.sub(r"\\{2,}", "\\\\", text)
        return text.strip("\\")

    def _build_iteration_catalog(self):
        """
        Build iteration -> sprint catalog from extracted work_items.json.
        Example:
          iteration: MES_X.0\\mvp
          sprint:    MES_X.0\\mvp\\Sprint MVP 01
        """
        roots = {}
        for wi in self.workitems_data.values():
            raw_path = wi.get("iteration_path") or ""
            norm_path = self._normalize_iteration_path(raw_path)
            if not norm_path:
                continue
            parts = [p for p in norm_path.split("\\") if p]
            if len(parts) < 2:
                continue

            root = "\\".join(parts[:2])
            root_entry = roots.setdefault(root.lower(), {
                "iteration_path": root,
                "workitem_count": 0,
                "sprints": {},
            })
            root_entry["workitem_count"] += 1

            if len(parts) >= 3:
                sprint = "\\".join(parts[:3])
                sprint_entry = root_entry["sprints"].setdefault(sprint.lower(), {
                    "sprint_path": sprint,
                    "workitem_count": 0,
                })
                sprint_entry["workitem_count"] += 1

        iterations = []
        for entry in roots.values():
            sprints = sorted(
                list(entry["sprints"].values()),
                key=lambda x: (x["sprint_path"].lower(), -x["workitem_count"]),
            )
            iterations.append({
                "iteration_path": entry["iteration_path"],
                "workitem_count": entry["workitem_count"],
                "sprint_count": len(sprints),
                "sprints": sprints,
            })

        iterations.sort(key=lambda x: x["iteration_path"].lower())
        return iterations

    def get_iteration_catalog(self):
        catalog = self._build_iteration_catalog()
        return {
            "total_iterations": len(catalog),
            "iterations": catalog,
        }

    def get_workitem_status_catalog(self, iteration_path="", sprint_path="", workitem_type="All"):
        statuses = {}
        iter_norm = self._normalize_iteration_path(iteration_path).lower()
        type_norm = str(workitem_type or "All").strip().lower()
        sprint_tokens = []
        raw_sprint = str(sprint_path or "").strip()
        if raw_sprint and raw_sprint.lower() not in {"all", "all sprints"}:
            if "," in raw_sprint:
                sprint_tokens = [x.strip() for x in raw_sprint.split(",") if x.strip()]
            elif "+" in raw_sprint:
                sprint_tokens = [x.strip() for x in raw_sprint.split("+") if x.strip()]
            else:
                sprint_tokens = [raw_sprint]
        allowed_sprints = {
            self._normalize_iteration_path(token).lower()
            for token in sprint_tokens
            if self._normalize_iteration_path(token)
        }
        allowed_types = None
        if type_norm not in {"all", ""}:
            tokens = []
            if "," in type_norm:
                tokens = [t.strip() for t in type_norm.split(",") if t.strip()]
            elif "+" in type_norm:
                tokens = [t.strip() for t in type_norm.split("+") if t.strip()]
            else:
                tokens = [type_norm]
            normalized = set()
            for t in tokens:
                if t in {"bug", "user story"}:
                    normalized.add(t)
            if normalized:
                allowed_types = normalized

        for wi in self.workitems_data.values():
            wi_type = str(wi.get("type", "")).strip()
            if allowed_types is not None and wi_type.lower() not in allowed_types:
                continue
            wi_iter = self._normalize_iteration_path(wi.get("iteration_path"))
            wi_iter_l = wi_iter.lower()
            if allowed_sprints:
                if wi_iter_l not in allowed_sprints:
                    continue
            elif iter_norm:
                if not (wi_iter_l == iter_norm or wi_iter_l.startswith(iter_norm + "\\")):
                    continue
            raw_status = str(wi.get("statut") or "").strip()
            if not raw_status:
                continue
            key = raw_status.lower()
            entry = statuses.setdefault(key, {
                "status": raw_status,
                "workitem_count": 0,
            })
            entry["workitem_count"] += 1

        rows = sorted(
            list(statuses.values()),
            key=lambda x: (x["status"].lower(), -x["workitem_count"]),
        )
        return {
            "total_statuses": len(rows),
            "statuses": rows,
        }

    def get_workitems_by_iteration(self, iteration_path="", sprint_path="", workitem_type="All", status="All", limit=500):
        """
        Filter WorkItems by iteration/sprint path, type, and status using extracted data.
        """
        if not self.workitems_data:
            return {"error": "No WorkItems loaded"}

        iter_norm = self._normalize_iteration_path(iteration_path).lower()
        type_norm = str(workitem_type or "All").strip().lower()
        status_norm = str(status or "All").strip().lower()
        sprint_tokens = []
        raw_sprint = str(sprint_path or "").strip()
        if raw_sprint and raw_sprint.lower() not in {"all", "all sprints"}:
            if "," in raw_sprint:
                sprint_tokens = [x.strip() for x in raw_sprint.split(",") if x.strip()]
            elif "+" in raw_sprint:
                sprint_tokens = [x.strip() for x in raw_sprint.split("+") if x.strip()]
            else:
                sprint_tokens = [raw_sprint]
        allowed_sprints = {
            self._normalize_iteration_path(token).lower()
            for token in sprint_tokens
            if self._normalize_iteration_path(token)
        }
        allowed_types = None
        if type_norm not in {"all", ""}:
            # Accept single, CSV, or '+' combinations and keep only supported ticket types.
            tokens = []
            if "," in type_norm:
                tokens = [t.strip() for t in type_norm.split(",") if t.strip()]
            elif "+" in type_norm:
                tokens = [t.strip() for t in type_norm.split("+") if t.strip()]
            else:
                tokens = [type_norm]
            normalized = set()
            for t in tokens:
                if t in {"bug", "user story"}:
                    normalized.add(t)
            if normalized:
                allowed_types = normalized

        try:
            max_items = int(limit)
        except Exception:
            max_items = 500
        max_items = max(1, min(max_items, 2000))

        results = []
        for wi in self.workitems_data.values():
            wi_id = wi.get("id")
            if wi_id is None:
                continue
            wi_type = str(wi.get("type", "")).strip()
            if allowed_types is not None and wi_type.lower() not in allowed_types:
                continue
            wi_status = str(wi.get("statut") or "").strip()
            if status_norm not in {"all", ""} and wi_status.lower() != status_norm:
                continue

            wi_iter = self._normalize_iteration_path(wi.get("iteration_path"))
            wi_iter_l = wi_iter.lower()
            if allowed_sprints:
                if wi_iter_l not in allowed_sprints:
                    continue
            elif iter_norm:
                if not (wi_iter_l == iter_norm or wi_iter_l.startswith(iter_norm + "\\")):
                    continue

            links = self.workitem_dev_links_index.get(int(wi_id), {})
            linked_commit_ids = [str(x) for x in (links.get("linked_commit_ids") or []) if x]
            linked_pr_ids = [int(x) for x in (links.get("linked_pull_request_ids") or []) if x is not None]
            results.append({
                "id": int(wi_id),
                "titre": wi.get("titre") or wi.get("title") or "",
                "type": wi_type,
                "statut": wi_status,
                "priorite": wi.get("priorite", 0),
                "assigne_a": wi.get("assigne_a") or "",
                "iteration_path": wi_iter,
                "linked_commit_count": len(linked_commit_ids),
                "linked_pr_count": len(linked_pr_ids),
                "has_dev_activity": len(linked_commit_ids) > 0 or len(linked_pr_ids) > 0,
            })

        results.sort(
            key=lambda x: (
                not x.get("has_dev_activity", False),
                -int(x.get("linked_commit_count", 0)),
                -int(x.get("linked_pr_count", 0)),
                x.get("id", 0),
            )
        )
        sliced = results[:max_items]
        return {
            "iteration_path": iteration_path,
            "sprint_path": sprint_path,
            "selected_sprint_count": len(allowed_sprints),
            "type": workitem_type,
            "status": status,
            "count": len(sliced),
            "total_found": len(results),
            "results": sliced,
        }

    def get_nrt_scope_batch(self, workitem_ids):
        """
        Build consolidated NRT scope for multiple WorkItems:
        - deduplicated microservices/functions
        - retains WorkItem evidence mapping
        """
        if not isinstance(workitem_ids, list) or not workitem_ids:
            return {"error": "workitem_ids is required"}

        wi_ids = []
        for wid in workitem_ids:
            try:
                wi_ids.append(int(wid))
            except Exception:
                continue
        wi_ids = sorted(set(wi_ids))
        if not wi_ids:
            return {"error": "No valid workitem IDs provided"}

        scope_rows = []
        ms_map = {}
        fn_map = {}
        wi_meta = []

        for wi_id in wi_ids:
            result = self.get_nrt_scope(wi_id)
            if result.get("error"):
                continue

            wi_obj = result.get("workitem") or {}
            wi_meta.append({
                "id": wi_obj.get("id", wi_id),
                "title": wi_obj.get("title", ""),
                "type": wi_obj.get("type", ""),
            })

            direct = result.get("direct_microservices") or []
            indirect = result.get("indirect_microservices") or []
            scope_rows.append({
                "workitem": wi_obj,
                "direct_microservices": [{"name": m.get("name"), "function_count": len(m.get("functions") or [])} for m in direct],
                "indirect_microservices": [{"name": m.get("name"), "function_count": len(m.get("functions") or [])} for m in indirect],
                "summary": result.get("summary") or {},
            })

            for group_name, ms_list in (("DIRECT", direct), ("INDIRECT", indirect)):
                for ms in ms_list:
                    ms_name = ms.get("name")
                    if not ms_name:
                        continue
                    key = ms_name.lower()
                    entry = ms_map.setdefault(key, {
                        "name": ms_name,
                        "impact_types": set(),
                        "workitem_ids": set(),
                        "function_ids": set(),
                    })
                    entry["impact_types"].add(group_name)
                    entry["workitem_ids"].add(wi_id)

                    for fn in (ms.get("functions") or []):
                        fn_id = fn.get("id")
                        if not fn_id:
                            continue
                        entry["function_ids"].add(fn_id)
                        fkey = fn_id.lower()
                        fentry = fn_map.setdefault(fkey, {
                            "id": fn_id,
                            "name": fn.get("name") or fn_id.split("::")[-1],
                            "microservices": set(),
                            "workitem_ids": set(),
                        })
                        fentry["microservices"].add(ms_name)
                        fentry["workitem_ids"].add(wi_id)

        microservices = []
        for m in ms_map.values():
            if m["impact_types"] == {"DIRECT"}:
                impact = "DIRECT"
            elif m["impact_types"] == {"INDIRECT"}:
                impact = "INDIRECT"
            else:
                impact = "DIRECT+INDIRECT"
            microservices.append({
                "name": m["name"],
                "impact_type": impact,
                "workitem_count": len(m["workitem_ids"]),
                "workitem_ids": sorted(m["workitem_ids"]),
                "function_count": len(m["function_ids"]),
            })
        microservices.sort(key=lambda x: (-x["workitem_count"], -x["function_count"], x["name"].lower()))

        functions = []
        for f in fn_map.values():
            functions.append({
                "id": f["id"],
                "name": f["name"],
                "microservice_count": len(f["microservices"]),
                "microservices": sorted(f["microservices"]),
                "workitem_count": len(f["workitem_ids"]),
                "workitem_ids": sorted(f["workitem_ids"]),
            })
        functions.sort(key=lambda x: (-x["workitem_count"], -x["microservice_count"], x["id"].lower()))

        return {
            "input_workitem_count": len(wi_ids),
            "scoped_workitem_count": len(scope_rows),
            "workitems": wi_meta,
            "consolidated": {
                "microservice_count": len(microservices),
                "function_count": len(functions),
                "microservices": microservices,
                "functions": functions,
            },
            "workitem_scopes": scope_rows,
        }

    def get_nrt_scope_batch_direct_trace(self, workitem_ids):
        """
        Build consolidated NRT scope for multiple WorkItems using strict direct evidence only:
        - WorkItem -> (linked PR/commit) -> commit files
        - microservices from file target resolution
        - functions from diff line intersection only (touched_functions)
        No indirect dependency propagation.
        """
        if not isinstance(workitem_ids, list) or not workitem_ids:
            return {"error": "workitem_ids is required"}

        wi_ids = []
        for wid in workitem_ids:
            try:
                wi_ids.append(int(wid))
            except Exception:
                continue
        wi_ids = sorted(set(wi_ids))
        if not wi_ids:
            return {"error": "No valid workitem IDs provided"}

        wi_meta = []
        scope_rows = []
        ms_map = {}
        fn_map = {}

        for wi_id in wi_ids:
            wi_obj = self.workitems_data.get(wi_id, {})
            wi_title = str(wi_obj.get("titre") or wi_obj.get("title") or "").strip()
            wi_meta.append({
                "id": wi_id,
                "title": wi_title,
                "type": wi_obj.get("type") or "",
            })

            commit_trace = self._build_commit_trace_for_workitem(wi_id)
            local_ms = {}

            for commit in commit_trace or []:
                commit_id = str(commit.get("commit_id") or "").strip()
                commit_ms = str(commit.get("microservice") or "").strip()

                for file_entry in commit.get("files", []) or []:
                    file_path = str(file_entry.get("path") or "").strip()
                    changed_lines = []
                    for ln in file_entry.get("changed_lines", []) or []:
                        try:
                            iln = int(ln)
                        except Exception:
                            continue
                        if iln > 0:
                            changed_lines.append(iln)

                    target_ms_list = [m for m in (file_entry.get("target_microservices") or []) if m]
                    if not target_ms_list and commit_ms:
                        target_ms_list = [commit_ms]

                    touched_functions = file_entry.get("touched_functions", []) or []
                    all_file_functions = file_entry.get("all_file_functions", []) or []
                    file_type = str(file_entry.get("type") or "").strip().lower()

                    for target_ms in target_ms_list:
                        ms_entry = local_ms.setdefault(target_ms, {
                            "name": target_ms,
                            "commit_ids": set(),
                            "commit_messages": set(),
                            "files": set(),
                            "functions": {},
                        })
                        if commit_id:
                            ms_entry["commit_ids"].add(commit_id)
                        commit_message = str(commit.get("message") or "").strip()
                        if commit_message:
                            ms_entry["commit_messages"].add(commit_message)
                        if file_path:
                            ms_entry["files"].add(file_path)

                        # Strict function evidence: only functions proven by line diff.
                        if file_type != "code":
                            continue
                        proven_ids = set()
                        for fn in touched_functions:
                            fn_id = str(fn.get("id") or fn.get("name") or "").strip()
                            if not fn_id:
                                continue
                            line_start = int(fn.get("line_start") or 0)
                            line_end = int(fn.get("line_end") or 0)
                            evidence_lines = []
                            if line_start > 0 and line_end >= line_start:
                                evidence_lines = [ln for ln in changed_lines if line_start <= ln <= line_end]
                            if not evidence_lines:
                                continue
                            proven_ids.add(fn_id)

                            f_entry = ms_entry["functions"].setdefault(fn_id, {
                                "id": fn_id,
                                "name": fn.get("name") or fn_id.split("::")[-1],
                                "line_start": line_start,
                                "line_end": line_end,
                                "_workitem_ids": set(),
                                "_microservices": set(),
                                "_commit_ids": set(),
                                "_files": set(),
                                "_evidence_lines": set(),
                            })
                            f_entry["_workitem_ids"].add(wi_id)
                            f_entry["_microservices"].add(target_ms)
                            if commit_id:
                                f_entry["_commit_ids"].add(commit_id)
                            if file_path:
                                f_entry["_files"].add(file_path)
                            for ln in evidence_lines:
                                f_entry["_evidence_lines"].add(int(ln))

                        # Candidate functions (P2/P3): any other parsed function in the same
                        # touched code file remains in the NRT scope as a same-file candidate,
                        # even when another function in that file is already proven by line diff.
                        for fn in all_file_functions:
                            fn_id = str(fn.get("id") or fn.get("name") or "").strip()
                            if not fn_id or fn_id in proven_ids:
                                continue
                            line_start = int(fn.get("line_start") or 0)
                            line_end = int(fn.get("line_end") or 0)
                            f_entry = ms_entry["functions"].setdefault(fn_id, {
                                "id": fn_id,
                                "name": fn.get("name") or fn_id.split("::")[-1],
                                "line_start": line_start,
                                "line_end": line_end,
                                "_workitem_ids": set(),
                                "_microservices": set(),
                                "_commit_ids": set(),
                                "_files": set(),
                                "_evidence_lines": set(),
                            })
                            f_entry["_workitem_ids"].add(wi_id)
                            f_entry["_microservices"].add(target_ms)
                            if commit_id:
                                f_entry["_commit_ids"].add(commit_id)
                            if file_path:
                                f_entry["_files"].add(file_path)

            direct_ms_list = []
            for ms_name, ms_data in sorted(local_ms.items(), key=lambda x: x[0].lower()):
                target_fn_payload = []
                fn_values = []
                for raw_fn in ms_data["functions"].values():
                    norm_fn = {
                        "id": raw_fn["id"],
                        "name": raw_fn["name"],
                        "line_start": raw_fn["line_start"],
                        "line_end": raw_fn["line_end"],
                        "evidence_lines": sorted(raw_fn["_evidence_lines"]),
                        "commit_ids": sorted(raw_fn["_commit_ids"]),
                        "files": sorted(raw_fn["_files"]),
                    }
                    fn_values.append(norm_fn)
                    target_fn_payload.append({
                        "id": raw_fn["id"],
                        "name": raw_fn["name"],
                        "line_start": raw_fn["line_start"],
                        "line_end": raw_fn["line_end"],
                        "_evidence_commits": sorted(raw_fn["_commit_ids"]),
                        "_evidence_files": sorted(raw_fn["_files"]),
                        "_evidence_lines": sorted(raw_fn["_evidence_lines"]),
                    })
                fn_values.sort(key=lambda x: x["id"].lower())

                # Reuse the same tester-facing description logic as workitem-scope.
                retest_items = self._build_retest_items_for_target(
                    microservice=ms_name,
                    files=sorted(ms_data["files"]),
                    commit_ids=sorted(ms_data["commit_ids"]),
                    commit_messages=sorted(ms_data["commit_messages"]),
                    target_functions=target_fn_payload,
                    infra_blocks=[],
                )
                retest_by_fn = {}
                for item in retest_items or []:
                    if str(item.get("type") or "").upper() != "CODE-RETEST":
                        continue
                    element = str(item.get("element") or "").strip()
                    if not element:
                        continue
                    retest_by_fn[element] = item

                for fn in fn_values:
                    rt = retest_by_fn.get(fn.get("id"), {})
                    has_line_evidence = bool(fn.get("evidence_lines"))
                    fn["function_scope_type"] = "P1 touchée prouvée" if has_line_evidence else "P2/P3 candidate"
                    fn["priority_order"] = 1 if has_line_evidence else 2
                    fn["description"] = rt.get("description") or "Description métier non disponible."
                    fn["impact_front"] = rt.get("impact_front") or "Impact front non disponible."
                    fn["what_to_test"] = rt.get("what_to_test") or "Retest conseillé non disponible."
                    fn["evidence"] = rt.get("evidence") or (
                        f"commit(s): {', '.join([c[:8] for c in fn.get('commit_ids', [])[:4]])} | "
                        f"file(s): {', '.join(fn.get('files', [])[:2])}"
                    )
                    endpoint_info = self._extract_endpoint_from_description_text(fn.get("description"))
                    fn["endpoint"] = endpoint_info or {}
                    front_eq = []
                    if endpoint_info:
                        front_eq = self._find_front_equivalents_for_endpoint(
                            endpoint_info.get("method"),
                            endpoint_info.get("path"),
                        )
                    fn["frontend_equivalents"] = front_eq
                    fn["frontend_equivalent_count"] = len(front_eq)
                    preferred_file = ""
                    for candidate_path in (fn.get("files") or []):
                        if candidate_path:
                            preferred_file = str(candidate_path)
                            break
                    ui_ctx = self._derive_ui_context_for_function(
                        fn_id=fn.get("id"),
                        microservice=ms_name,
                        file_path=preferred_file,
                        frontend_equivalents=front_eq,
                    )
                    fn["ui_components"] = ui_ctx.get("ui_components", [])
                    fn["ui_files"] = ui_ctx.get("ui_files", [])
                    fn["ui_screens"] = ui_ctx.get("ui_screens", [])
                    fn["ui_labels"] = ui_ctx.get("ui_labels", [])
                    fn["functional_description"] = self._build_functional_description(
                        fn_id=fn.get("id"),
                        microservice=ms_name,
                        file_path=preferred_file,
                    ) or "Description fonctionnelle non disponible."

                fallback_group = None
                if not fn_values:
                    fallback_group = self._build_non_function_scope_group(
                        microservice=ms_name,
                        files=sorted(ms_data["files"]),
                        commit_ids=sorted(ms_data["commit_ids"]),
                        commit_messages=sorted(ms_data["commit_messages"]),
                        retest_items=retest_items,
                        workitem_titles=[wi_title] if wi_title else [],
                    )

                direct_ms_list.append({
                    "name": ms_name,
                    "impact_type": "DIRECT",
                    "commit_count": len(ms_data["commit_ids"]),
                    "files_count": len(ms_data["files"]),
                    "function_count": len(fn_values),
                    "fallback_groups": [],
                    "functions": fn_values,
                })
                if fallback_group:
                    direct_ms_list[-1]["fallback_groups"] = [fallback_group]

                global_ms = ms_map.setdefault(ms_name.lower(), {
                    "name": ms_name,
                    "impact_type": "DIRECT",
                    "workitem_ids": set(),
                    "commit_ids": set(),
                    "files": set(),
                    "function_ids": set(),
                    "fallback_groups": [],
                })
                global_ms["workitem_ids"].add(wi_id)
                global_ms["commit_ids"].update(ms_data["commit_ids"])
                global_ms["files"].update(ms_data["files"])
                global_ms["function_ids"].update([f["id"] for f in fn_values])
                if fallback_group:
                    global_ms["fallback_groups"].append(fallback_group)

                for f in fn_values:
                    gfn = fn_map.setdefault(f["id"].lower(), {
                        "id": f["id"],
                        "name": f["name"],
                        "microservices": set(),
                        "workitem_ids": set(),
                        "commit_ids": set(),
                        "files": set(),
                        "evidence_lines": set(),
                        "description": "",
                        "functional_description": "",
                        "impact_front": "",
                        "what_to_test": "",
                        "evidence_texts": set(),
                        "ui_components": set(),
                        "ui_files": set(),
                        "ui_screens": set(),
                        "ui_labels": set(),
                        "frontend_equivalent_count": 0,
                        "function_scope_type": "P2/P3 candidate",
                        "priority_order": 2,
                    })
                    gfn["microservices"].add(ms_name)
                    gfn["workitem_ids"].add(wi_id)
                    gfn["commit_ids"].update(f.get("commit_ids", []))
                    gfn["files"].update(f.get("files", []))
                    gfn["evidence_lines"].update(f.get("evidence_lines", []))
                    if f.get("description") and not gfn["description"]:
                        gfn["description"] = f.get("description")
                    if f.get("functional_description") and not gfn["functional_description"]:
                        gfn["functional_description"] = f.get("functional_description")
                    if f.get("impact_front") and not gfn["impact_front"]:
                        gfn["impact_front"] = f.get("impact_front")
                    if f.get("what_to_test") and not gfn["what_to_test"]:
                        gfn["what_to_test"] = f.get("what_to_test")
                    if f.get("evidence"):
                        gfn["evidence_texts"].add(str(f.get("evidence")))
                    for comp in (f.get("ui_components") or []):
                        if comp:
                            gfn["ui_components"].add(str(comp))
                    for fp in (f.get("ui_files") or []):
                        if fp:
                            gfn["ui_files"].add(str(fp))
                    for screen in (f.get("ui_screens") or []):
                        if screen:
                            gfn["ui_screens"].add(str(screen))
                    for label in (f.get("ui_labels") or []):
                        if label:
                            gfn["ui_labels"].add(str(label))
                    try:
                        gfn["frontend_equivalent_count"] = max(
                            int(gfn.get("frontend_equivalent_count", 0) or 0),
                            int(f.get("frontend_equivalent_count", 0) or 0),
                        )
                    except Exception:
                        pass
                    if int(f.get("priority_order", 2)) < int(gfn.get("priority_order", 2)):
                        gfn["priority_order"] = int(f.get("priority_order", 2))
                        gfn["function_scope_type"] = f.get("function_scope_type") or gfn.get("function_scope_type")

            scope_rows.append({
                "workitem": {
                    "id": wi_id,
                    "title": wi_obj.get("titre") or wi_obj.get("title") or "",
                    "type": wi_obj.get("type") or "",
                },
                "direct_microservices": [
                    {
                        "name": m["name"],
                        "function_count": m["function_count"],
                        "commit_count": m["commit_count"],
                        "files_count": m["files_count"],
                        "fallback_group_count": len(m.get("fallback_groups") or []),
                    }
                    for m in direct_ms_list
                ],
                "summary": {
                    "commit_count": len(commit_trace or []),
                    "direct_ms_count": len(direct_ms_list),
                    "direct_touched_functions": sum(m["function_count"] for m in direct_ms_list),
                },
            })

        microservices = []
        for ms in ms_map.values():
            fallback_groups = []
            seen_fb = set()
            for group in ms.get("fallback_groups", []) or []:
                key = "|".join([
                    str(group.get("priority_order", "")),
                    str(group.get("description", "")).strip().lower(),
                    str(group.get("functional_description", "")).strip().lower(),
                    str(group.get("what_to_test", "")).strip().lower(),
                ])
                if key in seen_fb:
                    continue
                seen_fb.add(key)
                fallback_groups.append(group)
            microservices.append({
                "name": ms["name"],
                "impact_type": "DIRECT",
                "workitem_count": len(ms["workitem_ids"]),
                "workitem_ids": sorted(ms["workitem_ids"]),
                "commit_count": len(ms["commit_ids"]),
                "files_count": len(ms["files"]),
                "function_count": len(ms["function_ids"]),
                "fallback_groups": fallback_groups,
            })
        microservices.sort(key=lambda x: (-x["workitem_count"], -x["function_count"], x["name"].lower()))

        functions = []
        for fn in fn_map.values():
            functions.append({
                "id": fn["id"],
                "name": fn["name"],
                "microservice_count": len(fn["microservices"]),
                "microservices": sorted(fn["microservices"]),
                "workitem_count": len(fn["workitem_ids"]),
                "workitem_ids": sorted(fn["workitem_ids"]),
                "commit_count": len(fn["commit_ids"]),
                "files_count": len(fn["files"]),
                "files": sorted(fn["files"]),
                "evidence_lines_count": len(fn["evidence_lines"]),
                "function_scope_type": fn.get("function_scope_type") or ("P1 touchée prouvée" if len(fn["evidence_lines"]) > 0 else "P2/P3 candidate"),
                "priority_order": int(fn.get("priority_order", 2)),
                "description": fn.get("description") or "Description métier non disponible.",
                "functional_description": fn.get("functional_description") or "Description fonctionnelle non disponible.",
                "impact_front": fn.get("impact_front") or "Impact front non disponible.",
                "what_to_test": fn.get("what_to_test") or "Retest conseillé non disponible.",
                "ui_components": sorted(fn.get("ui_components") or []),
                "ui_files": sorted(fn.get("ui_files") or []),
                "ui_screens": sorted(fn.get("ui_screens") or []),
                "ui_labels": sorted(fn.get("ui_labels") or []),
                "frontend_equivalent_count": int(fn.get("frontend_equivalent_count", 0) or 0),
                "evidence": " || ".join(sorted(fn.get("evidence_texts") or [])) if fn.get("evidence_texts") else (
                    f"commit(s): {', '.join([c[:8] for c in sorted(fn['commit_ids'])[:4]])} | file(s): {', '.join(sorted(fn['files'])[:2])}"
                ),
            })
        functions.sort(key=lambda x: (int(x.get("priority_order", 9)), x["id"].lower()))

        return {
            "mode": "strict_direct_commit_trace_only",
            "input_workitem_count": len(wi_ids),
            "scoped_workitem_count": len(scope_rows),
            "workitems": wi_meta,
            "consolidated": {
                "microservice_count": len(microservices),
                "function_count": len(functions),
                "microservices": microservices,
                "functions": functions,
            },
            "workitem_scopes": scope_rows,
        }

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

    def _load_function_code_index(self):
        try:
            path = EXTRACTION_DIR / "function_code_index.json"
            with path.open("r", encoding="utf-8") as f:
                rows = json.load(f)
            out = {}
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                fn_id = str(row.get("id", "")).strip()
                if not fn_id:
                    continue
                out[fn_id.lower()] = row
            return out
        except Exception as e:
            print(f"[WARN] Could not load function code index: {e}")
            return {}

    def _load_repo_inventory_index(self):
        try:
            path = EXTRACTION_DIR / "repo_inventory.json"
            with path.open("r", encoding="utf-8") as f:
                rows = json.load(f)
            out = {}
            for repo in rows or []:
                repo_name = str(repo.get("repo_name", "")).strip()
                if not repo_name:
                    continue
                module_views = {}
                for entry in repo.get("entries", []) or []:
                    if entry.get("is_folder"):
                        continue
                    file_path = self._normalize_repo_path(entry.get("path"))
                    if not file_path:
                        continue
                    ext = Path(file_path).suffix.lower()
                    if ext not in {".vue", ".tsx", ".jsx", ".ts", ".js"}:
                        continue
                    if "/views/" in file_path or "/pages/" in file_path:
                        module_name = self._extract_module_name_from_path(file_path)
                        screen_name = self._extract_artifact_name_from_path(file_path)
                        if module_name and screen_name:
                            module_views.setdefault(module_name, set()).add(screen_name)
                out[repo_name] = {
                    "module_views": {k: sorted(v) for k, v in module_views.items()}
                }
            return out
        except Exception as e:
            print(f"[WARN] Could not load repo inventory index: {e}")
            return {}

    @staticmethod
    def _extract_module_name_from_path(file_path):
        raw = str(file_path or "").replace("\\", "/")
        match = re.search(r"/modules/([^/]+)/", raw, flags=re.IGNORECASE)
        return (match.group(1) or "").strip() if match else ""

    @staticmethod
    def _extract_artifact_name_from_path(file_path):
        path = Path(str(file_path or ""))
        stem = path.stem.strip()
        return stem or ""

    @staticmethod
    def _short_code_excerpt(text, max_len=220):
        value = " ".join(str(text or "").split())
        if len(value) <= max_len:
            return value
        return value[: max_len - 3].rstrip() + "..."

    def _build_functional_description(self, fn_id, microservice, file_path):
        row = self.function_code_index.get(str(fn_id or "").lower())
        if not isinstance(row, dict):
            return ""

        function_name = str(row.get("function_name", "")).strip()
        code_excerpt = str(row.get("code_excerpt", "")).strip()
        template_usages = row.get("template_usages", []) or []
        ui_behavior = row.get("ui_behavior", []) or []
        http_method = str(row.get("http_method", "")).strip().upper()
        http_path = str(row.get("http_path", "")).strip()
        module_name = self._extract_module_name_from_path(file_path)
        artifact_name = self._extract_artifact_name_from_path(file_path)
        extension = str(row.get("extension", "")).strip().lower()
        lowered_name = function_name.lower()
        lowered_code = code_excerpt.lower()
        readable_name = function_name or artifact_name or "cette fonction"

        def uniq_keep_order(values):
            out = []
            seen = set()
            for value in values:
                cleaned = str(value or "").strip()
                if not cleaned:
                    continue
                key = cleaned.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(cleaned)
            return out

        verb = ""
        action_label = ""
        if any(k in lowered_name for k in ["delete", "remove", "clear"]):
            verb = "supprimer"
            action_label = "des elements selectionnes"
        elif any(k in lowered_name for k in ["display", "show", "open", "preview"]):
            verb = "afficher"
            action_label = "un element selectionne"
        elif any(k in lowered_name for k in ["create", "add", "insert"]):
            verb = "creer"
            action_label = "une nouvelle donnee"
        elif any(k in lowered_name for k in ["update", "edit", "save", "patch"]):
            verb = "modifier"
            action_label = "des donnees existantes"
        elif any(k in lowered_name for k in ["search", "filter", "find"]):
            verb = "filtrer et rechercher"
            action_label = "des donnees"
        elif any(k in lowered_name for k in ["upload", "import"]):
            verb = "importer"
            action_label = "des fichiers ou des donnees"

        if extension == ".vue":
            subject = "Dans le composant UI"
            if module_name:
                subject = f"Dans le composant du module {module_name}"
            if artifact_name and artifact_name != module_name:
                subject += f" ({artifact_name})"

            if not verb:
                verb = "gerer"
                action_label = "une interaction utilisateur"

            clauses = [f"{subject}, la fonction {readable_name} permet de {verb} {action_label}".strip()]

            template_bits = []
            for usage in template_usages[:4]:
                snippet = str(usage.get("template_snippet", "")).strip()
                if not snippet:
                    continue
                if "@click" in snippet:
                    template_bits.append("l'action est declenchee par un clic utilisateur")
                elif "v-model" in snippet:
                    template_bits.append("le comportement depend d'une saisie ou d'une selection")
                elif ":disabled" in snippet:
                    template_bits.append("l'etat d'activation des controles est gere dans l'interface")
            clauses.extend(template_bits[:1])

            if any(x in lowered_code for x in ["fetch", "reload", "refresh", "load"]) and any(x in lowered_name for x in ["delete", "remove", "update", "save", "add"]):
                clauses.append("rafraichit la liste ou recharge les donnees apres l'action")

            if any(x in lowered_code for x in ["selected", "checkbox", "v-model"]) and any(x in lowered_name for x in ["delete", "display", "show", "open"]):
                clauses.append("gere explicitement la selection des elements concernes")

            if any(x in lowered_code for x in ["url.createobjecturl", "window.open", "dialog", "popup"]):
                clauses.append("declenche un affichage detaille ou une ouverture de contenu")

            if "alert(" in lowered_code:
                if "success" in lowered_code or "successfully" in lowered_code:
                    clauses.append("affiche un message de confirmation en cas de succes")
                if "failed" in lowered_code or "error" in lowered_code:
                    clauses.append("affiche un message explicite en cas d'erreur")
            elif "console.error" in lowered_code or "catch (" in lowered_code or "catch(" in lowered_code:
                clauses.append("gere un scenario d'erreur ou d'echec")

            result = ". ".join(uniq_keep_order([c for c in clauses if c])).strip()
            if result and not result.endswith("."):
                result += "."
            return result

        parts = []
        subject = f"Dans le microservice {microservice}, la fonction {readable_name}"
        parts.append(subject)

        if http_method and http_path:
            parts.append(f"traite l'endpoint {http_method} {http_path}")

        actions = []
        call_matches = re.findall(r"\b(?:await\s+)?([A-Za-z_][A-Za-z0-9_$.]*)\s*\(", code_excerpt)
        ignored_calls = {
            "if", "for", "while", "switch", "catch", "return", "console.error", "console.log",
            "alert", "url.createobjecturl",
        }
        ordered_calls = []
        seen_calls = set()
        for item in call_matches:
            norm = str(item or "").strip()
            if not norm:
                continue
            low = norm.lower()
            if low in ignored_calls or low.startswith("this.logger"):
                continue
            if norm not in seen_calls:
                seen_calls.add(norm)
                ordered_calls.append(norm)
        if ordered_calls:
            main_calls = ", ".join(ordered_calls[:3])
            actions.append(f"appelle {main_calls}")

        state_updates = re.findall(r"([A-Za-z_][A-Za-z0-9_$.]*(?:\.value)?)\s*=", code_excerpt)
        state_updates = [s for s in state_updates if not str(s).startswith("const ")]
        dedup_updates = []
        seen_updates = set()
        for item in state_updates:
            if item not in seen_updates:
                seen_updates.add(item)
                dedup_updates.append(item)
        if dedup_updates:
            actions.append(f"met a jour {', '.join(dedup_updates[:3])}")

        if "response.json(" in lowered_code or ".json(result)" in lowered_code:
            actions.append("retourne une reponse JSON en cas de succes")

        if "response.status(500)" in lowered_code or "internal server error" in lowered_code:
            actions.append("renvoie une erreur serveur en cas d'echec")
        elif "catch (" in lowered_code or "catch(" in lowered_code:
            actions.append("gere un cas d'erreur ou d'exception")

        if any(x in lowered_code for x in ["dto", "@body()", "@query()", "@param("]):
            actions.append("utilise des donnees d'entree structurees")

        if not actions and ui_behavior:
            normalized = [str(x).replace("_", " ").replace("template binding:", "binding template ") for x in ui_behavior[:4]]
            actions.append(", ".join(normalized))

        if actions:
            parts.append(", puis ".join(uniq_keep_order(actions)))

        result = ". ".join([p for p in parts if p]).strip()
        if result and not result.endswith("."):
            result += "."
        return result

    def _derive_ui_screens_for_file(self, microservice, file_path):
        screens = set()
        file_path = self._normalize_repo_path(file_path)
        if not microservice or not file_path:
            return []
        artifact = self._extract_artifact_name_from_path(file_path)
        if ("/views/" in file_path or "/pages/" in file_path) and artifact:
            screens.add(artifact)
        module_name = self._extract_module_name_from_path(file_path)
        repo_meta = self.repo_inventory_index.get(str(microservice).strip(), {})
        for screen in (repo_meta.get("module_views", {}) or {}).get(module_name, []):
            if screen:
                screens.add(str(screen))
        return sorted(screens)

    def _extract_ui_labels_for_function(self, fn_id, microservice, file_path):
        row = self.function_code_index.get(str(fn_id or "").lower())
        if not isinstance(row, dict):
            return []
        content = self._get_source_file_content(microservice, file_path)
        if not content:
            return []
        lines = content.splitlines()
        labels = set()

        def _extract_from_window(window_text):
            for m in re.finditer(r"\$t\(\s*['\"]([^'\"]+)['\"]\s*\)", window_text):
                token = str(m.group(1) or "").strip()
                if token:
                    labels.add(token)
            for m in re.finditer(r">\s*([^<{][^<]{0,80}?)\s*<", window_text):
                token = re.sub(r"\s+", " ", str(m.group(1) or "")).strip()
                if token and len(token) > 1 and not token.startswith("$t("):
                    labels.add(token)

        usages = row.get("template_usages", []) or []
        for usage in usages:
            try:
                line_no = int(usage.get("template_line") or 0)
            except Exception:
                line_no = 0
            if line_no > 0:
                start = max(1, line_no - 4)
                end = min(len(lines), line_no + 5)
                _extract_from_window("\n".join(lines[start - 1:end]))

        if not labels:
            function_name = str(row.get("function_name", "")).strip()
            if function_name:
                for idx, line in enumerate(lines, start=1):
                    if function_name in line and "@click" in line:
                        start = max(1, idx - 4)
                        end = min(len(lines), idx + 5)
                        _extract_from_window("\n".join(lines[start - 1:end]))

        return sorted(labels)

    def _derive_ui_context_for_function(self, fn_id, microservice, file_path, frontend_equivalents=None):
        ui_components = set()
        ui_files = set()
        ui_screens = set()
        ui_labels = set()

        sources = []
        normalized_path = self._normalize_repo_path(file_path)
        if normalized_path and str(microservice or "").endswith("-ui"):
            sources.append((str(microservice).strip(), normalized_path))
        for eq in frontend_equivalents or []:
            eq_ms = str((eq or {}).get("microservice") or "").strip()
            eq_file = self._normalize_repo_path((eq or {}).get("file"))
            if eq_ms and eq_file:
                sources.append((eq_ms, eq_file))

        seen = set()
        for src_ms, src_file in sources:
            sig = f"{src_ms}|{src_file}"
            if sig in seen:
                continue
            seen.add(sig)
            ui_files.add(src_file)
            component = self._component_from_path(src_file)
            if component:
                ui_components.add(component)
            for screen in self._derive_ui_screens_for_file(src_ms, src_file):
                ui_screens.add(screen)
            for label in self._extract_ui_labels_for_function(fn_id, src_ms, src_file):
                ui_labels.add(label)

        return {
            "ui_components": sorted(ui_components),
            "ui_files": sorted(ui_files),
            "ui_screens": sorted(ui_screens),
            "ui_labels": sorted(ui_labels),
        }

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

    @staticmethod
    def _camel_to_label_v2(name):
        raw = str(name or "").strip()
        if not raw:
            return ""
        words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw).replace("_", " ").split()
        out = []
        for word in words:
            low = word.lower()
            if low == "jit":
                out.append("JIT")
            elif low == "api":
                out.append("API")
            else:
                out.append(low)
        return " ".join(out).strip()

    def _derive_non_endpoint_business_phrase_v2(self, function_name, code_text):
        raw = str(function_name or "").strip()
        lowered = raw.lower()
        code_l = str(code_text or "").lower()
        readable = self._camel_to_label_v2(raw)

        replacements = [
            (" status string ", " chaine de statuts "),
            (" status strings ", " chaines de statuts "),
            (" status code ", " code statut "),
            (" status codes ", " codes statut "),
            (" to colors ", " en couleurs "),
            (" to color ", " en couleur "),
            (" colors ", " couleurs "),
            (" color ", " couleur "),
            (" duration seconds ", " duree en secondes "),
            (" duration second ", " duree en secondes "),
            (" closed downtime ", " downtime fermee "),
            (" border ", " bordure "),
        ]

        def normalize_core(text):
            core = f" {str(text or '').strip()} "
            for source, target in replacements:
                core = core.replace(source, target)
            return " ".join(core.split()).strip()

        if lowered.startswith("is") and lowered.endswith("required") and readable:
            core = normalize_core(readable[2:].strip())
            if core.endswith(" required"):
                core = core[:-9].strip()
            if core:
                return f"verifie si {core} est requise"
        if lowered.startswith("resolve") and readable:
            core = normalize_core(readable[len("resolve"):].strip())
            if core:
                return f"determine {core}"
        if lowered.startswith("determine") and readable:
            core = normalize_core(readable[len("determine"):].strip())
            if core:
                return f"determine {core}"
        if lowered.startswith("get") and readable:
            core = normalize_core(readable[len("get"):].strip())
            if core:
                return f"recupere {core}"
        if lowered.startswith("find") and readable:
            core = normalize_core(readable[len("find"):].strip())
            if core:
                return f"recherche {core}"
        if lowered.startswith("create") and readable:
            core = normalize_core(readable[len("create"):].strip() or "des donnees")
            return f"cree {core}"
        if lowered.startswith("update") and readable:
            core = normalize_core(readable[len("update"):].strip() or "des donnees")
            return f"met a jour {core}"
        if lowered.startswith("save") and readable:
            core = normalize_core(readable[len("save"):].strip() or "des donnees")
            return f"enregistre {core}"
        if lowered.startswith("calculate") and readable:
            core = normalize_core(readable[len("calculate"):].strip() or "une valeur")
            return f"calcule {core}"
        if lowered.startswith("parse") and readable:
            core = normalize_core(readable[len("parse"):].strip() or "une valeur")
            return f"analyse {core}"
        if lowered.startswith("normalize") and readable:
            core = normalize_core(readable[len("normalize"):].strip() or "une valeur")
            return f"normalise {core}"
        if lowered.startswith("serialize") and readable:
            core = normalize_core(readable[len("serialize"):].strip() or "une valeur")
            return f"serialise {core}"
        if lowered.startswith("format") and readable:
            core = normalize_core(readable[len("format"):].strip() or "une valeur")
            return f"formate {core}"
        if lowered.startswith("convert") and readable:
            core = normalize_core(readable[len("convert"):].strip() or "une valeur")
            return f"convertit {core}"
        if lowered.startswith("map") and readable:
            core = normalize_core(readable[len("map"):].strip() or "une valeur")
            return f"associe {core}"

        if "threshold" in code_l and "shiftstart" in code_l:
            return "applique une regle metier liee au seuil et au shift"
        if readable:
            return f"traite {normalize_core(readable)}"
        return "traite une regle metier"

    @staticmethod
    def _extract_jsdoc_return_value_v2(text):
        raw = str(text or "")
        match = re.search(r"@returns?\s+([^\n\r*]+)", raw, flags=re.IGNORECASE)
        if not match:
            return ""
        return " ".join(match.group(1).split()).strip(" .")

    @staticmethod
    def _extract_jsdoc_param_names_v2(text):
        raw = str(text or "")
        names = []
        for match in re.finditer(r"@param\s+([A-Za-z_][A-Za-z0-9_]*)", raw, flags=re.IGNORECASE):
            name = str(match.group(1) or "").strip()
            if name and name not in names:
                names.append(name)
        return names[:3]

    def _build_non_endpoint_business_description_v2(self, fn_id, fn_name, fn_container, zone_text, changed_lines):
        row = self.function_code_index.get(str(fn_id or "").lower(), {}) or {}
        context_excerpt = str(row.get("context_excerpt", "")).strip()
        code_excerpt = str(row.get("code_excerpt", "")).strip()
        merged_text = "\n".join([part for part in [context_excerpt, code_excerpt, zone_text] if part]).strip()
        business_phrase = self._derive_non_endpoint_business_phrase_v2(fn_name, merged_text)
        params = self._extract_jsdoc_param_names_v2(context_excerpt)

        details = []
        merged_lower = merged_text.lower()
        if "threshold" in merged_lower:
            details.append("selon un seuil de controle")
        if "ticket.userid" in merged_lower or "modifiedby" in merged_lower:
            details.append("en tenant compte du type de ticket et de son statut de modification")
        if any(token in merged_lower for token in ["shiftstart", "shiftend", "startdate", "enddate"]):
            details.append("et de sa position dans le shift")
        if not details and params:
            details.append(f"a partir de {', '.join(params)}")

        if business_phrase:
            phrase = business_phrase
            if details:
                phrase += " " + " ".join(details)
            return phrase.strip().capitalize()
        return "Traite une regle metier simple."

    def _extract_controller_call_context_v2(self, content, call_index, called_name):
        raw = str(content or "")
        if not raw or call_index is None:
            return {}

        pre_text = raw[:call_index]
        tail_text = pre_text[-5000:]
        controller_prefix = self._extract_controller_prefix_v2(raw)

        route_matches = list(re.finditer(r"@(Get|Post|Put|Patch|Delete)\s*\(([^)]*)\)", tail_text, flags=re.IGNORECASE))
        api_matches = list(re.finditer(r"@ApiOperation\s*\(\s*\{(.*?)\}\s*\)", tail_text, flags=re.IGNORECASE | re.DOTALL))
        dto_matches = list(re.finditer(r"@Api(?:Ok|Created|Response)\s*\(\s*\{(.*?)\}\s*\)", tail_text, flags=re.IGNORECASE | re.DOTALL))

        method = ""
        route = ""
        if route_matches:
            last_route = route_matches[-1]
            method = str(last_route.group(1) or "").upper().strip()
            route = self._extract_first_quoted_v2(last_route.group(2))

        api_summary = ""
        api_description = ""
        if api_matches:
            op_body = api_matches[-1].group(1)
            api_summary = self._extract_swagger_field_v2(op_body, "summary")
            api_description = self._extract_swagger_field_v2(op_body, "description")

        response_type = ""
        if dto_matches:
            type_match = re.search(r"type\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", dto_matches[-1].group(1))
            if type_match:
                response_type = type_match.group(1)

        endpoint_part = ""
        if method:
            full_route = "/".join([p.strip("/") for p in [controller_prefix, route] if p])
            full_route = f"/{full_route}" if full_route else "/"
            endpoint_part = f"Endpoint {method} {full_route}"

        service_call = ""
        call_match = re.search(rf"\bthis\.(\w+)\.{re.escape(str(called_name or ''))}\s*\(", raw[call_index - 120:call_index + 200])
        if call_match:
            service_call = f"appelle {call_match.group(1)}.{called_name}(...)"

        return {
            "endpoint_part": endpoint_part,
            "swagger_part": api_summary or api_description,
            "response_type": response_type,
            "service_call": service_call,
            "score": int(bool(endpoint_part)) * 3 + int(bool(api_summary or api_description)) * 2 + int(bool(service_call)),
        }

    def _find_controller_context_for_function_v2(self, microservice, function_name):
        snapshot = self._load_source_snapshot_for_microservice(microservice)
        if not snapshot or not function_name:
            return {}

        best = {}
        pattern = re.compile(rf"\bthis\.(\w+)\.{re.escape(str(function_name))}\s*\(", flags=re.IGNORECASE)
        for _, content in snapshot.items():
            if "@Controller" not in str(content or ""):
                continue
            for match in pattern.finditer(content):
                ctx = self._extract_controller_call_context_v2(content, match.start(), function_name)
                if ctx.get("score", 0) > best.get("score", 0):
                    best = ctx
        return best

    def _find_same_file_caller_names_v2(self, microservice, file_path, fn):
        normalized_path = self._normalize_repo_path(file_path)
        peers = self.function_index_by_ms_file.get((microservice, normalized_path), []) or []
        content = self._get_source_file_content(microservice, normalized_path)
        if not peers or not content:
            return []

        fn_id = str(fn.get("id") or fn.get("name") or "").strip()
        fn_name = self._extract_function_short_name(fn_id)
        fn_container = self._extract_function_container(fn_id)
        if not fn_name:
            return []

        callers = []
        for peer in peers:
            peer_id = str(peer.get("id") or peer.get("name") or "").strip()
            peer_name = self._extract_function_short_name(peer_id)
            if not peer_name or peer_name == fn_name:
                continue
            if fn_container and self._extract_function_container(peer_id) != fn_container:
                continue
            start = int(peer.get("line_start") or 0)
            end = int(peer.get("line_end") or 0)
            if start <= 0 or end <= 0 or end < start:
                continue
            lines = content.splitlines()
            peer_zone = "\n".join(lines[start - 1:end])
            if re.search(rf"\b(?:this\.)?{re.escape(fn_name)}\s*\(", peer_zone):
                callers.append(peer_name)
        return callers

    @staticmethod
    def _extract_enclosing_callable_name_v2(content, index):
        raw = str(content or "")
        if not raw or index is None:
            return ""
        pre_text = raw[:index]
        candidates = []
        patterns = [
            r"(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            r"(?:public|private|protected|static|\s)*(?:async\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*:\s*[^{=\n]+\{",
            r"(?:public|private|protected|static|\s)*(?:async\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*\{",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, pre_text, flags=re.IGNORECASE | re.MULTILINE):
                name = str(match.group(1) or "").strip()
                if name and name.lower() not in {"if", "for", "while", "switch", "catch", "constructor"}:
                    candidates.append((match.start(), name))
        if not candidates:
            return ""
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][1]

    def _find_raw_caller_names_v2(self, microservice, file_path, function_name):
        normalized_path = self._normalize_repo_path(file_path)
        content = self._get_source_file_content(microservice, normalized_path)
        if not content or not function_name:
            return []

        callers = []
        pattern = re.compile(rf"\b(?:this\.)?{re.escape(str(function_name))}\s*\(", flags=re.IGNORECASE)
        for match in pattern.finditer(content):
            prefix = content[max(0, match.start() - 30):match.start()]
            if re.search(r"(?:function|const|let|var)\s+$", prefix, flags=re.IGNORECASE):
                continue
            caller_name = self._extract_enclosing_callable_name_v2(content, match.start())
            if caller_name and caller_name.lower() != str(function_name).lower() and caller_name not in callers:
                callers.append(caller_name)
        return callers

    def _find_transitive_caller_names_v2(self, microservice, file_path, function_name, max_depth=3):
        if not function_name:
            return []
        seen = {str(function_name).lower()}
        ordered = []
        frontier = [function_name]
        depth = 0
        while frontier and depth < max_depth:
            next_frontier = []
            for current_name in frontier:
                raw_callers = self._find_raw_caller_names_v2(microservice, file_path, current_name)
                for caller_name in raw_callers:
                    key = str(caller_name).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    ordered.append(caller_name)
                    next_frontier.append(caller_name)
            frontier = next_frontier
            depth += 1
        return ordered

    def _resolve_endpoint_context_for_function_v2(self, microservice, file_path, fn):
        fn_id = str(fn.get("id") or fn.get("name") or "").strip()
        fn_name = self._extract_function_short_name(fn_id)
        if not fn_name:
            return {}

        best = self._find_controller_context_for_function_v2(microservice, fn_name)
        for caller_name in self._find_same_file_caller_names_v2(microservice, file_path, fn):
            ctx = self._find_controller_context_for_function_v2(microservice, caller_name)
            if ctx.get("score", 0) > best.get("score", 0):
                best = ctx
        for caller_name in self._find_transitive_caller_names_v2(microservice, file_path, fn_name):
            ctx = self._find_controller_context_for_function_v2(microservice, caller_name)
            if ctx.get("score", 0) > best.get("score", 0):
                best = ctx
        return best

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
        controller_prefix = self._extract_controller_prefix_v2(content)
        zone_start = max(1, start - 20)
        zone_end = min(len(lines), end + 20)
        zone_text = "\n".join(lines[zone_start - 1:zone_end])

        route_match = re.search(r"@(Get|Post|Put|Patch|Delete)\s*\(([^)]*)\)", zone_text, flags=re.IGNORECASE)
        method = str(fn.get("http_method") or "").upper().strip()
        route = str(fn.get("path") or "").strip()
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
        swagger_part = api_summary or api_description

        if not endpoint_part and not swagger_part and not service_call and fn_name:
            resolved_ctx = self._resolve_endpoint_context_for_function_v2(microservice, file_path, fn)
            endpoint_part = resolved_ctx.get("endpoint_part", "")
            swagger_part = resolved_ctx.get("swagger_part", "") or swagger_part
            response_type = response_type or resolved_ctx.get("response_type", "")
            service_call = service_call or resolved_ctx.get("service_call", "")

        if not endpoint_part and not swagger_part and not service_call and fn_name:
            non_endpoint_desc = self._build_non_endpoint_business_description_v2(
                fn_id=fn_id,
                fn_name=fn_name,
                fn_container=fn_container,
                zone_text=zone_text,
                changed_lines=changed_lines,
            )
            if non_endpoint_desc:
                return non_endpoint_desc

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
        method = str(fn.get("http_method") or "").upper().strip()
        route = str(fn.get("path") or "").strip()
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
        swagger_part = api_summary or api_description

        if not endpoint_part and not swagger_part and not service_call and fn_name:
            resolved_ctx = self._resolve_endpoint_context_for_function_v2(microservice, file_path, fn)
            endpoint_part = resolved_ctx.get("endpoint_part", "")
            swagger_part = resolved_ctx.get("swagger_part", "") or swagger_part
            response_type = response_type or resolved_ctx.get("response_type", "")
            service_call = service_call or resolved_ctx.get("service_call", "")

        if not endpoint_part and not swagger_part and not service_call and fn_name:
            non_endpoint_desc = self._build_non_endpoint_business_description_v2(
                fn_id=fn_id,
                fn_name=fn_name,
                fn_container=fn_container,
                zone_text=zone_text,
                changed_lines=changed_lines,
            )
            if non_endpoint_desc:
                return non_endpoint_desc

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
    def _extract_file_roles_from_paths(files):
        roles = []
        seen = set()
        for file_path in files or []:
            raw = str(file_path or "").replace("\\", "/").strip()
            if not raw:
                continue
            low = raw.lower()
            name = Path(low).name
            detected = []
            if "/migration/" in low or name.endswith(".sql"):
                detected.append("migration")
            if "/entities/" in low or name.endswith(".entity.ts") or "entity" in name:
                detected.append("entity")
            if "/service/" in low or name in {"api.ts", "api.js"} or name.endswith(".service.ts"):
                detected.append("service/api")
            if "/controller" in low or name.endswith(".controller.ts"):
                detected.append("controller")
            if "/store/" in low:
                detected.append("store")
            if "/components/" in low:
                detected.append("component")
            if "/views/" in low or "/pages/" in low:
                detected.append("view/page")
            if "/dto/" in low or name.endswith(".dto.ts"):
                detected.append("dto")
            if "/config/" in low or name.endswith(".yaml") or name.endswith(".yml"):
                detected.append("config")
            if name.endswith(".json"):
                detected.append("json")
            for role in detected:
                if role.lower() in seen:
                    continue
                seen.add(role.lower())
                roles.append(role)
        return roles

    @staticmethod
    def _join_sentence_parts(parts):
        cleaned = []
        for part in parts or []:
            value = str(part or "").strip().rstrip(".")
            if value:
                cleaned.append(value)
        if not cleaned:
            return ""
        text = ". ".join(cleaned).strip()
        if text and not text.endswith("."):
            text += "."
        return text

    def _build_non_function_scope_group(self, microservice, files, commit_ids, commit_messages, retest_items, workitem_titles=None):
        files = [str(f or "").strip() for f in (files or []) if str(f or "").strip()]
        commit_ids = [str(c or "").strip() for c in (commit_ids or []) if str(c or "").strip()]
        retest_items = [item for item in (retest_items or []) if isinstance(item, dict)]
        if not files and not retest_items:
            return None

        file_items = [item for item in retest_items if str(item.get("type") or "").upper() == "FILE-RETEST"]
        infra_items = [item for item in retest_items if str(item.get("type") or "").upper() == "INFRA-RETEST"]
        modules = []
        seen_modules = set()
        for file_path in files:
            module_name = self._extract_module_name_from_path(file_path)
            if module_name and module_name.lower() not in seen_modules:
                seen_modules.add(module_name.lower())
                modules.append(module_name)
        file_roles = self._extract_file_roles_from_paths(files)
        file_names = []
        seen_names = set()
        for file_path in files:
            name = Path(str(file_path)).name.strip()
            if name and name.lower() not in seen_names:
                seen_names.add(name.lower())
                file_names.append(name)

        actions = []
        seen_actions = set()
        for item in file_items + infra_items:
            text = str(item.get("what_to_test") or "").strip()
            key = text.lower()
            if text and key not in seen_actions:
                seen_actions.add(key)
                actions.append(text)

        if infra_items and not file_items:
            description = str(infra_items[0].get("description") or "").strip() or "Changement infra/config detecte."
            priority_order = 3
            scope_label = "P3 infra/config"
            function_scope_type = "P3 infra/config"
            impact_label = "Impact infra/config"
            functional_parts = [
                f"Dans le microservice {microservice}, les changements touchent directement la configuration, le deploiement ou le versioning du service."
            ]
            if modules:
                functional_parts.append(f"Les modules visibles dans les fichiers touches sont: {', '.join(modules[:3])}")
            elif file_names:
                functional_parts.append(f"Les fichiers traces par commit sont: {', '.join(file_names[:3])}")
            if file_roles:
                functional_parts.append(f"Les types de fichiers touches sont: {', '.join(file_roles[:4])}")
            functional_parts.append("Aucune fonction metier exacte n'est prouvee par ligne de diff, mais le microservice reste directement impacte par commit et fichier.")
        else:
            description = "Fichiers code touches sans preuve ligne->fonction exploitable."
            priority_order = 2
            scope_label = "P2/P3 microservice direct"
            function_scope_type = "P2/P3 candidate"
            impact_label = "Impact microservice direct"
            zone_bits = []
            if modules:
                zone_bits.append(f"les modules {', '.join(modules[:3])}")
            if file_roles:
                zone_bits.append(f"des fichiers de type {', '.join(file_roles[:4])}")
            if file_names and not zone_bits:
                zone_bits.append(f"les fichiers {', '.join(file_names[:3])}")
            scope_area = ", ".join(zone_bits) if zone_bits else "des fichiers code traces par commit"
            functional_parts = [
                f"Dans le microservice {microservice}, les changements touchent directement {scope_area}."
            ]
            if any(role in file_roles for role in ["service/api", "controller"]):
                functional_parts.append("Ces changements peuvent influencer les appels API, la recuperation ou la mise a jour des donnees du microservice.")
            if any(role in file_roles for role in ["entity", "migration", "dto"]):
                functional_parts.append("Ils peuvent aussi influencer la structure, l'indexation ou la lecture des donnees manipulees par ce microservice.")
            if any(role in file_roles for role in ["store", "component", "view/page"]):
                functional_parts.append("Ils peuvent modifier le comportement applicatif ou l'enchainement technique du module touche.")
            functional_parts.append("Aucune fonction metier exacte n'est prouvee par ligne de diff, mais le microservice reste directement impacte par commit et fichier.")

        business_symptom = self._build_non_function_business_symptom(
            microservice=microservice,
            modules=modules,
            file_roles=file_roles,
            workitem_titles=workitem_titles or [],
            commit_messages=commit_messages or [],
        )
        technical_justification = self._build_non_function_technical_justification(
            microservice=microservice,
            modules=modules,
            file_roles=file_roles,
            files=files,
            commit_ids=commit_ids,
        )
        action_parts = self._merge_non_function_retest_actions(
            base_actions=actions,
            microservice=microservice,
            modules=modules,
            file_roles=file_roles,
            workitem_titles=workitem_titles or [],
            commit_messages=commit_messages or [],
        )

        evidence = (
            f"commit(s): {', '.join([c[:8] for c in commit_ids[:4]])} | "
            f"file(s): {', '.join(files[:3])}"
        ).strip()
        if evidence.endswith("|"):
            evidence = evidence[:-1].strip()

        return {
            "group_type": "INFRA-RETEST" if priority_order == 3 and not file_items else "FILE-RETEST",
            "function_scope_type": function_scope_type,
            "priority_order": priority_order,
            "priority": "P1" if priority_order == 1 else "P2/P3",
            "scope_label": scope_label,
            "impact_label": impact_label,
            "fallback_label": "Aucune fonction metier prouvee",
            "functions": [],
            "description": description,
            "business_symptom": business_symptom,
            "technical_justification": technical_justification,
            "functional_description": self._join_sentence_parts(functional_parts),
            "what_to_test": " ".join(action_parts).strip() or "Verifier le parcours nominal du microservice directement impacte.",
            "evidence": evidence,
            "files": files,
            "commit_ids": commit_ids,
        }

    @staticmethod
    def _clean_scope_title_text(value):
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"\[[^\]]+\]\s*", "", text)
        text = re.sub(r"\s+", " ", text).strip(" -:;,")
        return text

    def _build_non_function_business_symptom(self, microservice, modules, file_roles, workitem_titles, commit_messages):
        merged = " ".join(
            [self._clean_scope_title_text(x) for x in ((workitem_titles or []) + (commit_messages or []))]
        ).strip()
        lowered = merged.lower()
        module_label = modules[0] if modules else ""

        if "shift-context" in lowered:
            if "current shift" in lowered and "previous" in lowered:
                return (
                    "Le changement cible la recuperation des quarts via l'API shift-context "
                    "et peut limiter le retour complet des quarts attendus au-dela du quart actuel."
                )
            return (
                "Le changement cible la recuperation des donnees de quart via l'API shift-context "
                "et peut influencer la disponibilite des informations attendues dans les ecrans consommateurs."
            )
        if "not displaying data" in lowered or ("display" in lowered and "data" in lowered):
            area = f" dans le module {module_label}" if module_label else ""
            return (
                f"Le symptome signale un risque de non affichage ou d'affichage incomplet des donnees{area}, "
                "avec un impact possible sur le parcours fonctionnel consommateur."
            )
        if "null" in lowered:
            area = f" pour le module {module_label}" if module_label else ""
            return (
                f"Le changement peut faire remonter des valeurs absentes ou nulles{area}, "
                "ce qui peut alterer l'affichage ou le traitement fonctionnel attendu."
            )
        if any(role in file_roles for role in ["service/api", "controller"]):
            area = f" du module {module_label}" if module_label else ""
            return (
                f"Le changement peut modifier la recuperation ou la restitution des donnees applicatives{area}, "
                "avec un impact possible sur les parcours qui consomment cette API."
            )
        if any(role in file_roles for role in ["entity", "migration"]):
            area = f" pour le module {module_label}" if module_label else ""
            return (
                f"Le changement peut influencer la structure ou la lecture des donnees{area}, "
                "et donc la coherence fonctionnelle visible cote utilisateur."
            )
        if "json" in file_roles or "config" in file_roles:
            return (
                f"Le changement peut influencer la disponibilite ou le comportement de {microservice} "
                "au niveau configuration/deploiement."
            )
        return (
            f"Le microservice {microservice} est directement impacte par commit et fichier, "
            "avec un risque fonctionnel a verifier sur le parcours nominal associe."
        )

    @staticmethod
    def _build_non_function_technical_justification(microservice, modules, file_roles, files, commit_ids):
        parts = [f"Microservice direct touche: {microservice}"]
        if modules:
            parts.append(f"Module(s) detecte(s): {', '.join(modules[:3])}")
        if file_roles:
            parts.append(f"Type(s) de fichiers touches: {', '.join(file_roles[:4])}")
        if files:
            parts.append(f"Fichiers traces: {', '.join([Path(f).name for f in files[:3]])}")
        if commit_ids:
            parts.append(f"Preuve: {len(commit_ids)} commit(s) relies")
        return " | ".join(parts)

    def _merge_non_function_retest_actions(self, base_actions, microservice, modules, file_roles, workitem_titles, commit_messages):
        merged = " ".join(
            [self._clean_scope_title_text(x) for x in ((workitem_titles or []) + (commit_messages or []))]
        ).lower()
        actions = []
        seen = set()

        def add_action(text):
            value = str(text or "").strip()
            if not value:
                return
            key = value.lower()
            if key in seen:
                return
            seen.add(key)
            actions.append(value)

        for item in base_actions or []:
            add_action(item)

        if "shift-context" in merged:
            if "current shift" in merged and "previous" in merged:
                add_action("Verifier que l'API shift-context retourne bien le quart actuel ainsi que les quarts attendus au-dela du quart actuel.")
            else:
                add_action("Verifier le comportement fonctionnel des donnees de quart renvoyees par l'API shift-context.")
        if "not displaying data" in merged or ("display" in merged and "data" in merged):
            add_action("Verifier que les donnees attendues s'affichent correctement dans les ecrans consommateurs.")
        if "null" in merged:
            add_action("Verifier le comportement applicatif si certaines valeurs reviennent absentes ou nulles.")
        if any(role in file_roles for role in ["entity", "migration"]):
            add_action("Verifier la coherence des donnees lues, structurees ou retournees apres modification de structure.")
        if any(role in file_roles for role in ["service/api", "controller"]):
            add_action("Verifier les cas nominal, reponse vide, reponse partielle et gestion d'erreur des appels API lies au microservice.")
        if modules:
            add_action(f"Verifier le parcours fonctionnel principal du module {modules[0]}.")
        if microservice.endswith("-data-ingestion") or "ingestion" in microservice:
            add_action("Verifier que l'alimentation ou la mise a disposition des donnees reste coherente pour les consommateurs fonctionnels.")
        if microservice.endswith("-infrastructure") or microservice.endswith("infrastructure"):
            add_action("Verifier la disponibilite du service et le parcours nominal de bout en bout apres mise a jour de configuration.")

        return actions

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

    @staticmethod
    def _normalize_endpoint_path(path_value):
        p = str(path_value or "").strip()
        if not p:
            return ""
        if "://" in p:
            try:
                p = "/" + p.split("://", 1)[1].split("/", 1)[1]
            except Exception:
                return ""
        p = p.split("?", 1)[0].strip()
        if not p.startswith("/"):
            p = "/" + p
        p = re.sub(r"/{2,}", "/", p)
        if len(p) > 1 and p.endswith("/"):
            p = p[:-1]
        return p

    @staticmethod
    def _component_from_path(path_value):
        p = str(path_value or "").replace("\\", "/")
        if not p:
            return ""
        name = p.split("/")[-1]
        return re.sub(r"\.(vue|tsx|jsx|ts|js)$", "", name, flags=re.IGNORECASE)

    def _extract_http_calls_from_content(self, content):
        """
        Extract literal HTTP calls from front code with strict evidence only.
        Returns list of dict: {method, path}
        """
        out = []
        if not isinstance(content, str) or not content:
            return out

        for method in ("get", "post", "put", "patch", "delete"):
            pattern = re.compile(rf"\.{method}\s*\(\s*([\"'`])([^\"'`]+)\1", re.IGNORECASE)
            for m in pattern.finditer(content):
                raw_path = str(m.group(2) or "").strip()
                if not raw_path or "${" in raw_path:
                    continue
                norm = self._normalize_endpoint_path(raw_path)
                if not norm:
                    continue
                out.append({"method": method.upper(), "path": norm})

        fetch_pattern = re.compile(
            r"fetch\s*\(\s*([\"'`])([^\"'`]+)\1(?:\s*,\s*\{([^}]*)\})?",
            re.IGNORECASE | re.DOTALL
        )
        for m in fetch_pattern.finditer(content):
            raw_path = str(m.group(2) or "").strip()
            if not raw_path or "${" in raw_path:
                continue
            norm = self._normalize_endpoint_path(raw_path)
            if not norm:
                continue
            options_blob = str(m.group(3) or "")
            mm = re.search(r"method\s*:\s*([\"'`])([A-Z]+)\1", options_blob, re.IGNORECASE)
            method = str(mm.group(2) if mm else "GET").upper()
            out.append({"method": method, "path": norm})

        dedup = []
        seen = set()
        for row in out:
            key = f"{row.get('method','')}|{row.get('path','')}"
            if key in seen:
                continue
            seen.add(key)
            dedup.append(row)
        return dedup

    def _build_front_endpoint_usage_index(self):
        """
        Build index from endpoint -> proven front files/components consuming it.
        Key: (METHOD, /path)
        """
        repo_root = Path(__file__).parent.parent
        index = {}
        for source_file in repo_root.glob("source_mesx_*ui*.json"):
            try:
                ms_name = source_file.stem.replace("source_", "").replace("_", "-")
                with open(source_file, "r", encoding="utf-8") as f:
                    entries = json.load(f)
                for entry in entries or []:
                    file_path = self._normalize_repo_path(entry.get("chemin"))
                    content = entry.get("contenu")
                    if not file_path or not isinstance(content, str):
                        continue
                    if Path(file_path).suffix.lower() not in {".vue", ".ts", ".tsx", ".js", ".jsx"}:
                        continue
                    http_calls = self._extract_http_calls_from_content(content)
                    if not http_calls:
                        continue
                    component = self._component_from_path(file_path)
                    for call in http_calls:
                        method = str(call.get("method") or "").upper().strip()
                        path = self._normalize_endpoint_path(call.get("path"))
                        if not method or not path:
                            continue
                        key = (method, path)
                        index.setdefault(key, []).append({
                            "microservice": ms_name,
                            "file": file_path,
                            "component": component,
                            "method": method,
                            "path": path,
                        })
            except Exception:
                continue

        # Deduplicate per endpoint.
        for key, rows in list(index.items()):
            seen = set()
            out = []
            for r in rows:
                sig = f"{r.get('microservice','')}|{r.get('file','')}"
                if sig in seen:
                    continue
                seen.add(sig)
                out.append(r)
            index[key] = out
        return index

    @staticmethod
    def _extract_endpoint_from_description_text(description):
        text = str(description or "")
        m = re.search(r"Endpoint\s+([A-Z]+)\s+([^\|\n]+)", text, re.IGNORECASE)
        if not m:
            return None
        method = str(m.group(1) or "").upper().strip()
        path = str(m.group(2) or "").strip()
        if not method or not path:
            return None
        return {"method": method, "path": path}

    def _find_front_equivalents_for_endpoint(self, method, path):
        m = str(method or "").upper().strip()
        p = self._normalize_endpoint_path(path)
        if not m or not p:
            return []
        return list(self.front_endpoint_usage_index.get((m, p), []))

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

    def get_workitems_by_activity_date(self, date_from, date_to, workitem_type="All", date_source="any", limit=300):
        """
        Filter WorkItems (Bug/User Story) by PR/Commit activity date interval
        using already extracted JSON evidence.
        """
        if not self.workitems_data:
            return {"error": "No WorkItems loaded"}

        try:
            start = datetime.strptime(str(date_from), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end_exclusive = (
                datetime.strptime(str(date_to), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                + timedelta(days=1)
            )
            if end_exclusive <= start:
                return {"error": "Invalid date range: date_to must be >= date_from"}
        except Exception:
            return {"error": "Invalid date format. Use YYYY-MM-DD."}

        date_source_norm = str(date_source or "any").strip().lower()
        if date_source_norm not in {"commit", "pr", "any"}:
            date_source_norm = "any"

        type_norm = str(workitem_type or "All").strip().lower()
        allowed_types = {"all", "bug", "user story"}
        if type_norm not in allowed_types:
            type_norm = "all"

        try:
            max_items = int(limit)
        except Exception:
            max_items = 300
        max_items = max(1, min(max_items, 1000))

        def in_range(dt):
            return dt is not None and start <= dt < end_exclusive

        results = []
        for wi in self.workitems_data.values():
            wi_id = wi.get("id")
            if wi_id is None:
                continue

            wi_type = str(wi.get("type", "")).strip()
            if type_norm != "all" and wi_type.lower() != type_norm:
                continue

            links = self.workitem_dev_links_index.get(int(wi_id), {})
            commit_ids = [str(cid) for cid in (links.get("linked_commit_ids") or []) if cid]
            pr_ids = [int(pid) for pid in (links.get("linked_pull_request_ids") or []) if pid is not None]

            matched_commit_ids = []
            matched_pr_ids = []
            last_activity = None

            for cid in commit_ids:
                commit = self.commits_index.get(cid)
                c_dt = self._parse_iso_datetime((commit or {}).get("date"))
                if in_range(c_dt):
                    matched_commit_ids.append(cid)
                    if last_activity is None or c_dt > last_activity:
                        last_activity = c_dt

            for pid in pr_ids:
                pr = self.pull_requests_index.get(pid)
                p_dt = self._parse_iso_datetime((pr or {}).get("date_creation"))
                if in_range(p_dt):
                    matched_pr_ids.append(pid)
                    if last_activity is None or p_dt > last_activity:
                        last_activity = p_dt

            has_commit = len(matched_commit_ids) > 0
            has_pr = len(matched_pr_ids) > 0
            if date_source_norm == "commit" and not has_commit:
                continue
            if date_source_norm == "pr" and not has_pr:
                continue
            if date_source_norm == "any" and not (has_commit or has_pr):
                continue

            results.append({
                "id": int(wi_id),
                "titre": wi.get("titre") or wi.get("title") or "",
                "type": wi_type,
                "statut": wi.get("statut") or "",
                "priorite": wi.get("priorite", 0),
                "assigne_a": wi.get("assigne_a") or "",
                "matched_commit_count": len(matched_commit_ids),
                "matched_pr_count": len(matched_pr_ids),
                "matched_commit_ids": matched_commit_ids,
                "matched_pr_ids": matched_pr_ids,
                "last_activity_date": last_activity.isoformat().replace("+00:00", "Z") if last_activity else "",
            })

        results.sort(
            key=lambda r: (
                r.get("last_activity_date") or "",
                r.get("matched_commit_count", 0),
                r.get("matched_pr_count", 0),
                r.get("id", 0),
            ),
            reverse=True,
        )
        sliced = results[:max_items]

        return {
            "date_from": date_from,
            "date_to": date_to,
            "type": workitem_type,
            "date_source": date_source_norm,
            "count": len(sliced),
            "total_found": len(results),
            "results": sliced,
        }
    
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

    def _get_cookie_value(self, name):
        raw = self.headers.get("Cookie", "")
        if not raw:
            return ""
        try:
            jar = cookies.SimpleCookie()
            jar.load(raw)
            morsel = jar.get(name)
            return morsel.value if morsel else ""
        except Exception:
            return ""

    def _get_authenticated_email(self):
        token = self._get_cookie_value(AUTH_COOKIE_NAME)
        return _get_auth_session_email(token)

    def _is_public_path(self, path):
        return path in {"/login", "/logout"} or path.startswith("/static/")

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _set_auth_cookie(self, token, expires_at):
        expiry = expires_at.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.send_header(
            "Set-Cookie",
            f"{AUTH_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Expires={expiry}"
        )

    def _clear_auth_cookie(self):
        self.send_header(
            "Set-Cookie",
            f"{AUTH_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
        )

    @staticmethod
    def _escape_html(value):
        text = str(value or "")
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )

    def _render_template_text(self, content, context):
        out = str(content or "")
        for key, value in (context or {}).items():
            out = out.replace(f"{{{{{key}}}}}", str(value))
        return out

    def _inject_auth_chrome(self, content, email):
        safe_email = self._escape_html(email)
        auth_css = """
        .nav-user-badge {
            color: #fff;
            font-size: 12px;
            font-weight: 600;
            padding: 8px 12px;
            border: 1px solid rgba(255,255,255,0.25);
            border-radius: 999px;
            background: rgba(255,255,255,0.10);
            display: inline-flex;
            align-items: center;
            gap: 8px;
            white-space: nowrap;
        }
        .nav-logout-link {
            color: #fff !important;
        }
        .nav-logout-link::after {
            display: none !important;
        }
        @media (max-width: 980px) {
            .nav-user-badge { display: none; }
        }
        """
        auth_nav = (
            f'<li><span class="nav-user-badge"><i class="fas fa-user-circle"></i>{safe_email}</span></li>'
            f'<li><a class="nav-logout-link" href="/logout"><i class="fas fa-sign-out-alt"></i>Logout</a></li>'
        )
        out = str(content or "")
        if "</style>" in out:
            out = out.replace("</style>", auth_css + "\n</style>", 1)
        if "</ul>" in out and 'nav-logout-link' not in out:
            out = out.replace("</ul>", auth_nav + "</ul>", 1)
        return out

    def serve_login_template(self, error_message="", email_value=""):
        try:
            full_path = (BASE_DIR / "templates" / "login.html").resolve()
            if not full_path.exists():
                self.send_error(404, "Login template not found")
                return
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            error_html = ""
            if str(error_message or "").strip():
                error_html = (
                    f'<div class="login-alert"><i class="fas fa-circle-exclamation"></i>'
                    f'<span>{self._escape_html(error_message)}</span></div>'
                )
            rendered = self._render_template_text(content, {
                "ERROR_BLOCK": error_html,
                "EMAIL_VALUE": self._escape_html(email_value),
                "ALLOWED_DOMAIN": self._escape_html(AUTH_ALLOWED_DOMAIN),
            })
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(rendered.encode("utf-8"))
        except Exception as e:
            self.send_error(500, str(e))

    def handle_login(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except Exception:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        params = urllib.parse.parse_qs(raw.decode("utf-8", errors="ignore"))
        email = str((params.get("email") or [""])[0]).strip()
        if not _is_allowed_auth_email(email):
            self.serve_login_template(
                error_message=f"Only corporate email addresses ending with @{AUTH_ALLOWED_DOMAIN} are allowed.",
                email_value=email,
            )
            return
        token, expires_at = _create_auth_session(email)
        self.send_response(302)
        self._set_auth_cookie(token, expires_at)
        self.send_header("Location", "/")
        self.end_headers()

    def handle_logout(self):
        token = self._get_cookie_value(AUTH_COOKIE_NAME)
        _clear_auth_session(token)
        self.send_response(302)
        self._clear_auth_cookie()
        self.send_header("Location", "/login")
        self.end_headers()
    
    def do_GET(self):
        """Handle GET requests"""
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        query_params = urllib.parse.parse_qs(parsed_path.query)
        auth_email = self._get_authenticated_email()

        if path == '/login':
            if auth_email:
                self._redirect('/')
            else:
                self.serve_login_template()
            return
        if path == '/logout':
            self.handle_logout()
            return
        if path == '/static/forvia_logo.png':
            self.serve_file(path.lstrip('/'))
            return
        if not auth_email and path.startswith('/api/webhook/azure-devops'):
            pass
        elif not auth_email and not self._is_public_path(path):
            if path.startswith('/api/'):
                self.send_json_response({"error": "authentication required"}, status_code=401)
            else:
                self._redirect('/login')
            return
        
        # API endpoints
        if path == '/api/statistics':
            self.send_json_response(analyzer.get_statistics())
        elif path == '/api/me':
            self.send_json_response({
                "authenticated": True,
                "email": auth_email,
                "allowed_domain": AUTH_ALLOWED_DOMAIN,
            })
        elif path == '/api/webhook/status':
            self.send_json_response({
                "enabled": WEBHOOK_AUTO_REFRESH_ENABLED,
                "mode": WEBHOOK_REFRESH_MODE,
                "include_step3": WEBHOOK_INCLUDE_STEP3,
                "network_runtime": {
                    "AZURE_INSECURE_TLS": WEBHOOK_AZURE_INSECURE_TLS,
                    "AZURE_DISABLE_ENV_PROXY": WEBHOOK_AZURE_DISABLE_ENV_PROXY,
                    "AZURE_MAX_WORKERS": WEBHOOK_AZURE_MAX_WORKERS,
                    "AZURE_MAX_RETRIES": WEBHOOK_AZURE_MAX_RETRIES,
                    "AZURE_REQUEST_DELAY_MS": WEBHOOK_AZURE_REQUEST_DELAY_MS,
                },
                "state": _snapshot_webhook_state(),
            })
        elif path == '/api/workitem-types':
            self.send_json_response(analyzer.get_workitem_types())
        elif path == '/api/workitem-statuses':
            iteration_path = query_params.get('iteration_path', [''])[0]
            sprint_path = query_params.get('sprint_path', [''])[0]
            workitem_type = query_params.get('type', ['All'])[0]
            self.send_json_response(
                analyzer.get_workitem_status_catalog(
                    iteration_path=iteration_path,
                    sprint_path=sprint_path,
                    workitem_type=workitem_type,
                )
            )
        elif path == '/api/iterations':
            self.send_json_response(analyzer.get_iteration_catalog())
        elif path == '/api/workitems-by-iteration':
            iteration_path = query_params.get('iteration_path', [''])[0]
            sprint_path = query_params.get('sprint_path', [''])[0]
            workitem_type = query_params.get('type', ['All'])[0]
            workitem_status = query_params.get('status', ['All'])[0]
            limit = query_params.get('limit', ['500'])[0]
            self.send_json_response(
                analyzer.get_workitems_by_iteration(
                    iteration_path=iteration_path,
                    sprint_path=sprint_path,
                    workitem_type=workitem_type,
                    status=workitem_status,
                    limit=limit,
                )
            )
        elif path == '/api/nrt-scope-batch':
            raw_ids = query_params.get('workitem_ids', [''])[0]
            ids = [x.strip() for x in str(raw_ids).split(',') if x.strip()]
            self.send_json_response(analyzer.get_nrt_scope_batch(ids))
        elif path == '/api/nrt-scope-batch-direct':
            raw_ids = query_params.get('workitem_ids', [''])[0]
            ids = [x.strip() for x in str(raw_ids).split(',') if x.strip()]
            self.send_json_response(analyzer.get_nrt_scope_batch_direct_trace(ids))
        elif path.startswith('/api/workitems-by-type/'):
            workitem_type = urllib.parse.unquote(path.split('/api/workitems-by-type/')[-1])
            self.send_json_response(analyzer.get_workitems_by_type(workitem_type))
        elif path == '/api/workitems-by-activity-date':
            date_from = query_params.get('date_from', [''])[0]
            date_to = query_params.get('date_to', [''])[0]
            workitem_type = query_params.get('type', ['All'])[0]
            date_source = query_params.get('date_source', ['any'])[0]
            limit = query_params.get('limit', ['300'])[0]
            self.send_json_response(
                analyzer.get_workitems_by_activity_date(
                    date_from=date_from,
                    date_to=date_to,
                    workitem_type=workitem_type,
                    date_source=date_source,
                    limit=limit,
                )
            )
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

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        auth_email = self._get_authenticated_email()

        if path == '/login':
            self.handle_login()
            return

        if path == '/api/webhook/azure-devops':
            self.handle_azure_webhook()
            return

        if not auth_email:
            if path.startswith('/api/'):
                self.send_json_response({"error": "authentication required"}, status_code=401)
            else:
                self._redirect('/login')
            return

        self.send_error(404, 'Not found')

    def handle_azure_webhook(self):
        token_header = self.headers.get("X-NRT-Webhook-Token", "").strip()
        auth_header = self.headers.get("Authorization", "").strip()
        bearer_token = ""
        if auth_header.lower().startswith("bearer "):
            bearer_token = auth_header[7:].strip()

        if AZURE_WEBHOOK_TOKEN:
            provided = token_header or bearer_token
            if provided != AZURE_WEBHOOK_TOKEN:
                self.send_json_response(
                    {"ok": False, "error": "unauthorized webhook token"},
                    status_code=401,
                )
                return

        try:
            length = int(self.headers.get('Content-Length', 0))
        except Exception:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self.send_json_response({"ok": False, "error": "invalid json payload"}, status_code=400)
            return

        event_type = (
            str(payload.get("eventType", "")).strip()
            or str(payload.get("eventTypeName", "")).strip()
            or str((payload.get("resource") or {}).get("eventType", "")).strip()
            or "unknown"
        )

        accepted, reason = _trigger_webhook_refresh("azure-devops-webhook", event_type, payload)
        state = _snapshot_webhook_state()
        self.send_json_response(
            {
                "ok": accepted,
                "message": reason,
                "event_type": event_type,
                "refresh_mode": WEBHOOK_REFRESH_MODE,
                "state": state,
            },
            status_code=202 if accepted else 200,
        )
    
    def serve_template(self, template_path):
        """Serve an HTML template"""
        try:
            full_path = (BASE_DIR / template_path).resolve()
            if full_path.exists():
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                auth_email = self._get_authenticated_email()
                if auth_email:
                    content = self._inject_auth_chrome(content, auth_email)
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
    
    def send_json_response(self, data, status_code=200):
        """Send JSON response"""
        self.send_response(status_code)
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
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-NRT-Webhook-Token, Authorization')
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
            print("  - GET /api/webhook/status")
            print("  - POST /api/webhook/azure-devops")
            print("Press Ctrl+C to stop the server")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Server stopped")
    except Exception as e:
        print(f"[ERROR] {e}")
