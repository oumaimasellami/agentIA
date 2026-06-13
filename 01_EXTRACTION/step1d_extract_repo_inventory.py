#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MESX.0 - Step 1d: Full repository inventory extraction

Goal:
- keep the current extraction pipeline untouched
- extract a complete Azure DevOps repository tree inventory
- preserve all folders/files as proof material for later UI/backend mapping work

Outputs:
- 01_EXTRACTION/repo_inventory.json
- 01_EXTRACTION/repo_inventory_summary.json

This script does NOT download full file contents.
It inventories the tree structure of each repository:
- folders
- files
- extensions
- module/view/component/service/store/router hints
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent
API_VERSION = "7.0"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_WORKERS = 8
DEFAULT_MAX_RETRIES = 6
DEFAULT_REQUEST_DELAY_MS = 0

INVENTORY_PATH = ROOT_DIR / "repo_inventory.json"
SUMMARY_PATH = ROOT_DIR / "repo_inventory_summary.json"
MICROSERVICES_PATH = ROOT_DIR / "microservices.json"


def load_config() -> Dict[str, Any]:
    load_dotenv(ROOT_DIR.parent / ".env")
    return {
        "org": os.getenv("AZURE_ORG", "faurecia-cloud").strip(),
        "project": os.getenv("AZURE_PROJECT", "MES_X.0").strip(),
        "pat": os.getenv("AZURE_PAT", "").strip(),
        "ca_bundle": os.getenv("AZURE_CA_BUNDLE", "").strip(),
        "insecure_tls": os.getenv("AZURE_INSECURE_TLS", "").strip().lower() in {"1", "true", "yes"},
        "timeout": int(os.getenv("AZURE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT)),
        "max_workers": int(os.getenv("AZURE_MAX_WORKERS", DEFAULT_MAX_WORKERS)),
        "disable_env_proxy": os.getenv("AZURE_DISABLE_ENV_PROXY", "false").strip().lower() in {"1", "true", "yes", "on"},
        "max_retries": int(os.getenv("AZURE_MAX_RETRIES", DEFAULT_MAX_RETRIES)),
        "request_delay_ms": int(os.getenv("AZURE_REQUEST_DELAY_MS", DEFAULT_REQUEST_DELAY_MS)),
    }


def ensure_pat(pat: str) -> None:
    if not pat:
        raise ValueError("AZURE_PAT is missing in .env")


def safe_json_dump(path: Path, data: Any) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    last_error: Exception | None = None
    for _ in range(5):
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, path)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    if last_error:
        raise last_error


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_branch_name(value: str) -> str:
    if not isinstance(value, str):
        return "main"
    value = value.strip()
    if value.startswith("refs/heads/"):
        return value[len("refs/heads/"):]
    return value or "main"


def normalize_repo_path(path: str) -> str:
    p = str(path or "").replace("\\", "/").strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    return p


class AzureDevOpsClient:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.org = config["org"]
        self.project = config["project"]
        self.base_url = f"https://dev.azure.com/{self.org}/{self.project}/_apis"
        self.timeout = config["timeout"]
        self.max_retries = max(1, int(config["max_retries"]))
        self.request_delay_ms = max(0, int(config["request_delay_ms"]))

        token = base64.b64encode(f":{config['pat']}".encode("utf-8")).decode("utf-8")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
            }
        )

        if config["disable_env_proxy"]:
            self.session.trust_env = False
            self.session.proxies = {}

        verify_setting: Any = True
        if config["ca_bundle"]:
            verify_setting = config["ca_bundle"]
        elif config["insecure_tls"]:
            verify_setting = False
            requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
        self.verify = verify_setting

    def request(self, method: str, url: str, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.verify)
        attempts = self.max_retries
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            if self.request_delay_ms > 0:
                time.sleep(self.request_delay_ms / 1000.0)
            try:
                response = self.session.request(method, url, **kwargs)
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(min(60, 2 ** min(6, attempt)))
                    continue
                raise

            if response.status_code in {429, 503} and attempt < attempts:
                retry_after = response.headers.get("Retry-After", "").strip()
                try:
                    wait_s = max(1, int(retry_after)) if retry_after else min(60, 2 ** min(6, attempt))
                except Exception:
                    wait_s = min(60, 2 ** min(6, attempt))
                print(f"  ! throttled ({response.status_code}) -> retry in {wait_s}s [{attempt}/{attempts}]")
                time.sleep(wait_s)
                continue

            response.raise_for_status()
            return response

        if last_error:
            raise last_error
        raise RuntimeError("Request failed after retries")

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any] | List[Any]:
        response = self.request("GET", url, params=params)
        return response.json()


