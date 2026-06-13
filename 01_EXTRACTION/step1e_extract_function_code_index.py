#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MESX.0 - Step 1e: Extract real function code bodies from source snapshots

Goal:
- keep the current pipeline untouched
- attach real source-code excerpts to already parsed functions
- provide stronger evidence to understand what a touched function does

Outputs:
- 01_EXTRACTION/function_code_index.json

Sources:
- 01_EXTRACTION/graph_data.json
- source_mesx_*.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ROOT_DIR.parent
GRAPH_DATA_PATH = ROOT_DIR / "graph_data.json"
OUTPUT_PATH = ROOT_DIR / "function_code_index.json"

RE_TEMPLATE_BLOCK = re.compile(r"<template\b[^>]*>(.*?)</template>", re.IGNORECASE | re.DOTALL)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_source_lookup() -> Dict[Tuple[str, str], Dict[str, Any]]:
    lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for source_file in sorted(REPO_ROOT.glob("source_mesx_*.json")):
        entries = load_json(source_file, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            microservice = str(entry.get("microservice", "")).strip()
            path_value = str(entry.get("chemin", "")).strip()
            if not microservice or not path_value:
                continue
            lookup[(microservice, path_value)] = entry
    return lookup


def slice_lines(content: str, line_start: int, line_end: int) -> str:
    if not isinstance(content, str) or not content:
        return ""
    lines = content.splitlines()
    if line_start <= 0 or line_end <= 0 or line_end < line_start:
        return ""
    start_idx = max(0, line_start - 1)
    end_idx = min(len(lines), line_end)
    if start_idx >= len(lines):
        return ""
    return "\n".join(lines[start_idx:end_idx]).strip()


def slice_context(content: str, line_start: int, line_end: int, before: int = 4, after: int = 4) -> str:
    if not isinstance(content, str) or not content:
        return ""
    lines = content.splitlines()
    if line_start <= 0:
        return ""
    start_idx = max(0, line_start - 1 - before)
    end_idx = min(len(lines), max(line_end, line_start) + after)
    return "\n".join(lines[start_idx:end_idx]).strip()


def extract_template_usages(content: str, function_name: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(content, str) or not content or not function_name:
        return out

    template_match = RE_TEMPLATE_BLOCK.search(content)
    if not template_match:
        return out

    template = template_match.group(1) or ""
    if not template.strip():
        return out

    lines = template.splitlines()
    patterns = [
        ("click", re.compile(rf"@click\s*=\s*['\"][^'\"]*\b{re.escape(function_name)}\b[^'\"]*['\"]", re.IGNORECASE)),
        ("change", re.compile(rf"@change\s*=\s*['\"][^'\"]*\b{re.escape(function_name)}\b[^'\"]*['\"]", re.IGNORECASE)),
        ("submit", re.compile(rf"@submit\s*=\s*['\"][^'\"]*\b{re.escape(function_name)}\b[^'\"]*['\"]", re.IGNORECASE)),
        ("input", re.compile(rf"@input\s*=\s*['\"][^'\"]*\b{re.escape(function_name)}\b[^'\"]*['\"]", re.IGNORECASE)),
        ("keyup", re.compile(rf"@keyup[^\=]*=\s*['\"][^'\"]*\b{re.escape(function_name)}\b[^'\"]*['\"]", re.IGNORECASE)),
        ("if", re.compile(rf"v-if\s*=\s*['\"][^'\"]*\b{re.escape(function_name)}\b[^'\"]*['\"]", re.IGNORECASE)),
        ("show", re.compile(rf"v-show\s*=\s*['\"][^'\"]*\b{re.escape(function_name)}\b[^'\"]*['\"]", re.IGNORECASE)),
        ("disabled", re.compile(rf":disabled\s*=\s*['\"][^'\"]*\b{re.escape(function_name)}\b[^'\"]*['\"]", re.IGNORECASE)),
    ]

    for idx, line in enumerate(lines, start=1):
        raw = line.strip()
        if not raw:
            continue
        for event_type, regex in patterns:
            if regex.search(raw):
                out.append(
                    {
                        "binding_type": event_type,
                        "template_line": str(idx),
                        "template_snippet": raw,
                    }
                )
    return out


def derive_ui_behavior(function_name: str, template_usages: List[Dict[str, str]]) -> List[str]:
    behavior: List[str] = []
    lowered = function_name.lower()

    if "delete" in lowered or "remove" in lowered:
        behavior.append("delete_or_remove_action")
    if "create" in lowered or "add" in lowered:
        behavior.append("create_or_add_action")
    if "update" in lowered or "edit" in lowered or "save" in lowered:
        behavior.append("update_or_save_action")
    if "display" in lowered or "show" in lowered or "open" in lowered:
        behavior.append("display_or_open_action")
    if "close" in lowered or "cancel" in lowered:
        behavior.append("close_or_cancel_action")
    if "select" in lowered or "check" in lowered:
        behavior.append("selection_action")
    if "upload" in lowered:
        behavior.append("upload_action")
    if "download" in lowered:
        behavior.append("download_action")
    if "filter" in lowered or "search" in lowered:
        behavior.append("filter_or_search_action")

    for usage in template_usages:
        binding_type = usage.get("binding_type", "")
        if binding_type:
            behavior.append(f"template_binding:{binding_type}")

    # Keep order but deduplicate
    seen = set()
    ordered = []
    for item in behavior:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def main() -> None:
    print("MESX.0 - Function code index extraction")

    graph_data = load_json(GRAPH_DATA_PATH, {})
    functions = graph_data.get("fonctions", []) if isinstance(graph_data, dict) else []
    if not isinstance(functions, list):
        functions = []

    source_lookup = build_source_lookup()
    print(f"Functions in graph_data: {len(functions)}")
    print(f"Source snapshot entries indexed: {len(source_lookup)}")

    output: List[Dict[str, Any]] = []
    missing_source = 0
    with_code = 0

    for fn in functions:
        if not isinstance(fn, dict):
            continue
        microservice = str(fn.get("microservice", "")).strip()
        file_path = str(fn.get("fichier", "")).strip()
        function_name = str(fn.get("nom", "")).strip()
        line_start = int(fn.get("line_start") or 0)
        line_end = int(fn.get("line_end") or 0)

        source_entry = source_lookup.get((microservice, file_path))
        content = str(source_entry.get("contenu", "")) if source_entry else ""
        extension = str(source_entry.get("extension", "")) if source_entry else ""
        code_excerpt = slice_lines(content, line_start, line_end)
        context_excerpt = slice_context(content, line_start, line_end) if code_excerpt else ""
        template_usages = extract_template_usages(content, function_name) if extension == ".vue" else []
        ui_behavior = derive_ui_behavior(function_name, template_usages)

        if not source_entry:
            missing_source += 1
        if code_excerpt:
            with_code += 1

        output.append(
            {
                "id": fn.get("id", ""),
                "microservice": microservice,
                "file_path": file_path,
                "extension": extension,
                "function_name": function_name,
                "function_type": fn.get("type", ""),
                "http_method": fn.get("http_method", ""),
                "http_path": fn.get("path", ""),
                "line_start": line_start,
                "line_end": line_end,
                "source_found": bool(source_entry),
                "code_excerpt": code_excerpt,
                "context_excerpt": context_excerpt,
                "template_usages": template_usages,
                "ui_behavior": ui_behavior,
            }
        )

    save_json(OUTPUT_PATH, output)

    print("\nSaved files:")
    print(f"  - {OUTPUT_PATH}")
    print("\nSummary:")
    print(f"  - Functions processed: {len(output)}")
    print(f"  - Functions with source found: {len(output) - missing_source}")
    print(f"  - Functions with code excerpt: {with_code}")
    print(f"  - Functions missing source snapshot entry: {missing_source}")


if __name__ == "__main__":
    main()
