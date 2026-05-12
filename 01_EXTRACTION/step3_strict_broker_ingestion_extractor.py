#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Strict extraction of inter-microservice async dependencies from Azure source files.

Rules:
- source of truth: source_mesx_*.json only
- no hardcoded relationship list
- no confidence score, no "known pattern"
- relations are emitted only when an explicit producer/consumer topic coupling exists
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
EXTRACTION_DIR = ROOT_DIR / "01_EXTRACTION"
MICROSERVICES_FILE = EXTRACTION_DIR / "microservices.json"
OUTPUT_FILE = EXTRACTION_DIR / "broker_ingestion_relations.json"


BROKER_DETECTORS = {
    "Azure Event Hubs": [
        r"bindings\.azure\.eventhubs",
        r"pubsub\.azure\.eventhubs",
        r"eventhub",
    ],
    "Azure Service Bus": [
        r"pubsub\.azure\.servicebus",
        r"bindings\.azure\.servicebusqueues",
        r"servicebus",
    ],
    "Redis Stream": [
        r"redis",
        r"stream",
        r"pubsub",
    ],
}

INGESTION_NAME_MARKERS = (
    "-ingestion",
    "-materialized-view",
    "-mv-processor",
    "-router",
    "-actor",
    "-datahub",
)


@dataclass(frozen=True)
class Signal:
    microservice: str
    broker_type: str
    direction: str  # INPUT / OUTPUT
    topic: str
    file_path: str
    source_detected: str


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_topic(value: str) -> str:
    v = (value or "").strip().strip("\"'`")
    if not v:
        return ""
    if len(v) > 200:
        return ""
    return v.lower()


def normalize_topic_token(value: str) -> str:
    """
    Normalize variable-like topic references to a canonical key.
    Example:
      TOPIC_PLANTS -> var:topic_plants
      this.pubSubService.topicPlants -> var:topicplants
      ${this.eventhubService.topicPlants} -> var:topicplants
    """
    raw = (value or "").strip().strip("\"'`").strip("${}").strip()
    if not raw:
        return ""
    # Keep only letters, digits and underscore for stable matching.
    normalized = re.sub(r"[^a-zA-Z0-9_]", "", raw).lower()
    if "topic" not in normalized:
        return ""
    if normalized in {"topic", "topics"}:
        return ""
    return f"var:{normalized}"


def detect_broker_type(content_lower: str) -> str | None:
    if re.search(r"bindings\.azure\.eventhubs|pubsub\.azure\.eventhubs|eventhub", content_lower):
        return "Azure Event Hubs"
    if re.search(r"pubsub\.azure\.servicebus|bindings\.azure\.servicebusqueues|servicebus", content_lower):
        return "Azure Service Bus"
    if "redis" in content_lower and re.search(r"stream|pubsub", content_lower):
        return "Redis Stream"
    return None


def extract_literal_topics(content: str) -> Set[str]:
    topics: Set[str] = set()
    patterns = [
        r"\btopic\s*[:=]\s*[\"'`]([^\"'`]+)[\"'`]",
        r"\btopics\s*[:=]\s*\[([^\]]+)\]",
        r"\bqueue\s*[:=]\s*[\"'`]([^\"'`]+)[\"'`]",
        r"\beventHub\s*[:=]\s*[\"'`]([^\"'`]+)[\"'`]",
        r"\broute\s*:\s*[`\"']/([^`\"']+)[`\"']",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, content, flags=re.IGNORECASE):
            if isinstance(match, tuple):
                for part in match:
                    norm = normalize_topic(part)
                    if norm:
                        topics.add(norm)
                continue
            text = str(match)
            # topics list case: "a", "b"
            if "," in text and "[" not in text and "]" not in text:
                for part in text.split(","):
                    norm = normalize_topic(part)
                    if norm:
                        topics.add(norm)
                continue
            norm = normalize_topic(text)
            if norm:
                topics.add(norm)

    # Explicit publish-like calls with literal topic as first argument.
    publish_literal = re.findall(
        r"\bpublish[A-Za-z0-9_]*\(\s*[\"'`]([^\"'`]+)[\"'`]",
        content,
        flags=re.IGNORECASE,
    )
    for val in publish_literal:
        norm = normalize_topic(str(val))
        if norm:
            topics.add(norm)
    return topics


