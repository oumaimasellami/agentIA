#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backfill changed_lines for selected WorkItems, or for all WorkItems already linked to commits.

This is a fast validation path for P1/P2 classification:
WorkItem -> direct commits + PR commits -> Azure commit file diff -> commits.json changed_lines.
It does not rebuild snapshots, graph_data, inventory, or Neo4j.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

import requests

ROOT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from step1_azure_extract import (  # noqa: E402
    API_VERSION,
    AzureDevOpsClient,
    ensure_pat,
    fetch_commit_changes,
    load_config,
    normalize_commit_id,
    safe_json_dump,
)


COMMITS_PATH = ROOT_DIR / "commits.json"
WORKITEM_LINKS_PATH = ROOT_DIR / "workitem_dev_links.json"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def parse_workitem_ids(argv_values: Iterable[str]) -> List[int]:
    ids: List[int] = []
    for raw in argv_values:
        for part in str(raw or "").replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ids.append(int(part))
            except ValueError:
                raise SystemExit(f"Invalid workitem id: {part}")
    return sorted(set(ids))


def build_client(config: Dict[str, Any]) -> AzureDevOpsClient:
    ensure_pat(config["pat"])
    verify: Any = True
    if config["insecure_tls"]:
        verify = False
        requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
    elif config["ca_bundle"]:
        verify = config["ca_bundle"]

    return AzureDevOpsClient(
        org=config["org"],
        project=config["project"],
        pat=config["pat"],
        timeout=config["timeout"],
        verify=verify,
        disable_env_proxy=config["disable_env_proxy"],
        max_retries=config["max_retries"],
        request_delay_ms=config["request_delay_ms"],
    )


def apply_network_runtime_defaults() -> None:
    # Mirror the refresh pipeline behavior so manual runs use the same Azure connectivity settings.
    if not str(Path(".")).strip():
        return
    if not (Path.cwd() / ".env").exists() and not (PROJECT_ROOT / ".env").exists():
        return
    import os
    if not os.getenv("AZURE_INSECURE_TLS"):
        os.environ["AZURE_INSECURE_TLS"] = "true"
    if not os.getenv("AZURE_DISABLE_ENV_PROXY"):
        os.environ["AZURE_DISABLE_ENV_PROXY"] = "true"


def all_workitem_ids_with_commits() -> List[int]:
    links = load_json(WORKITEM_LINKS_PATH, [])
    ids: List[int] = []
    for row in links:
        if not row.get("linked_commit_ids"):
            continue
        try:
            ids.append(int(row.get("workitem_id")))
        except (TypeError, ValueError):
            continue
    return sorted(set(ids))


def commit_ids_for_workitems(workitem_ids: List[int]) -> Set[str]:
    links = load_json(WORKITEM_LINKS_PATH, [])
    selected = set(workitem_ids)
    out: Set[str] = set()
    for row in links:
        try:
            wid = int(row.get("workitem_id"))
        except (TypeError, ValueError):
            continue
        if wid not in selected:
            continue
        for cid in row.get("linked_commit_ids", []) or []:
            norm = normalize_commit_id(str(cid))
            if norm:
                out.add(norm)
    return out


def fetch_parent_commit_id(client: AzureDevOpsClient, repo_id: str, commit_id: str) -> str:
    if not repo_id or not commit_id:
        return ""
    url = f"{client.base_url}/git/repositories/{repo_id}/commits/{commit_id}"
    try:
        payload = client.get_json(url, params={"api-version": API_VERSION})
    except requests.RequestException:
        return ""
    parents = payload.get("parents", []) or []
    return normalize_commit_id(parents[0]) if parents else ""


def has_any_changed_lines(files: List[Dict[str, Any]]) -> bool:
    return any(bool(item.get("changed_lines")) for item in files or [])


