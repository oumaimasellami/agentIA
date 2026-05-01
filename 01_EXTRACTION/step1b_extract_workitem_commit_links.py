#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MEX.0 - ETAPE 1B : EXTRACTION DES LIENS WORKITEM -> COMMIT

Objectif:
- Extraire les liens WorkItem -> Commit depuis Azure DevOps (relations natives)
- Ajouter un fallback par detection de references WI dans les messages de commit (ex: AB#127835)
- Enrichir work_items.json avec linked_commit_ids et linked_commit_sources

Fichiers d'entree:
- ./work_items.json (prioritaire)
- ./commits.json (prioritaire)

Sorties:
- ./workitem_commit_links.json
- ./work_items_enriched.json (optionnel)
"""

import argparse
import base64
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv


def load_env():
    load_dotenv()
    org = os.getenv("AZURE_ORG", "faurecia-cloud")
    project = os.getenv("AZURE_PROJECT", "MES_X.0")
    pat = os.getenv("AZURE_PAT", "")
    return org, project, pat


def make_auth_headers(pat: str) -> Dict[str, str]:
    # Azure DevOps Basic auth uses base64(:PAT)
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("utf-8")
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def read_json_candidates(paths: List[Path]):
    for p in paths:
        if p.exists() and p.is_file():
            with p.open("r", encoding="utf-8") as f:
                return json.load(f), p
    return None, None


def normalize_commit_id(value: str) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    m = re.search(r"\b[0-9a-fA-F]{40}\b", value)
    return m.group(0).lower() if m else ""


def extract_commit_id_from_relation_url(url: str) -> str:
    # Exemples attendus:
    # - vstfs:///Git/Commit/{projectId}%2F{repoId}%2F{commitId}
    # - ...commitId=<sha>
    if not isinstance(url, str) or not url:
        return ""

    # Try direct SHA search first
    commit_id = normalize_commit_id(url)
    if commit_id:
        return commit_id

    # Fallback: parse last token after %2F
    if "%2F" in url:
        tail = url.split("%2F")[-1]
        return normalize_commit_id(tail)

    return ""


def fetch_workitem_relations(base_url: str, wi_id: int, headers: Dict[str, str], timeout: int = 30):
    url = f"{base_url}/wit/workitems/{wi_id}"
    params = {"$expand": "relations", "api-version": "7.0"}
    response = requests.get(url, headers=headers, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return data.get("relations", [])


def fetch_workitem_commits_safe(base_url: str, wi_id: int, headers: Dict[str, str], timeout: int, verify_tls):
    try:
        url = f"{base_url}/wit/workitems/{wi_id}"
        params = {"$expand": "relations", "api-version": "7.0"}
        response = requests.get(url, headers=headers, params=params, timeout=timeout, verify=verify_tls)
        response.raise_for_status()
        relations = response.json().get("relations", [])
        commits = extract_commits_from_relations(relations)
        return wi_id, commits, None
    except Exception as exc:
        return wi_id, set(), str(exc)


def extract_commits_from_relations(relations: List[Dict]) -> Set[str]:
    commits = set()
    for rel in relations:
        url = rel.get("url", "")
        rel_type = rel.get("rel", "")
        attrs = rel.get("attributes", {}) or {}
        name = str(attrs.get("name", "")).lower()

        # Keep only artifact links that likely target git/commit
        if "artifactlink" not in rel_type.lower() and "artifactlink" not in name:
            if "commit" not in url.lower() and "git" not in url.lower():
                continue

        commit_id = extract_commit_id_from_relation_url(url)
        if commit_id:
            commits.add(commit_id)
    return commits


def build_commit_message_index(commits: List[Dict]) -> Dict[int, Set[str]]:
    """Index commit IDs by referenced workitem IDs found in messages (AB#12345, #12345, WI 12345)."""
    index: Dict[int, Set[str]] = defaultdict(set)

    if not isinstance(commits, list):
        return index

    for c in commits:
        commit_id = normalize_commit_id(str(c.get("commit_id", "")))
        if not commit_id:
            continue

        message = str(c.get("message", ""))
        if not message:
            continue

        patterns = [
            r"AB#(\d{4,8})",
            r"\bWI\s*(\d{4,8})\b",
            r"\bWork\s*Item\s*(\d{4,8})\b",
        ]

        wi_ids = set()
        for pat in patterns:
            for m in re.findall(pat, message, flags=re.IGNORECASE):
                try:
                    wi_ids.add(int(m))
                except ValueError:
                    continue

        for wi_id in wi_ids:
            index[wi_id].add(commit_id)

    return index


def extract_links(
    workitems: List[Dict],
    commits: List[Dict],
    org: str,
    project: str,
    pat: str,
    offline: bool,
    timeout: int,
    workers: int,
    max_workitems: int,
    verify_tls,
) -> Tuple[Dict[int, Dict], Dict[str, int]]:
    base_url = f"https://dev.azure.com/{org}/{project}/_apis"
    headers = make_auth_headers(pat) if pat else {}

    msg_index = build_commit_message_index(commits)

    result: Dict[int, Dict] = {}
    stats = {
        "total_workitems": len(workitems),
        "with_ado_links": 0,
        "with_message_links": 0,
        "with_any_links": 0,
        "ado_errors": 0,
        "ado_error_samples": [],
    }

    parsed_workitems = []
    for wi in workitems:
        wi_id = wi.get("id")
        try:
            wi_id = int(wi_id)
        except (TypeError, ValueError):
            continue
        parsed_workitems.append(wi_id)

    if max_workitems > 0:
        parsed_workitems = parsed_workitems[:max_workitems]

    stats["total_workitems"] = len(parsed_workitems)

    # Phase A: fetch ADO relations in parallel for speed.
    ado_links_map: Dict[int, Set[str]] = {wi_id: set() for wi_id in parsed_workitems}
    if not offline and parsed_workitems:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(fetch_workitem_commits_safe, base_url, wi_id, headers, timeout, verify_tls): wi_id
                for wi_id in parsed_workitems
            }

            done = 0
            for fut in as_completed(futures):
                done += 1
                wi_id, ado_commits, err = fut.result()
                if err:
                    stats["ado_errors"] += 1
                    if len(stats["ado_error_samples"]) < 5:
                        stats["ado_error_samples"].append({
                            "workitem_id": wi_id,
                            "error": err,
                        })
                ado_links_map[wi_id] = ado_commits
                if done % 50 == 0:
                    print(f"  - fetched ADO links {done}/{len(parsed_workitems)} workitems")

    # Phase B: merge ADO + commit-message fallback.
    for idx, wi_id in enumerate(parsed_workitems, start=1):
        ado_commits = ado_links_map.get(wi_id, set())
        msg_commits = msg_index.get(wi_id, set())

        sources = {}
        for c in sorted(ado_commits):
            sources[c] = "ado_relation"
        for c in sorted(msg_commits):
            if c in sources:
                sources[c] = "ado_relation+commit_message"
            else:
                sources[c] = "commit_message"

        linked = sorted(sources.keys())

        if ado_commits:
            stats["with_ado_links"] += 1
        if msg_commits:
            stats["with_message_links"] += 1
        if linked:
            stats["with_any_links"] += 1

        result[wi_id] = {
            "workitem_id": wi_id,
            "linked_commit_ids": linked,
            "linked_commit_count": len(linked),
            "linked_commit_sources": sources,
        }

        if idx % 50 == 0:
            print(f"  - processed {idx}/{len(parsed_workitems)} workitems")

    return result, stats


def enrich_workitems(workitems: List[Dict], links: Dict[int, Dict]) -> List[Dict]:
    enriched = []
    for wi in workitems:
        wi_copy = dict(wi)
        try:
            wi_id = int(wi_copy.get("id"))
        except (TypeError, ValueError):
            enriched.append(wi_copy)
            continue

        link_obj = links.get(wi_id, {})
        wi_copy["linked_commit_ids"] = link_obj.get("linked_commit_ids", [])
        wi_copy["linked_commit_count"] = link_obj.get("linked_commit_count", 0)
        wi_copy["linked_commit_sources"] = link_obj.get("linked_commit_sources", {})
        enriched.append(wi_copy)

    return enriched


def main():
    parser = argparse.ArgumentParser(description="Extract WorkItem->Commit links from Azure DevOps")
    parser.add_argument("--offline", action="store_true", help="Skip Azure DevOps API calls and use commit-message fallback only")
    parser.add_argument("--no-enrich", action="store_true", help="Do not generate work_items_enriched.json")
    parser.add_argument("--timeout", type=int, default=12, help="HTTP timeout in seconds for Azure DevOps calls (default: 12)")
    parser.add_argument("--workers", type=int, default=10, help="Parallel workers for Azure DevOps calls (default: 10)")
    parser.add_argument("--max-workitems", type=int, default=0, help="Limit number of workitems for quick tests (0 = all)")
    parser.add_argument("--ca-bundle", default="", help="Path to CA bundle file for TLS verification (corporate certificates)")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification (last resort)")
    args = parser.parse_args()

    org, project, pat = load_env()

    extraction_dir = Path(__file__).resolve().parent
    repo_root = extraction_dir.parent

    workitems, wi_path = read_json_candidates([
        extraction_dir / "work_items.json",
        repo_root / "work_items.json",
        Path("work_items.json"),
        Path("../work_items.json"),
    ])
    if not isinstance(workitems, list):
        raise FileNotFoundError("work_items.json introuvable ou invalide")

    commits, commits_path = read_json_candidates([
        extraction_dir / "commits.json",
        repo_root / "04_VISUALIZATION" / "commits.json",
        repo_root / "01_EXTRACTION" / "commits.json",
        repo_root / "commits.json",
        Path("commits.json"),
    ])
    if not isinstance(commits, list):
        commits = []

    if not args.offline and not pat:
        raise ValueError("AZURE_PAT manquant dans .env. Utilise --offline si tu veux tester sans API.")

    verify_tls = True
    if args.insecure:
        verify_tls = False
        requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
    elif args.ca_bundle:
        verify_tls = args.ca_bundle

    env_ca = os.getenv("AZURE_CA_BUNDLE", "").strip()
    if verify_tls is True and env_ca:
        verify_tls = env_ca

    print("=" * 80)
    print("MEX.0 - ETAPE 1B : EXTRACTION WORKITEM -> COMMIT")
    print("=" * 80)
    print(f"Org: {org}")
    print(f"Project: {project}")
    print(f"Work items source: {wi_path}")
    print(f"Commits source: {commits_path}")
    print(f"Mode offline: {args.offline}")
    print(f"Timeout: {args.timeout}s | Workers: {args.workers} | Max workitems: {args.max_workitems if args.max_workitems > 0 else 'all'}")
    print(f"TLS verify: {verify_tls}")

    links, stats = extract_links(
        workitems=workitems,
        commits=commits,
        org=org,
        project=project,
        pat=pat,
        offline=args.offline,
        timeout=max(3, args.timeout),
        workers=max(1, args.workers),
        max_workitems=max(0, args.max_workitems),
        verify_tls=verify_tls,
    )

    links_path = extraction_dir / "workitem_commit_links.json"
    with links_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "metadata": {
                    "org": org,
                    "project": project,
                    "offline": args.offline,
                    "stats": stats,
                },
                "links": list(links.values()),
            },
            f,
            indent=2,
            ensure_ascii=True,
        )

    print("\nStats:")
    print(f"  - total_workitems: {stats['total_workitems']}")
    print(f"  - with_ado_links: {stats['with_ado_links']}")
    print(f"  - with_message_links: {stats['with_message_links']}")
    print(f"  - with_any_links: {stats['with_any_links']}")
    print(f"  - ado_errors: {stats['ado_errors']}")
    if stats.get("ado_error_samples"):
        print("  - ado_error_samples (first 5):")
        for sample in stats["ado_error_samples"]:
            print(f"      WI {sample['workitem_id']}: {sample['error'][:180]}")
    print(f"\nSaved: {links_path}")

    if not args.no_enrich:
        enriched = enrich_workitems(workitems, links)
        enriched_path = extraction_dir / "work_items_enriched.json"
        with enriched_path.open("w", encoding="utf-8") as f:
            json.dump(enriched, f, indent=2, ensure_ascii=True)
        print(f"Saved: {enriched_path}")


if __name__ == "__main__":
    main()
