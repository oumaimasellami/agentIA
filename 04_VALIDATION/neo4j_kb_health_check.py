import json
from pathlib import Path

from neo4j import GraphDatabase

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "forvia2025"

EXPECTED_LABELS = {"Commit", "WorkItem", "Function", "Microservice"}
EXPECTED_REL_TYPES = {
    "MODIFIES",
    "IMPLEMENTS",
    "COVERS",
    "API_CALLS",
    "MESSAGE_BROKER",
    "INGESTION",
    "TOUCHES_FUNCTION",
    "RELATES_TO_COMMIT",
}


def q_single(session, query):
    row = session.run(query).single()
    return dict(row) if row else {}


def run_health_check(repo_root: Path):
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    with driver.session() as session:
        node_counts = {
            row["label"]: row["c"]
            for row in session.run(
                """
                MATCH (n)
                RETURN labels(n)[0] as label, count(*) as c
                ORDER BY c DESC
                """
            )
        }

        rel_counts = {
            row["rel"]: row["c"]
            for row in session.run(
                """
                MATCH ()-[r]->()
                RETURN type(r) as rel, count(*) as c
                ORDER BY c DESC
                """
            )
        }

        duplicate_checks = {
            "duplicate_workitem_ids": q_single(
                session,
                """
                MATCH (wi:WorkItem)
                WITH wi.id as id, count(*) as c
                WHERE c > 1
                RETURN count(*) as value
                """,
            ).get("value", 0),
            "duplicate_function_ids": q_single(
                session,
                """
                MATCH (f:Function)
                WITH f.id as id, count(*) as c
                WHERE c > 1
                RETURN count(*) as value
                """,
            ).get("value", 0),
            "duplicate_microservice_names": q_single(
                session,
                """
                MATCH (ms:Microservice)
                WITH ms.name as name, count(*) as c
                WHERE c > 1
                RETURN count(*) as value
                """,
            ).get("value", 0),
        }

        rel_endpoint_validity = {
            "modifies_validity": q_single(
                session,
                """
                MATCH ()-[r:MODIFIES]->()
                WITH count(r) as total
                MATCH (:Commit)-[r:MODIFIES]->(:Microservice)
                RETURN total, count(r) as valid
                """,
            ),
            "implements_validity": q_single(
                session,
                """
                MATCH ()-[r:IMPLEMENTS]->()
                WITH count(r) as total
                MATCH (:Microservice)-[r:IMPLEMENTS]->(:Function)
                RETURN total, count(r) as valid
                """,
            ),
            "covers_validity": q_single(
                session,
                """
                MATCH ()-[r:COVERS]->()
                WITH count(r) as total
                MATCH (:Function)-[r:COVERS]->(:WorkItem)
                RETURN total, count(r) as valid
                """,
            ),
        }

        orphan_workitems = q_single(
            session,
            """
            MATCH (wi:WorkItem)
            WHERE NOT (wi)<-[:COVERS]-()
            RETURN count(wi) as value
            """,
        ).get("value", 0)

        workitem_count = node_counts.get("WorkItem", 0)
        orphan_rate = (orphan_workitems / workitem_count) if workitem_count else 0.0

        workitems_without_relates = q_single(
            session,
            """
            MATCH (wi:WorkItem)
            WHERE NOT (wi)-[:RELATES_TO_COMMIT]->()
            RETURN count(wi) as value
            """,
        ).get("value", 0)

        evidence_stats = q_single(
            session,
            """
            MATCH (wi:WorkItem)-[:RELATES_TO_COMMIT]->(c:Commit)-[:TOUCHES_FUNCTION]->(f:Function)
            RETURN count(DISTINCT wi) as wi_with_evidence,
                   count(DISTINCT c) as commits,
                   count(DISTINCT f) as functions
            """,
        )

        dependency_rel_count = q_single(
            session,
            """
            MATCH (:Microservice)-[r:API_CALLS|MESSAGE_BROKER|INGESTION]->(:Microservice)
            RETURN count(r) as value
            """,
        ).get("value", 0)

        unexpected_labels = sorted([label for label in node_counts.keys() if label not in EXPECTED_LABELS])
        unexpected_rel_types = sorted([rel for rel in rel_counts.keys() if rel not in EXPECTED_REL_TYPES])

    driver.close()

    checks = {
        "no_duplicate_identifiers": all(v == 0 for v in duplicate_checks.values()),
        "valid_core_relation_endpoints": all(
            (x.get("total", 0) == x.get("valid", 0)) for x in rel_endpoint_validity.values()
        ),
        "expected_core_labels_present": all(node_counts.get(label, 0) > 0 for label in EXPECTED_LABELS),
        "expected_core_relations_present": all(rel_counts.get(rel, 0) > 0 for rel in EXPECTED_REL_TYPES),
        "dependency_graph_non_empty": dependency_rel_count > 0,
    }

    warnings = {
        "unexpected_labels": unexpected_labels,
        "unexpected_relation_types": unexpected_rel_types,
        "orphan_workitem_rate": round(orphan_rate, 3),
        "workitems_without_relates_to_commit": workitems_without_relates,
    }

    # Base quality policy:
    # - FAIL: structural integrity broken
    # - WARN: structural integrity okay but data completeness concerns
    # - PASS: structure okay and no warnings above tolerance
    structural_ok = all(checks.values())

    has_warning = bool(unexpected_labels or unexpected_rel_types or orphan_rate > 0.40)
    if not structural_ok:
        status = "FAIL"
    elif has_warning:
        status = "WARN"
    else:
        status = "PASS"

    report = {
        "status": status,
        "checks": checks,
        "node_counts": node_counts,
        "relation_counts": rel_counts,
        "duplicate_checks": duplicate_checks,
        "relation_endpoint_validity": rel_endpoint_validity,
        "evidence_stats": evidence_stats,
        "warnings": warnings,
    }

    out_path = repo_root / "neo4j_kb_health_report.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")

    print("=" * 80)
    print("NEO4J KNOWLEDGE BASE HEALTH CHECK")
    print("=" * 80)
    print(f"Status: {status}")
    print(f"No duplicate identifiers: {checks['no_duplicate_identifiers']}")
    print(f"Valid core relation endpoints: {checks['valid_core_relation_endpoints']}")
    print(f"Expected labels present: {checks['expected_core_labels_present']}")
    print(f"Expected relations present: {checks['expected_core_relations_present']}")
    print(f"Dependency graph non-empty: {checks['dependency_graph_non_empty']}")
    print(f"Orphan workitem rate: {warnings['orphan_workitem_rate']}")
    print(f"Unexpected labels: {', '.join(unexpected_labels) if unexpected_labels else 'none'}")
    print(f"Report file: {out_path}")


if __name__ == "__main__":
    run_health_check(Path(__file__).resolve().parent.parent)