def backfill_changed_lines(workitem_ids: List[int], max_commits: int = 0, sleep_ms: int = 0) -> int:
    apply_network_runtime_defaults()
    config = load_config()
    client = build_client(config)
    commits = load_json(COMMITS_PATH, [])
    if not isinstance(commits, list):
        raise SystemExit(f"Invalid commits.json format: {COMMITS_PATH}")

    selected_commits = commit_ids_for_workitems(workitem_ids)
    if not selected_commits:
        print(f"No linked commits found for WorkItems: {', '.join(map(str, workitem_ids))}")
        return 0

    commit_by_id = {
        normalize_commit_id(str(item.get("commit_id", ""))): item
        for item in commits
        if isinstance(item, dict) and normalize_commit_id(str(item.get("commit_id", "")))
    }

    print("=" * 80)
    print("MESX.0 - targeted changed_lines backfill")
    print("=" * 80)
    if len(workitem_ids) <= 25:
        print(f"WorkItems: {', '.join(map(str, workitem_ids))}")
    else:
        print(f"WorkItems: {len(workitem_ids)} selected")
    print(f"Linked commits: {len(selected_commits)}")
    if max_commits > 0:
        print(f"Run limit: {max_commits} commit(s)")
    print(f"Max files per commit: {config['commit_diff_max_files_per_commit']}")
    print(f"Max lines per file: {config['commit_diff_max_lines_per_file']}")
    print(f"Sleep between commits: {sleep_ms} ms")
    print(f"Disable env proxy: {config['disable_env_proxy']}")
    print(f"Insecure TLS: {config['insecure_tls']}")

    updated = 0
    skipped = 0
    missing = 0
    started = time.time()

    processed_network_calls = 0
    for idx, commit_id in enumerate(sorted(selected_commits), start=1):
        record = commit_by_id.get(commit_id)
        if not record:
            missing += 1
            print(f"  [{idx}/{len(selected_commits)}] missing commit in commits.json: {commit_id[:8]}")
            continue

        files = record.get("fichiers_modifies", []) or []
        if has_any_changed_lines(files):
            skipped += 1
            if idx <= 20 or idx % 100 == 0:
                print(f"  [{idx}/{len(selected_commits)}] already has changed_lines: {commit_id[:8]}")
            continue

        if max_commits > 0 and processed_network_calls >= max_commits:
            print(f"  Reached run limit ({max_commits} commit(s)); stop cleanly.")
            break

        repo_id = str(record.get("repo_id") or "").strip()
        parent_commit_id = normalize_commit_id(str(record.get("parent_commit_id") or ""))
        if not parent_commit_id:
            parent_commit_id = fetch_parent_commit_id(client, repo_id, commit_id)
            if parent_commit_id:
                record["parent_commit_id"] = parent_commit_id

        if not repo_id or not parent_commit_id:
            skipped += 1
            print(f"  [{idx}/{len(selected_commits)}] skip no parent/repo: {commit_id[:8]}")
            continue

        processed_network_calls += 1
        changes = fetch_commit_changes(
            client=client,
            repo_id=repo_id,
            commit_id=commit_id,
            parent_commit_id=parent_commit_id,
            include_diff_lines=True,
            max_files_for_diff=int(config["commit_diff_max_files_per_commit"]),
            max_lines_per_file=int(config["commit_diff_max_lines_per_file"]),
        )
        if not changes:
            skipped += 1
            print(f"  [{idx}/{len(selected_commits)}] no changes returned: {commit_id[:8]}")
            continue

        record["fichiers_modifies"] = changes
        updated += 1
        line_files = sum(1 for item in changes if item.get("changed_lines"))
        print(f"  [{idx}/{len(selected_commits)}] updated {commit_id[:8]} ({line_files} file(s) with lines)")
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

    if updated:
        safe_json_dump(COMMITS_PATH, commits)

    print("\nSummary:")
    print(f"  Updated commits: {updated}")
    print(f"  Already enriched/skipped: {skipped}")
    print(f"  Missing commits: {missing}")
    print(f"  Azure diff calls this run: {processed_network_calls}")
    print(f"  Output: {COMMITS_PATH}")
    print(f"  Done in {time.time() - started:.1f}s")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill changed_lines for selected WorkItems or all linked WorkItems.")
    parser.add_argument("workitem_ids", nargs="*", help="WorkItem ids, comma-separated or space-separated.")
    parser.add_argument("--all", action="store_true", help="Process all WorkItems that already have linked commits.")
    parser.add_argument("--max-commits", type=int, default=0, help="Maximum Azure diff calls for this run. 0 means no limit.")
    parser.add_argument("--sleep-ms", type=int, default=0, help="Pause between Azure diff calls to reduce throttling.")
    args = parser.parse_args()
    workitem_ids = all_workitem_ids_with_commits() if args.all else parse_workitem_ids(args.workitem_ids)
    if not workitem_ids:
        raise SystemExit("Please provide at least one WorkItem id, or use --all.")
    backfill_changed_lines(workitem_ids, max_commits=max(0, args.max_commits), sleep_ms=max(0, args.sleep_ms))


if __name__ == "__main__":
    main()
