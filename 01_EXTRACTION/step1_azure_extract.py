#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MESX.0 - Step 1: Azure DevOps extraction for real development traceability

Outputs:
  - microservices.json
  - commits.json
  - pull_requests.json
  - work_items.json
  - workitem_dev_links.json

Traceability model targeted by this extractor:
  WorkItem -> LINKED_TO_PULL_REQUEST -> PullRequest -> CONTAINS_COMMIT -> Commit -> MODIFIES -> Microservice
  WorkItem -> LINKED_TO_COMMIT -> Commit
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import unquote

import requests
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR
API_VERSION = "7.0"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_WORKERS = 8
DEFAULT_TOP = 200
DEFAULT_MAX_RETRIES = 6
DEFAULT_REQUEST_DELAY_MS = 0


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


def build_auth_header(pat: str) -> Dict[str, str]:
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("utf-8")
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


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


def normalize_commit_id(value: str) -> str:
    if not isinstance(value, str):
        return ""
    match = re.search(r"\b[0-9a-fA-F]{40}\b", value)
    return match.group(0).lower() if match else ""


def normalize_branch_name(value: str) -> str:
    if not isinstance(value, str):
        return "main"
    value = value.strip()
    if value.startswith("refs/heads/"):
        return value[len("refs/heads/"):]
    return value or "main"


def extract_pr_id(value: str) -> Optional[int]:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    match = re.search(r"\b(\d{3,})\b", value)
    return int(match.group(1)) if match else None


def extract_artifact_ids(rel_url: str) -> Dict[str, Optional[str]]:
    result = {"commit_id": None, "pr_id": None, "repo_id": None, "project_id": None}
    if not isinstance(rel_url, str):
        return result

    commit_id = normalize_commit_id(rel_url) or None
    pr_id = None
    repo_id = None
    project_id = None

    decoded = unquote(rel_url)

    # Native work item artifact formats:
    # - vstfs:///Git/PullRequestId/{projectId}/{repoId}/{prId}
    # - vstfs:///Git/Commit/{projectId}/{repoId}/{commitId}
    pr_artifact_match = re.search(
        r"vstfs:///Git/PullRequestId/([^/]+)/([^/]+)/(\d+)",
        decoded,
        flags=re.IGNORECASE,
    )
    if pr_artifact_match:
        project_id, repo_id, pr_id = pr_artifact_match.group(1), pr_artifact_match.group(2), pr_artifact_match.group(3)

    commit_artifact_match = re.search(
        r"vstfs:///Git/Commit/([^/]+)/([^/]+)/([0-9a-fA-F]{40})",
        decoded,
        flags=re.IGNORECASE,
    )
    if commit_artifact_match:
        project_id = project_id or commit_artifact_match.group(1)
        repo_id = repo_id or commit_artifact_match.group(2)
        commit_id = normalize_commit_id(commit_artifact_match.group(3)) or commit_id

    pr_match = re.search(r"PullRequestId%2F(\d+)", rel_url, flags=re.IGNORECASE)
    if pr_match:
        pr_id = pr_match.group(1)
    else:
        pr_match = re.search(r"pullrequest/(\d+)", rel_url, flags=re.IGNORECASE)
        if pr_match:
            pr_id = pr_match.group(1)
        else:
            # Common Azure artifact format:
            # vstfs:///Git/PullRequestId/{projectId}%2F{repoId}%2F{prId}
            # and some variants with "/" separators.
            pr_match = re.search(
                r"PullRequestId(?:%2F|/)[^%/]+(?:%2F|/)[^%/]+(?:%2F|/)(\d+)",
                rel_url,
                flags=re.IGNORECASE,
            )
            if pr_match:
                pr_id = pr_match.group(1)
            else:
                # Last fallback: if URL clearly references PullRequestId, keep the last numeric token.
                if "pullrequestid" in rel_url.lower():
                    numbers = re.findall(r"\d+", rel_url)
                    if numbers:
                        pr_id = numbers[-1]

    # REST URL forms (repo can be extracted from path)
    rest_pr_match = re.search(
        r"/repositories/([^/]+)/pullrequests/(\d+)",
        rel_url,
        flags=re.IGNORECASE,
    )
    if rest_pr_match:
        repo_id = repo_id or rest_pr_match.group(1)
        pr_id = pr_id or rest_pr_match.group(2)

    rest_commit_match = re.search(
        r"/repositories/([^/]+)/commits/([0-9a-fA-F]{40})",
        rel_url,
        flags=re.IGNORECASE,
    )
    if rest_commit_match:
        repo_id = repo_id or rest_commit_match.group(1)
        commit_id = commit_id or normalize_commit_id(rest_commit_match.group(2))

    result["commit_id"] = commit_id
    result["pr_id"] = pr_id
    result["repo_id"] = repo_id
    result["project_id"] = project_id
    return result