def fetch_repositories(client: AzureDevOpsClient) -> List[Dict[str, Any]]:
    if MICROSERVICES_PATH.exists():
        data = load_json(MICROSERVICES_PATH, [])
        if isinstance(data, list) and data:
            return [item for item in data if isinstance(item, dict) and not item.get("is_disabled", False)]

    payload = client.get_json(f"{client.base_url}/git/repositories", params={"api-version": API_VERSION})
    repos: List[Dict[str, Any]] = []
    if isinstance(payload, dict):
        values = payload.get("value", [])
        if isinstance(values, list):
            for repo in values:
                if not isinstance(repo, dict):
                    continue
                repos.append(
                    {
                        "name": repo.get("name", ""),
                        "id": repo.get("id", ""),
                        "project": client.project,
                        "repo_url": repo.get("webUrl", ""),
                        "default_branch": repo.get("defaultBranch", "refs/heads/main"),
                        "size": repo.get("size", 0),
                        "is_disabled": repo.get("isDisabled", False),
                    }
                )
    return [item for item in repos if not item.get("is_disabled", False)]


def fetch_repository_items(client: AzureDevOpsClient, repo_id: str, branch_name: str) -> List[Dict[str, Any]]:
    url = f"{client.base_url}/git/repositories/{repo_id}/items"
    params = {
        "api-version": API_VERSION,
        "scopePath": "/",
        "recursionLevel": "Full",
        "includeContentMetadata": "true",
        "versionDescriptor.versionType": "branch",
        "versionDescriptor.version": normalize_branch_name(branch_name),
    }
    data = client.get_json(url, params=params)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        values = data.get("value", [])
        return values if isinstance(values, list) else []
    return []


def classify_ui_role(path_value: str, is_folder: bool) -> str:
    low = normalize_repo_path(path_value).lower()
    if is_folder:
        if "/views/" in low:
            return "views_folder"
        if "/components/" in low:
            return "components_folder"
        if "/store/" in low:
            return "store_folder"
        if "/service/" in low or "/services/" in low:
            return "service_folder"
        if "/router/" in low:
            return "router_folder"
        if "/pages/" in low:
            return "pages_folder"
        if "/layouts/" in low or "/layout/" in low:
            return "layout_folder"
        if low.startswith("/src/modules/"):
            return "module_folder"
        return "folder"

    if "/views/" in low:
        return "view_file"
    if "/components/" in low:
        return "component_file"
    if "/store/" in low:
        return "store_file"
    if "/service/" in low or "/services/" in low:
        return "service_file"
    if "/router/" in low:
        return "router_file"
    if "/pages/" in low:
        return "page_file"
    if "/layouts/" in low or "/layout/" in low:
        return "layout_file"
    return "file"


def extract_module_name(path_value: str) -> str:
    match = re.search(r"^/src/modules/([^/]+)", normalize_repo_path(path_value), flags=re.IGNORECASE)
    return (match.group(1) or "").strip() if match else ""


def build_repo_inventory(repo: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    repo_name = str(repo.get("name", ""))
    repo_id = str(repo.get("id", ""))
    branch = normalize_branch_name(str(repo.get("default_branch", "main")))

    entries: List[Dict[str, Any]] = []
    ext_counter: Counter[str] = Counter()
    role_counter: Counter[str] = Counter()
    modules: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "views": [],
            "components": [],
            "services": [],
            "stores": [],
            "routers": [],
            "pages": [],
            "layouts": [],
            "other_files": [],
        }
    )

    for item in items:
        path_value = normalize_repo_path(str(item.get("path", "")))
        if not path_value:
            continue
        git_object_type = str(item.get("gitObjectType", "")).strip().lower()
        is_folder = bool(item.get("isFolder", False)) or git_object_type == "tree"
        ext = "" if is_folder else Path(path_value).suffix.lower()
        role = classify_ui_role(path_value, is_folder)
        module_name = extract_module_name(path_value)

        entry = {
            "path": path_value,
            "name": Path(path_value).name or "/",
            "is_folder": is_folder,
            "git_object_type": git_object_type or ("tree" if is_folder else "blob"),
            "extension": ext,
            "ui_role": role,
            "module_name": module_name,
        }
        entries.append(entry)

        role_counter[role] += 1
        if ext:
            ext_counter[ext] += 1

        if module_name and not is_folder:
            target = modules[module_name]
            if role == "view_file":
                target["views"].append(path_value)
            elif role == "component_file":
                target["components"].append(path_value)
            elif role == "service_file":
                target["services"].append(path_value)
            elif role == "store_file":
                target["stores"].append(path_value)
            elif role == "router_file":
                target["routers"].append(path_value)
            elif role == "page_file":
                target["pages"].append(path_value)
            elif role == "layout_file":
                target["layouts"].append(path_value)
            else:
                target["other_files"].append(path_value)

    module_summary = []
    for module_name, payload in sorted(modules.items()):
        module_summary.append(
            {
                "module_name": module_name,
                "views": sorted(payload["views"]),
                "components": sorted(payload["components"]),
                "services": sorted(payload["services"]),
                "stores": sorted(payload["stores"]),
                "routers": sorted(payload["routers"]),
                "pages": sorted(payload["pages"]),
                "layouts": sorted(payload["layouts"]),
                "other_files": sorted(payload["other_files"]),
            }
        )

    return {
        "repo_name": repo_name,
        "repo_id": repo_id,
        "default_branch": branch,
        "repo_url": str(repo.get("repo_url", "")),
        "items_count": len(entries),
        "folders_count": sum(1 for e in entries if e["is_folder"]),
        "files_count": sum(1 for e in entries if not e["is_folder"]),
        "extensions": dict(sorted(ext_counter.items())),
        "ui_roles": dict(sorted(role_counter.items())),
        "modules": module_summary,
        "entries": entries,
    }