def extract_variable_topics(content: str) -> Set[str]:
    topics: Set[str] = set()

    patterns = [
        # process.env.TOPIC_X / process.env.SOME_TOPIC
        r"process\.env\.([A-Z0-9_]*TOPIC[A-Z0-9_]*)",
        # this.pubSubService.topicPlants / this.eventhubService.topicResult
        r"this\.[A-Za-z0-9_]+\.(topic[A-Za-z0-9_]*)",
        # `${this.pubSubService.topicPlants}`
        r"\$\{\s*this\.[A-Za-z0-9_]+\.(topic[A-Za-z0-9_]*)\s*\}",
        # config/get style keys containing TOPIC
        r"[\"'`]([A-Z0-9_]*TOPIC[A-Z0-9_]*)[\"'`]",
        # publish*/send* calls carrying a topic-like symbol as first argument
        r"\b(?:publish|send)[A-Za-z0-9_]*\(\s*(?:this\.[A-Za-z0-9_]+\.)?(topic[A-Za-z0-9_]+)",
        r"\b(?:publish|send)[A-Za-z0-9_]*\(\s*process\.env\.([A-Z0-9_]*TOPIC[A-Z0-9_]*)",
    ]

    for pattern in patterns:
        for match in re.findall(pattern, content, flags=re.IGNORECASE):
            token = normalize_topic_token(str(match))
            if token:
                topics.add(token)
    return topics


def extract_dapr_component_metadata(content: str) -> Dict[str, str]:
    """
    Extracts pairs from Dapr component YAML blocks:
      - name: eventHub
        value: "dbz...."
    """
    out: Dict[str, str] = {}
    pattern = re.compile(
        r"-\s*name\s*:\s*([A-Za-z0-9_\-]+)\s*[\r\n]+\s*value\s*:\s*[\"']?([^\r\n\"']+)",
        re.IGNORECASE,
    )
    for key, value in pattern.findall(content):
        out[key.strip().lower()] = value.strip()
    return out


def detect_direction(content_lower: str) -> Set[str]:
    directions: Set[str] = set()
    if re.search(r"direction\s*[:=]\s*[\"'`]?input", content_lower):
        directions.add("INPUT")
    if re.search(r"direction\s*[:=]\s*[\"'`]?output", content_lower):
        directions.add("OUTPUT")
    if "/dapr/subscribe" in content_lower or "pubsubname" in content_lower:
        directions.add("INPUT")
    if (
        re.search(r"\bpubsub\.publish\(", content_lower)
        or re.search(r"\bpublish[a-z0-9_]*\(", content_lower)
        or re.search(r"\bsend[a-z0-9_]*\(", content_lower)
    ):
        directions.add("OUTPUT")
    return directions


def is_ingestion_service(ms_name: str) -> bool:
    name = (ms_name or "").lower()
    return any(marker in name for marker in INGESTION_NAME_MARKERS)


def iter_source_entries() -> Iterable[Tuple[str, str, str]]:
    for source_file in sorted(ROOT_DIR.glob("source_mesx_*.json")):
        payload = read_json(source_file, [])
        if not isinstance(payload, list):
            continue
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            ms = str(entry.get("microservice", "")).strip()
            path = str(entry.get("chemin", "")).strip()
            content = str(entry.get("contenu", ""))
            if not ms or not path or not content:
                continue
            yield ms, path, content


def extract_signals(known_microservices: Set[str]) -> List[Signal]:
    signals: List[Signal] = []
    for ms, path, content in iter_source_entries():
        if ms not in known_microservices:
            continue
        lower = content.lower()
        broker_type = detect_broker_type(lower)
        if not broker_type:
            continue
        meta = extract_dapr_component_metadata(content)
        topics = set()
        for key in ("topic", "topics", "eventhub", "queue", "channel"):
            if key in meta:
                topics.add(normalize_topic(meta[key]))
        topics |= extract_literal_topics(content)
        topics |= extract_variable_topics(content)
        topics = {t for t in topics if t}
        directions = detect_direction(lower)
        if "direction" in meta:
            val = meta["direction"].strip().lower()
            if val == "input":
                directions.add("INPUT")
            if val == "output":
                directions.add("OUTPUT")
        if not directions:
            continue
        if not topics:
            continue
        for direction in directions:
            for topic in topics:
                signals.append(
                    Signal(
                        microservice=ms,
                        broker_type=broker_type,
                        direction=direction,
                        topic=topic,
                        file_path=path,
                        source_detected="explicit_source_config",
                    )
                )
    return signals


