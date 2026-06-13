#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Populate graph_data.json with real code functions parsed from source_mesx_*.json.

Strict rules:
- function catalog comes from source code only
- no COVERS heuristic generation
- each function keeps microservice + file path + stable id
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
EXTRACTION_DIR = Path(__file__).resolve().parent
GRAPH_DATA_PATH = EXTRACTION_DIR / "graph_data.json"

CODE_EXTENSIONS = {".java", ".ts", ".js", ".vue", ".tsx", ".jsx"}
EXCLUDED_PATH_TOKENS = (
    "/test/",
    "/tests/",
    ".spec.",
    ".mock.",
    "__tests__",
    "/node_modules/",
    "/dist/",
    "/build/",
    "/docs/",
    "/documentation/",
)

RE_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
RE_DECORATOR = re.compile(r"@(Get|Post|Put|Delete|Patch)\s*\(\s*[\"'`]?([^\"'`)\\s]*)", re.IGNORECASE)
RE_TS_FN = re.compile(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)\s*\(")
RE_TS_ARROW = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"
)
RE_TS_METHOD = re.compile(
    r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:async\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*\{",
    re.MULTILINE,
)
RE_JAVA_MAPPING = re.compile(
    r"@(Get|Post|Put|Delete|Patch|Request)Mapping\s*(?:\(\s*(?:value\s*=\s*)?[\"']([^\"']*)[\"'])?",
    re.IGNORECASE,
)
RE_JAVA_METHOD = re.compile(
    r"(?:public|protected)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],\s]+\s+([A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)

TS_EXCLUDED_NAMES = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "constructor",
    "return",
    "else",
    "try",
}


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    last_error: Exception | None = None
    for _ in range(6):
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, path)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    if last_error:
        raise last_error