def run_parallel(items: Iterable[Any], worker_fn, max_workers: int, label: str) -> List[Any]:
    items = list(items)
    results: List[Any] = []
    if not items:
        return results

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {executor.submit(worker_fn, item): item for item in items}
        for idx, future in enumerate(as_completed(futures), start=1):
            item = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                item_label = item.get("name") if isinstance(item, dict) else str(item)
                print(f"  ! {label} failed for {item_label}: {exc}")
            if idx % 10 == 0 or idx == len(items):
                print(f"  - {label}: {idx}/{len(items)} done")
    return results


def extract_inventory_for_repo(client: AzureDevOpsClient, repo: Dict[str, Any]) -> Dict[str, Any]:
    items = fetch_repository_items(
        client=client,
        repo_id=str(repo.get("id", "")),
        branch_name=str(repo.get("default_branch", "main")),
    )
    return build_repo_inventory(repo, items)


def build_summary(inventory: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_repos = len(inventory)
    total_files = 0
    total_folders = 0
    repos_with_modules = 0
    repos_with_views = 0
    repos_with_components = 0
    roles_counter: Counter[str] = Counter()
    ext_counter: Counter[str] = Counter()

    repo_summaries: List[Dict[str, Any]] = []
    for repo in inventory:
        total_files += int(repo.get("files_count", 0))
        total_folders += int(repo.get("folders_count", 0))
        modules = repo.get("modules", []) or []
        if modules:
            repos_with_modules += 1
        has_views = any(m.get("views") for m in modules)
        has_components = any(m.get("components") for m in modules)
        if has_views:
            repos_with_views += 1
        if has_components:
            repos_with_components += 1

        roles_counter.update(repo.get("ui_roles", {}))
        ext_counter.update(repo.get("extensions", {}))

        repo_summaries.append(
            {
                "repo_name": repo.get("repo_name", ""),
                "items_count": repo.get("items_count", 0),
                "folders_count": repo.get("folders_count", 0),
                "files_count": repo.get("files_count", 0),
                "modules_count": len(modules),
                "has_views": has_views,
                "has_components": has_components,
            }
        )

    repo_summaries.sort(key=lambda x: (-int(x["modules_count"]), x["repo_name"]))
    return {
        "repos_count": total_repos,
        "total_files": total_files,
        "total_folders": total_folders,
        "repos_with_modules": repos_with_modules,
        "repos_with_views": repos_with_views,
        "repos_with_components": repos_with_components,
        "ui_roles": dict(sorted(roles_counter.items())),
        "extensions": dict(sorted(ext_counter.items())),
        "repos": repo_summaries,
    }


def main() -> None:
    print("MESX.0 - Full repository inventory extraction")
    config = load_config()
    ensure_pat(config["pat"])

    client = AzureDevOpsClient(config)
    repositories = fetch_repositories(client)
    print(f"Repositories to inventory: {len(repositories)}")

    inventory = run_parallel(
        repositories,
        lambda repo: extract_inventory_for_repo(client, repo),
        max_workers=max(1, int(config["max_workers"])),
        label="repo inventory",
    )
    inventory = [item for item in inventory if isinstance(item, dict)]
    inventory.sort(key=lambda x: x.get("repo_name", ""))

    summary = build_summary(inventory)

    safe_json_dump(INVENTORY_PATH, inventory)
    safe_json_dump(SUMMARY_PATH, summary)

    print("\nSaved files:")
    print(f"  - {INVENTORY_PATH}")
    print(f"  - {SUMMARY_PATH}")
    print("\nSummary:")
    print(f"  - Repos: {summary['repos_count']}")
    print(f"  - Total folders: {summary['total_folders']}")
    print(f"  - Total files: {summary['total_files']}")
    print(f"  - Repos with modules/: {summary['repos_with_modules']}")
    print(f"  - Repos with views/: {summary['repos_with_views']}")
    print(f"  - Repos with components/: {summary['repos_with_components']}")


if __name__ == "__main__":
    main()