def build_relations(signals: List[Signal]) -> List[Dict[str, object]]:
    by_key: Dict[Tuple[str, str], Dict[str, Set[Signal]]] = defaultdict(
        lambda: {"INPUT": set(), "OUTPUT": set()}
    )
    for sig in signals:
        by_key[(sig.broker_type, sig.topic)][sig.direction].add(sig)

    message_relations: Dict[Tuple[str, str, str, str], Dict[str, object]] = {}
    ingestion_relations: Dict[Tuple[str, str, str], Dict[str, object]] = {}

    for (broker_type, topic), groups in by_key.items():
        producers = groups["OUTPUT"]
        consumers = groups["INPUT"]
        if not producers or not consumers:
            continue
        for prod in producers:
            for cons in consumers:
                if prod.microservice == cons.microservice:
                    continue
                mkey = (prod.microservice, cons.microservice, "MESSAGE_BROKER", topic)
                if mkey not in message_relations:
                    message_relations[mkey] = {
                        "source": prod.microservice,
                        "cible": cons.microservice,
                        "type": "MESSAGE_BROKER",
                        "broker_type": broker_type,
                        "topics": [topic],
                        "direction": "OUTPUT",
                        "communication_mode": "event-driven",
                        "service_namespace": "",
                        "source_detected": "explicit_topic_coupling",
                        "evidence_mode": "STRICT_SOURCE",
                        "evidence_files": sorted(set([prod.file_path, cons.file_path])),
                    }

                # INGESTION only when consumer is an explicit ingestion-style service
                if is_ingestion_service(cons.microservice):
                    ikey = (prod.microservice, cons.microservice, topic)
                    if ikey not in ingestion_relations:
                        ingestion_relations[ikey] = {
                            "source": prod.microservice,
                            "cible": cons.microservice,
                            "type": "INGESTION",
                            "ingestion_type": "Pipeline/Consumer",
                            "broker_type": broker_type,
                            "topics": [topic],
                            "tables_consumed": [],
                            "direction": "INPUT",
                            "communication_mode": "event-driven",
                            "frequency": "",
                            "source_detected": "explicit_topic_coupling",
                            "evidence_mode": "STRICT_SOURCE",
                            "evidence_files": sorted(set([prod.file_path, cons.file_path])),
                        }

    all_relations = list(message_relations.values()) + list(ingestion_relations.values())
    all_relations.sort(key=lambda r: (r["type"], r["source"], r["cible"]))
    return all_relations


def main() -> None:
    print("\n" + "=" * 80)
    print("Strict Broker/Ingestion Extraction (Azure source evidence only)")
    print("=" * 80)

    microservices = read_json(MICROSERVICES_FILE, [])
    known_microservices = {str(ms.get("name", "")).strip() for ms in microservices if isinstance(ms, dict)}
    known_microservices.discard("")
    print(f"Known microservices: {len(known_microservices)}")

    signals = extract_signals(known_microservices)
    print(f"Signals detected: {len(signals)}")

    relations = build_relations(signals)
    print(f"Relations built: {len(relations)}")
    for rel in relations[:10]:
        topic = (rel.get("topics") or [""])[0]
        print(
            f"  - {rel.get('type')}: {rel.get('source')} -> {rel.get('cible')} "
            f"| {rel.get('broker_type')} | topic={topic}"
        )

    payload = json.dumps(relations, ensure_ascii=False, indent=2)
    try:
        OUTPUT_FILE.write_text(payload, encoding="utf-8")
        print(f"Saved: {OUTPUT_FILE}")
    except PermissionError:
        fallback = ROOT_DIR / "broker_ingestion_relations.strict.json"
        try:
            fallback.write_text(payload, encoding="utf-8")
            print(f"Permission denied on {OUTPUT_FILE}, saved fallback: {fallback}")
        except PermissionError:
            temp_fallback = Path(os.getenv("TEMP", ".")) / "broker_ingestion_relations.strict.json"
            temp_fallback.write_text(payload, encoding="utf-8")
            print(
                f"Permission denied on project files, saved fallback in TEMP: {temp_fallback}"
            )

    message_count = sum(1 for r in relations if r.get("type") == "MESSAGE_BROKER")
    ingestion_count = sum(1 for r in relations if r.get("type") == "INGESTION")
    print(f"MESSAGE_BROKER: {message_count}")
    print(f"INGESTION: {ingestion_count}")


if __name__ == "__main__":
    main()