def normalize_symbol(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", text or "")
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def stable_function_id(microservice: str, file_path: str, function_name: str) -> str:
    stem = normalize_symbol(Path(file_path or "").stem.lower())
    name = normalize_symbol(function_name)
    return f"{microservice}::{stem}::{name}"


def should_parse_file(path: str, ext: str) -> bool:
    low = (path or "").lower()
    if ext not in CODE_EXTENSIONS:
        return False
    return not any(token in low for token in EXCLUDED_PATH_TOKENS)


def line_number_from_offset(content: str, offset: int) -> int:
    safe_offset = max(0, min(len(content), int(offset)))
    return content.count("\n", 0, safe_offset) + 1


def find_block_end_line(content: str, decl_offset: int, fallback_line: int) -> int:
    if not content:
        return fallback_line
    start = max(0, min(len(content), int(decl_offset)))
    brace_pos = content.find("{", start)
    if brace_pos == -1:
        return fallback_line

    depth = 0
    for idx in range(brace_pos, len(content)):
        ch = content[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return line_number_from_offset(content, idx)
    return fallback_line


def parse_ts_like(content: str, line_offset: int = 0) -> List[Tuple[str, str, str, str, int, int]]:
    """
    Returns tuples: (name, fn_type, http_method, http_path, line_start, line_end)
    """
    out: List[Tuple[str, str, str, str, int, int]] = []
    seen = set()

    for m in RE_DECORATOR.finditer(content):
        http = (m.group(1) or "").upper()
        route = m.group(2) or "/"
        snippet = content[m.end() : m.end() + 240]
        fn = re.search(r"(?:async\s+)?([A-Za-z_]\w*)\s*\(", snippet)
        name = fn.group(1) if fn else f"{http}_{route.replace('/', '_').strip('_') or 'root'}"
        key = ("endpoint", name)
        if key in seen:
            continue
        seen.add(key)
        decl_offset = m.end() + (fn.start() if fn else 0)
        line_start = line_offset + line_number_from_offset(content, decl_offset)
        line_end = line_offset + find_block_end_line(content, decl_offset, line_start)
        out.append((name, "endpoint", http, route, line_start, line_end))

    for regex, fn_type in ((RE_TS_FN, "function"), (RE_TS_ARROW, "function"), (RE_TS_METHOD, "method")):
        for m in regex.finditer(content):
            name = (m.group(1) or "").strip()
            if not name:
                continue
            if name.lower() in TS_EXCLUDED_NAMES:
                continue
            if name.startswith("get") and len(name) > 3:
                continue
            if name.startswith("set") and len(name) > 3:
                continue
            key = (fn_type, name)
            if key in seen:
                continue
            seen.add(key)
            decl_offset = m.start(1)
            line_start = line_offset + line_number_from_offset(content, decl_offset)
            line_end = line_offset + find_block_end_line(content, m.end(), line_start)
            out.append((name, fn_type, "", "", line_start, line_end))
    return out


def parse_java(content: str) -> List[Tuple[str, str, str, str, int, int]]:
    out: List[Tuple[str, str, str, str, int, int]] = []
    seen = set()

    for m in RE_JAVA_MAPPING.finditer(content):
        http = (m.group(1) or "").upper()
        route = m.group(2) or "/"
        snippet = content[m.end() : m.end() + 240]
        fn = re.search(r"(?:public|private|protected)\s+\S+\s+([A-Za-z_]\w*)\s*\(", snippet)
        name = fn.group(1) if fn else f"{http}_{route.replace('/', '_').strip('_') or 'root'}"
        key = ("endpoint", name)
        if key in seen:
            continue
        seen.add(key)
        decl_offset = m.end() + (fn.start() if fn else 0)
        line_start = line_number_from_offset(content, decl_offset)
        line_end = find_block_end_line(content, decl_offset, line_start)
        out.append((name, "endpoint", http, route, line_start, line_end))

    for m in RE_JAVA_METHOD.finditer(content):
        name = (m.group(1) or "").strip()
        if not name or name.startswith("get") or name.startswith("set"):
            continue
        key = ("method", name)
        if key in seen:
            continue
        seen.add(key)
        decl_offset = m.start(1)
        line_start = line_number_from_offset(content, decl_offset)
        line_end = find_block_end_line(content, m.end(), line_start)
        out.append((name, "method", "", "", line_start, line_end))

    return out


def parse_functions_from_entry(entry: Dict[str, str]) -> List[Dict[str, str]]:
    microservice = entry.get("microservice", "")
    file_path = entry.get("chemin", "")
    content = entry.get("contenu", "")
    ext = str(entry.get("extension", "")).lower()
    if not microservice or not file_path or not isinstance(content, str):
        return []
    if len(content) < 30:
        return []
    if not should_parse_file(file_path, ext):
        return []

    tuples: List[Tuple[str, str, str, str, int, int]] = []
    if ext in {".ts", ".js", ".tsx", ".jsx"}:
        tuples = parse_ts_like(content)
    elif ext == ".vue":
        for block_match in RE_SCRIPT_BLOCK.finditer(content):
            block = block_match.group(1) or ""
            block_line_offset = line_number_from_offset(content, block_match.start(1)) - 1
            tuples.extend(parse_ts_like(block, line_offset=block_line_offset))
    elif ext == ".java":
        tuples = parse_java(content)

    results = []
    for name, fn_type, http_method, http_path, line_start, line_end in tuples:
        results.append(
            {
                "id": stable_function_id(microservice, file_path, name),
                "nom": name,
                "classe": Path(file_path).stem,
                "package": "",
                "type": fn_type,
                "http_method": http_method,
                "path": http_path,
                "microservice": microservice,
                "fichier": file_path,
                "line_start": int(line_start),
                "line_end": int(line_end),
                "description": "parsed_from_source_code",
            }
        )
    return results


def extract_all_functions() -> List[Dict[str, str]]:
    functions: Dict[str, Dict[str, str]] = {}
    source_files = sorted(ROOT_DIR.glob("source_mesx_*.json"))
    print(f"Source files found: {len(source_files)}")

    for source_file in source_files:
        entries = load_json(source_file, [])
        for entry in entries:
            for fn in parse_functions_from_entry(entry):
                if fn["id"] not in functions:
                    functions[fn["id"]] = fn
    return list(functions.values())


def count_by_microservice(functions: Iterable[Dict[str, str]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for fn in functions:
        ms = fn.get("microservice", "")
        if not ms:
            continue
        out[ms] = out.get(ms, 0) + 1
    return out


def populate_graph_data() -> bool:
    graph_data = load_json(GRAPH_DATA_PATH, {})
    if not graph_data:
        print("graph_data.json not found or empty")
        return False

    functions = extract_all_functions()
    by_ms = count_by_microservice(functions)

    graph_data["fonctions"] = functions
    graph_data["relations_covers"] = []
    if "use_cases" not in graph_data:
        graph_data["use_cases"] = []
    save_json(GRAPH_DATA_PATH, graph_data)

    print("\ngraph_data.json updated (strict source parsing):")
    print(f"  Functions: {len(functions)}")
    print(f"  Microservices with functions: {len(by_ms)}")
    print(f"  mesx-downtime-ui: {by_ms.get('mesx-downtime-ui', 0)}")
    print("  COVERS: 0 (disabled)")
    return True


if __name__ == "__main__":
    print("\n" + "=" * 72)
    print("Populate graph_data.json from source_mesx_*.json (strict mode)")
    print("=" * 72)
    ok = populate_graph_data()
    if ok:
        print("\nNext step:")
        print("  D:\\Python\\bin\\python.exe ..\\02_NEO4J_DATABASE\\build_graph_database.py")