@dataclass
class AzureDevOpsClient:
    org: str
    project: str
    pat: str
    timeout: int = DEFAULT_TIMEOUT
    verify: Any = True
    disable_env_proxy: bool = False
    max_retries: int = DEFAULT_MAX_RETRIES
    request_delay_ms: int = DEFAULT_REQUEST_DELAY_MS

    def __post_init__(self) -> None:
        self.base_url = f"https://dev.azure.com/{self.org}/{self.project}/_apis"
        self.session = requests.Session()
        if self.disable_env_proxy:
            # Ignore HTTP(S)_PROXY / ALL_PROXY from environment.
            self.session.trust_env = False
        self.session.headers.update(build_auth_header(self.pat))

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.verify)
        attempts = max(0, int(self.max_retries)) + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            if self.request_delay_ms > 0:
                time.sleep(self.request_delay_ms / 1000.0)
            try:
                response = self.session.request(method, url, **kwargs)
            except Exception as exc:
                last_error = exc
                backoff = min(60, 2 ** min(6, attempt))
                if attempt < attempts:
                    time.sleep(backoff)
                    continue
                raise

            # Handle throttling / transient service unavailability with retry.
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

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self.request("GET", url, params=params)
        return response.json()

    def get_json_with_headers(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        response = self.request("GET", url, params=params)
        return response.json(), dict(response.headers)

    def post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self.request("POST", url, json=payload)
        return response.json()


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
                value = future.result()
                if value is None:
                    continue
                if isinstance(value, list):
                    results.extend(value)
                else:
                    results.append(value)
            except Exception as exc:
                item_label = item.get("name") if isinstance(item, dict) else str(item)
                print(f"  ! {label} failed for {item_label}: {exc}")
            if idx % 25 == 0 or idx == len(items):
                print(f"  - {label}: {idx}/{len(items)} done")
    return results


def fetch_all_values_paginated(
    client: AzureDevOpsClient,
    url: str,
    params: Optional[Dict[str, Any]],
    label: str,
) -> List[Dict[str, Any]]:
    all_values: List[Dict[str, Any]] = []
    continuation_token: Optional[str] = None
    page = 0

    while True:
        page += 1
        page_params = dict(params or {})
        if continuation_token:
            page_params["continuationToken"] = continuation_token

        data, headers = client.get_json_with_headers(url, params=page_params)
        values = data.get("value", [])
        if not isinstance(values, list):
            values = []

        all_values.extend(values)
        if page % 10 == 0:
            print(f"  - {label}: page {page}, total {len(all_values)}")

        header_token = (
            headers.get("x-ms-continuationtoken")
            or headers.get("X-MS-ContinuationToken")
            or headers.get("x-ms-continuationToken")
        )
        body_token = data.get("continuationToken")
        continuation_token = str(header_token or body_token or "").strip() or None

        if not continuation_token or not values:
            break

    return all_values


def fetch_repositories(client: AzureDevOpsClient) -> List[Dict[str, Any]]:
    print("\n[1/5] Fetching repositories...")
    data = client.get_json(f"{client.base_url}/git/repositories", params={"api-version": API_VERSION})
    repositories = []

    for repo in data.get("value", []):
        repositories.append({
            "name": repo.get("name", ""),
            "id": repo.get("id", ""),
            "project": client.project,
            "repo_url": repo.get("webUrl", ""),
            "default_branch": repo.get("defaultBranch", "refs/heads/main"),
            "size": repo.get("size", 0),
            "is_disabled": repo.get("isDisabled", False),
        })

    print(f"  Retrieved {len(repositories)} repositories")
    return repositories


def fetch_commit_changes(
    client: AzureDevOpsClient,
    repo_id: str,
    commit_id: str,
) -> List[Dict[str, str]]:
    url = f"{client.base_url}/git/repositories/{repo_id}/commits/{commit_id}/changes"
    try:
        data = client.get_json(url, params={"api-version": API_VERSION, "$top": 1000})
    except requests.RequestException:
        return []

    changes = []
    for change in data.get("changes", []):
        path = change.get("item", {}).get("path", "")
        if not path:
            continue
        changes.append({
            "fichier": path,
            "action": str(change.get("changeType", "")).lower(),
        })
    return changes


def fetch_commits_for_repository(
    client: AzureDevOpsClient,
    repo: Dict[str, Any],
) -> List[Dict[str, Any]]:
    repo_id = repo["id"]
    service_name = repo["name"]
    print(f"  - commits for {service_name}")

    url = f"{client.base_url}/git/repositories/{repo_id}/commits"
    params = {
        "api-version": API_VERSION,
        "$top": 1000,
        "searchCriteria.itemVersion.version": normalize_branch_name(repo.get("default_branch", "main")),
        "searchCriteria.itemVersion.versionType": "branch",
    }
    all_commit_items = fetch_all_values_paginated(
        client,
        url,
        params=params,
        label=f"commits pages for {service_name}",
    )
    commits = []

    for commit in all_commit_items:
        commit_id = commit.get("commitId", "")
        commits.append(build_commit_record(client, commit, repo_id, service_name))

    return commits


def fetch_commits(
    client: AzureDevOpsClient,
    repositories: List[Dict[str, Any]],
    max_workers: int,
) -> List[Dict[str, Any]]:
    print("\n[2/5] Fetching commits...")
    all_commits = run_parallel(
        repositories,
        lambda repo: fetch_commits_for_repository(client, repo),
        max_workers=max_workers,
        label="commit extraction",
    )
    print(f"  Retrieved {len(all_commits)} commits")
    return all_commits


def build_commit_record(
    client: AzureDevOpsClient,
    commit_payload: Dict[str, Any],
    repo_id: str,
    service_name: str,
) -> Dict[str, Any]:
    commit_id = normalize_commit_id(commit_payload.get("commitId", ""))
    return {
        "commit_id": commit_id,
        "repo_id": repo_id,
        "microservice": service_name,
        "auteur": commit_payload.get("author", {}).get("name", ""),
        "email": commit_payload.get("author", {}).get("email", ""),
        "date": commit_payload.get("author", {}).get("date", ""),
        "message": commit_payload.get("comment", ""),
        "url": commit_payload.get("url", ""),
        "remote_url": commit_payload.get("remoteUrl", ""),
        "fichiers_modifies": fetch_commit_changes(client, repo_id, commit_id) if commit_id else [],
    }


def fetch_pr_commits(client: AzureDevOpsClient, repo_id: str, pr_id: int) -> List[str]:
    url = f"{client.base_url}/git/repositories/{repo_id}/pullRequests/{pr_id}/commits"
    try:
        data = client.get_json(url, params={"api-version": API_VERSION, "$top": 1000})
    except requests.RequestException:
        return []

    commit_ids = []
    for commit in data.get("value", []):
        commit_id = normalize_commit_id(commit.get("commitId", ""))
        if commit_id:
            commit_ids.append(commit_id)
    return commit_ids


def fetch_pr_workitems(client: AzureDevOpsClient, repo_id: str, pr_id: int) -> List[int]:
    url = f"{client.base_url}/git/repositories/{repo_id}/pullRequests/{pr_id}/workitems"
    try:
        data = client.get_json(url, params={"api-version": API_VERSION, "$top": 1000})
    except requests.RequestException:
        return []

    workitem_ids = []
    for wi in data.get("value", []):
        wi_id = wi.get("id")
        try:
            workitem_ids.append(int(wi_id))
        except (TypeError, ValueError):
            continue
    return sorted(set(workitem_ids))


def fetch_pull_request_details(
    client: AzureDevOpsClient,
    repo_id: str,
    service_name: str,
    pr: Dict[str, Any],
) -> Dict[str, Any] | None:
    pr_id = pr.get("pullRequestId")
    if pr_id is None:
        return None

    commit_ids = fetch_pr_commits(client, repo_id, int(pr_id))
    linked_workitem_ids = fetch_pr_workitems(client, repo_id, int(pr_id))
    return {
        "pr_id": int(pr_id),
        "repo_id": repo_id,
        "microservice": service_name,
        "titre": pr.get("title", ""),
        "description": pr.get("description", ""),
        "auteur": pr.get("createdBy", {}).get("displayName", ""),
        "statut": pr.get("status", "").lower(),
        "date_creation": pr.get("creationDate", ""),
        "date_fermeture": pr.get("closedDate", ""),
        "source_branch": pr.get("sourceRefName", ""),
        "target_branch": pr.get("targetRefName", ""),
        "url": pr.get("url", ""),
        "is_draft": bool(pr.get("isDraft", False)),
        "merge_status": pr.get("mergeStatus", ""),
        "last_merge_commit": normalize_commit_id(pr.get("lastMergeCommit", {}).get("commitId", "")),
        "commit_ids": commit_ids,
        "linked_workitem_ids": linked_workitem_ids,
    }


def fetch_pull_request_by_id(
    client: AzureDevOpsClient,
    repo_id: str,
    pr_id: int,
    repo_name_by_id: Dict[str, str],
) -> Dict[str, Any] | None:
    url = f"{client.base_url}/git/repositories/{repo_id}/pullrequests/{pr_id}"
    try:
        pr = client.get_json(
            url,
            params={
                "api-version": API_VERSION,
                "includeCommits": "true",
                "includeWorkItemRefs": "true",
            },
        )
    except requests.RequestException:
        return None

    service_name = repo_name_by_id.get(repo_id, "")
    commit_ids = fetch_pr_commits(client, repo_id, int(pr_id))
    linked_workitem_ids = fetch_pr_workitems(client, repo_id, int(pr_id))
    return {
        "pr_id": int(pr_id),
        "repo_id": repo_id,
        "microservice": service_name,
        "titre": pr.get("title", ""),
        "description": pr.get("description", ""),
        "auteur": pr.get("createdBy", {}).get("displayName", ""),
        "statut": pr.get("status", "").lower(),
        "date_creation": pr.get("creationDate", ""),
        "date_fermeture": pr.get("closedDate", ""),
        "source_branch": pr.get("sourceRefName", ""),
        "target_branch": pr.get("targetRefName", ""),
        "url": pr.get("url", ""),
        "is_draft": bool(pr.get("isDraft", False)),
        "merge_status": pr.get("mergeStatus", ""),
        "last_merge_commit": normalize_commit_id(pr.get("lastMergeCommit", {}).get("commitId", "")),
        "commit_ids": commit_ids,
        "linked_workitem_ids": linked_workitem_ids,
    }


def fetch_commit_by_id(
    client: AzureDevOpsClient,
    repo_id: str,
    commit_id: str,
    repo_name_by_id: Dict[str, str],
) -> Dict[str, Any] | None:
    normalized = normalize_commit_id(commit_id)
    if not normalized:
        return None
    url = f"{client.base_url}/git/repositories/{repo_id}/commits/{normalized}"
    try:
        payload = client.get_json(url, params={"api-version": API_VERSION})
    except requests.RequestException:
        return None
    return build_commit_record(client, payload, repo_id, repo_name_by_id.get(repo_id, ""))


def fetch_pull_requests_for_repository(
    client: AzureDevOpsClient,
    repo: Dict[str, Any],
    max_workers: int,
) -> List[Dict[str, Any]]:
    repo_id = repo["id"]
    service_name = repo["name"]
    print(f"  - PRs for {service_name}")

    url = f"{client.base_url}/git/repositories/{repo_id}/pullrequests"
    params = {
        "api-version": API_VERSION,
        "searchCriteria.status": "all",
        "$top": 1000,
    }
    all_pr_items = fetch_all_values_paginated(
        client,
        url,
        params=params,
        label=f"PR pages for {service_name}",
    )
    return run_parallel(
        all_pr_items,
        lambda pr: fetch_pull_request_details(client, repo_id, service_name, pr),
        max_workers=max(2, max_workers),
        label=f"PR enrichment for {service_name}",
    )


def fetch_pull_requests(
    client: AzureDevOpsClient,
    repositories: List[Dict[str, Any]],
    max_workers: int,
) -> List[Dict[str, Any]]:
    print("\n[3/5] Fetching pull requests...")
    all_pull_requests = run_parallel(
        repositories,
        lambda repo: fetch_pull_requests_for_repository(client, repo, max_workers=max(2, max_workers // 2)),
        max_workers=max_workers,
        label="pull request extraction",
    )
    print(f"  Retrieved {len(all_pull_requests)} pull requests")
    return all_pull_requests


def fetch_workitem_details(client: AzureDevOpsClient, workitem_id: int) -> Dict[str, Any]:
    url = f"{client.base_url}/wit/workitems/{workitem_id}"
    params = {"api-version": API_VERSION, "$expand": "relations"}
    data = client.get_json(url, params=params)
    fields = data.get("fields", {})
    relations = data.get("relations", []) or []

    linked_pr_ids = set()
    linked_commit_ids = set()
    linked_pr_refs = set()
    linked_commit_refs = set()

    for relation in relations:
        ids = extract_artifact_ids(relation.get("url", ""))
        attrs = relation.get("attributes", {}) or {}
        rel_name = str(attrs.get("name", ""))

        # Fallback parse from relation display name when URL format is unusual:
        # e.g. "Pull Request 64003", "Commit 9248416", etc.
        if not ids["pr_id"]:
            name_pr = re.search(r"\bPull\s*Request\s*(\d+)\b", rel_name, flags=re.IGNORECASE)
            if name_pr:
                ids["pr_id"] = name_pr.group(1)

        if ids["pr_id"]:
            pr_id_int = int(ids["pr_id"])
            linked_pr_ids.add(pr_id_int)
            if ids.get("repo_id"):
                linked_pr_refs.add((ids.get("project_id") or "", ids["repo_id"], pr_id_int))
        if ids["commit_id"]:
            commit_norm = normalize_commit_id(ids["commit_id"])
            if commit_norm:
                linked_commit_ids.add(commit_norm)
                if ids.get("repo_id"):
                    linked_commit_refs.add((ids.get("project_id") or "", ids["repo_id"], commit_norm))

    assigned_to = fields.get("System.AssignedTo", "")
    if isinstance(assigned_to, dict):
        assigned_to = assigned_to.get("displayName", "")

    return {
        "id": workitem_id,
        "type": fields.get("System.WorkItemType", ""),
        "titre": fields.get("System.Title", ""),
        "description": fields.get("System.Description", ""),
        "statut": fields.get("System.State", ""),
        "priorite": fields.get("Microsoft.VSTS.Common.Priority", 0),
        "area_path": fields.get("System.AreaPath", ""),
        "iteration_path": fields.get("System.IterationPath", ""),
        "tags": fields.get("System.Tags", ""),
        "assigne_a": assigned_to,
        "date_creation": fields.get("System.CreatedDate", ""),
        "linked_pull_request_ids": sorted(linked_pr_ids),
        "linked_commit_ids": sorted(linked_commit_ids),
        "linked_pull_request_refs": [
            {"project_id": p, "repo_id": r, "pr_id": pr}
            for (p, r, pr) in sorted(linked_pr_refs, key=lambda x: x[2])
        ],
        "linked_commit_refs": [
            {"project_id": p, "repo_id": r, "commit_id": c}
            for (p, r, c) in sorted(linked_commit_refs, key=lambda x: x[2])
        ],
    }


def fetch_work_items(client: AzureDevOpsClient, max_workers: int) -> List[Dict[str, Any]]:
    print("\n[4/5] Fetching work items...")

    wiql = {
        "query": (
            "SELECT [System.Id] "
            "FROM WorkItems "
            f"WHERE [System.TeamProject] = '{client.project}' "
            "AND [System.WorkItemType] IN ('User Story', 'Bug', 'Feature', 'Task', 'Test Case')"
        )
    }

    url = f"{client.base_url}/wit/wiql?api-version={API_VERSION}"
    data = client.post_json(url, wiql)
    workitem_ids = [int(item["id"]) for item in data.get("workItems", [])]
    work_items = run_parallel(
        workitem_ids,
        lambda workitem_id: fetch_workitem_details(client, workitem_id),
        max_workers=max_workers,
        label="work item detail extraction",
    )
    print(f"  Retrieved {len(work_items)} work items")
    return work_items


def build_workitem_dev_links(
    work_items: List[Dict[str, Any]],
    pull_requests: List[Dict[str, Any]],
    commits: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    print("\n[5/5] Building work item development links...")

    pr_by_workitem: Dict[int, set] = defaultdict(set)
    commit_by_workitem: Dict[int, set] = defaultdict(set)
    commit_to_prs: Dict[str, set] = defaultdict(set)

    for pr in pull_requests:
        pr_id = int(pr["pr_id"])
        for commit_id in pr.get("commit_ids", []):
            if commit_id:
                commit_to_prs[commit_id].add(pr_id)
        for workitem_id in pr.get("linked_workitem_ids", []):
            pr_by_workitem[int(workitem_id)].add(pr_id)
            for commit_id in pr.get("commit_ids", []):
                if commit_id:
                    commit_by_workitem[int(workitem_id)].add(commit_id)

    for work_item in work_items:
        wi_id = int(work_item["id"])
        for pr_id in work_item.get("linked_pull_request_ids", []):
            pr_by_workitem[wi_id].add(int(pr_id))
        for commit_id in work_item.get("linked_commit_ids", []):
            if commit_id:
                commit_by_workitem[wi_id].add(commit_id)
                for pr_id in commit_to_prs.get(commit_id, set()):
                    pr_by_workitem[wi_id].add(pr_id)

    links = []
    for work_item in work_items:
        wi_id = int(work_item["id"])
        linked_pr_ids = sorted(pr_by_workitem.get(wi_id, set()))
        linked_commit_ids = sorted(commit_by_workitem.get(wi_id, set()))
        links.append({
            "workitem_id": wi_id,
            "linked_pull_request_ids": linked_pr_ids,
            "linked_pull_request_count": len(linked_pr_ids),
            "linked_commit_ids": linked_commit_ids,
            "linked_commit_count": len(linked_commit_ids),
        })

    print(f"  Built links for {len(links)} work items")
    return links


def ensure_traceability_records(
    client: AzureDevOpsClient,
    repositories: List[Dict[str, Any]],
    work_items: List[Dict[str, Any]],
    pull_requests: List[Dict[str, Any]],
    commits: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Azure-only backfill:
    Ensure PR/Commit records referenced by WorkItem development links exist in extracted datasets.
    No heuristic inference; only native WorkItem ArtifactLink references are used.
    """
    repo_name_by_id = {str(r.get("id", "")): str(r.get("name", "")) for r in repositories}

    existing_pr_ids = {int(pr.get("pr_id")) for pr in pull_requests if pr.get("pr_id") is not None}
    existing_commit_ids = {
        normalize_commit_id(c.get("commit_id", ""))
        for c in commits
        if normalize_commit_id(c.get("commit_id", ""))
    }

    missing_pr_refs: Dict[tuple[str, int], Dict[str, Any]] = {}
    missing_commit_refs: Dict[tuple[str, str], Dict[str, Any]] = {}

    for wi in work_items:
        for ref in wi.get("linked_pull_request_refs", []) or []:
            repo_id = str(ref.get("repo_id", "")).strip()
            pr_id = ref.get("pr_id")
            if not repo_id or pr_id is None:
                continue
            try:
                pr_id_int = int(pr_id)
            except Exception:
                continue
            if pr_id_int not in existing_pr_ids:
                missing_pr_refs[(repo_id, pr_id_int)] = {"repo_id": repo_id, "pr_id": pr_id_int}

        for ref in wi.get("linked_commit_refs", []) or []:
            repo_id = str(ref.get("repo_id", "")).strip()
            commit_id = normalize_commit_id(str(ref.get("commit_id", "")))
            if not repo_id or not commit_id:
                continue
            if commit_id not in existing_commit_ids:
                missing_commit_refs[(repo_id, commit_id)] = {"repo_id": repo_id, "commit_id": commit_id}

    if missing_pr_refs:
        print(f"\n[Backfill] Missing PR records from WorkItem links: {len(missing_pr_refs)}")
    for ref in missing_pr_refs.values():
        pr = fetch_pull_request_by_id(client, ref["repo_id"], ref["pr_id"], repo_name_by_id)
        if not pr:
            continue
        if pr["pr_id"] not in existing_pr_ids:
            pull_requests.append(pr)
            existing_pr_ids.add(pr["pr_id"])

        for commit_id in pr.get("commit_ids", []) or []:
            commit_norm = normalize_commit_id(commit_id)
            if commit_norm and commit_norm not in existing_commit_ids:
                key = (str(pr.get("repo_id", "")), commit_norm)
                missing_commit_refs[key] = {"repo_id": str(pr.get("repo_id", "")), "commit_id": commit_norm}

    if missing_commit_refs:
        print(f"[Backfill] Missing Commit records from WorkItem/PR links: {len(missing_commit_refs)}")
    for ref in missing_commit_refs.values():
        commit = fetch_commit_by_id(client, ref["repo_id"], ref["commit_id"], repo_name_by_id)
        if not commit:
            continue
        commit_norm = normalize_commit_id(commit.get("commit_id", ""))
        if commit_norm and commit_norm not in existing_commit_ids:
            commits.append(commit)
            existing_commit_ids.add(commit_norm)

    print(f"[Backfill] Final PR count: {len(pull_requests)} | Commit count: {len(commits)}")
    return pull_requests, commits


def main() -> None:
    config = load_config()
    ensure_pat(config["pat"])

    verify: Any = True
    if config["insecure_tls"]:
        verify = False
        requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
    elif config["ca_bundle"]:
        verify = config["ca_bundle"]

    client = AzureDevOpsClient(
        org=config["org"],
        project=config["project"],
        pat=config["pat"],
        timeout=config["timeout"],
        verify=verify,
        disable_env_proxy=config["disable_env_proxy"],
        max_retries=config["max_retries"],
        request_delay_ms=config["request_delay_ms"],
    )

    print("=" * 80)
    print("MESX.0 - Azure DevOps extraction")
    print("=" * 80)
    print(f"Organization: {config['org']}")
    print(f"Project:      {config['project']}")
    print(f"Disable env proxy: {config['disable_env_proxy']}")
    print(f"Max workers:  {config['max_workers']}")
    print(f"Max retries:  {config['max_retries']}")
    print(f"Req delay ms: {config['request_delay_ms']}")

    started_at = time.time()
    repositories = fetch_repositories(client)

    commits = fetch_commits(client, repositories, max_workers=config["max_workers"])

    pull_requests = fetch_pull_requests(client, repositories, max_workers=config["max_workers"])

    work_items = fetch_work_items(client, max_workers=config["max_workers"])

    # Ensure extraction completeness for strict traceability:
    # WorkItem native links must have corresponding PR/Commit objects in datasets.
    pull_requests, commits = ensure_traceability_records(
        client=client,
        repositories=repositories,
        work_items=work_items,
        pull_requests=pull_requests,
        commits=commits,
    )

    safe_json_dump(ROOT_DIR / "microservices.json", repositories)
    safe_json_dump(ROOT_DIR / "commits.json", commits)
    safe_json_dump(ROOT_DIR / "pull_requests.json", pull_requests)
    safe_json_dump(OUTPUT_DIR / "work_items.json", work_items)

    workitem_dev_links = build_workitem_dev_links(work_items, pull_requests, commits)
    safe_json_dump(OUTPUT_DIR / "workitem_dev_links.json", workitem_dev_links)

    print("\nSaved files:")
    print(f"  - {ROOT_DIR / 'microservices.json'}")
    print(f"  - {ROOT_DIR / 'commits.json'}")
    print(f"  - {ROOT_DIR / 'pull_requests.json'}")
    print(f"  - {OUTPUT_DIR / 'work_items.json'}")
    print(f"  - {OUTPUT_DIR / 'workitem_dev_links.json'}")
    print(f"\nDone in {time.time() - started_at:.1f}s")


if __name__ == "__main__":
    main()
