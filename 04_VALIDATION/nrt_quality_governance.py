import json
import importlib.util
from pathlib import Path

from neo4j import GraphDatabase

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "forvia2025"

THRESHOLDS = {
    "min_consistency_rate": 0.99,
    "max_orphan_workitem_rate": 0.55,
    "max_zero_direct_rate": 0.40,
    "max_zero_functions_rate": 0.40,
    "min_confirmed_screen_source_rate": 0.95,
    "max_p95_total_ms": 25,
    "max_p95_total_functions": 80,
}


def load_server_module(repo_root: Path):
    server_path = repo_root / "05_WEB_INTERFACE" / "server.py"
    spec = importlib.util.spec_from_file_location("nrt_server", str(server_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def percentile(values, p):
    if not values:
        return 0
    sorted_values = sorted(values)
    idx = int(round((p / 100.0) * (len(sorted_values) - 1)))
    return sorted_values[idx]


def neo4j_baseline_counts(session, wi_id):
    query = """
    MATCH (wi:WorkItem {id: $wi_id})

    CALL {
      WITH wi
      OPTIONAL MATCH (wi)<-[:COVERS]-(f:Function)<-[:IMPLEMENTS]-(ms:Microservice)
            RETURN collect(DISTINCT ms.name) AS coversDirectMs, collect(DISTINCT f.id) AS coversDirectFnIds
    }

        CALL {
            WITH wi
            OPTIONAL MATCH (wi)-[:RELATES_TO_COMMIT]->(:Commit)-[:TOUCHES_FUNCTION]->(f:Function)
            OPTIONAL MATCH (ms:Microservice)-[:IMPLEMENTS]->(f)
            RETURN collect(DISTINCT ms.name) AS commitDirectMs, collect(DISTINCT f.id) AS commitDirectFnIds
        }

        WITH [x IN (coversDirectMs + commitDirectMs) WHERE x IS NOT NULL] AS directMsRaw,
                 [x IN (coversDirectFnIds + commitDirectFnIds) WHERE x IS NOT NULL] AS directFnRaw
        WITH reduce(acc=[], x IN directMsRaw | CASE WHEN x IN acc THEN acc ELSE acc + x END) AS directMs,
                 reduce(acc=[], x IN directFnRaw | CASE WHEN x IN acc THEN acc ELSE acc + x END) AS directFnIds

    CALL {
      WITH directMs
      WITH directMs WHERE size(directMs) > 0
      UNWIND directMs AS dms
      OPTIONAL MATCH (:Microservice {name:dms})-[:API_CALLS|MESSAGE_BROKER|INGESTION]->(ms2:Microservice)
      RETURN collect(DISTINCT ms2.name) AS indirectMs
      UNION
      WITH directMs
      WITH directMs WHERE size(directMs) = 0
      RETURN [] AS indirectMs
    }

    CALL {
      WITH indirectMs
      WITH indirectMs WHERE size(indirectMs) > 0
      UNWIND indirectMs AS ims
      OPTIONAL MATCH (m2:Microservice {name:ims})-[:IMPLEMENTS]->(f2:Function)
      RETURN collect(DISTINCT f2.id) AS indirectFnIds
      UNION
      WITH indirectMs
      WITH indirectMs WHERE size(indirectMs) = 0
      RETURN [] AS indirectFnIds
    }

    WITH directMs, indirectMs, [x IN directFnIds + indirectFnIds WHERE x IS NOT NULL] AS allIds
    RETURN
      size(directMs) AS direct_ms_count,
      size(indirectMs) AS indirect_ms_count,
      size(directMs) + size(indirectMs) AS total_ms_count,
      size(reduce(acc=[], x IN allIds | CASE WHEN x IN acc THEN acc ELSE acc + x END)) AS total_functions
    """
    record = session.run(query, wi_id=wi_id).single()
    if not record:
        return None
    return {
        "direct_ms_count": record["direct_ms_count"],
        "indirect_ms_count": record["indirect_ms_count"],
        "total_ms_count": record["total_ms_count"],
        "total_functions": record["total_functions"],
    }


def run_quality_audit(repo_root: Path):
    module = load_server_module(repo_root)
    analyzer = module.analyzer

    workitems_path = repo_root / "work_items.json"
    items = json.loads(workitems_path.read_text(encoding="utf-8"))
    user_stories = [wi for wi in items if wi.get("type") == "User Story"]

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    errors = []
    mismatches = []
    zero_direct = []
    zero_functions = []
    non_confirmed_screen_source = []
    total_ms_values = []
    total_function_values = []

    try:
        with driver.session() as session:
            for wi in user_stories:
                wi_id = wi.get("id")
                if wi_id is None:
                    continue

                scope = analyzer.get_nrt_scope(wi_id)
                if "error" in scope:
                    errors.append({"workitem_id": wi_id, "title": wi.get("titre", ""), "error": scope["error"]})
                    continue

                summary = scope.get("summary", {})
                tester_scope = scope.get("tester_scope", {})
                total_ms = int(summary.get("total_ms_count", 0) or 0)
                total_functions = int(summary.get("total_functions", 0) or 0)
                total_ms_values.append(total_ms)
                total_function_values.append(total_functions)

                if int(summary.get("direct_ms_count", 0) or 0) == 0:
                    zero_direct.append(wi_id)
                if total_functions == 0:
                    zero_functions.append(wi_id)

                if tester_scope.get("screen_source") != "confirmed_ui_routes_only":
                    non_confirmed_screen_source.append(wi_id)

                baseline = neo4j_baseline_counts(session, wi_id)
                if baseline is None:
                    mismatches.append({
                        "workitem_id": wi_id,
                        "title": wi.get("titre", ""),
                        "reason": "no_baseline",
                    })
                else:
                    keys = ["direct_ms_count", "indirect_ms_count", "total_ms_count", "total_functions"]
                    diff = {}
                    for k in keys:
                        api_value = summary.get(k)
                        if api_value != baseline.get(k):
                            diff[k] = {"api": api_value, "neo4j": baseline.get(k)}
                    if diff:
                        mismatches.append({
                            "workitem_id": wi_id,
                            "title": wi.get("titre", ""),
                            "reason": "count_mismatch",
                            "diff": diff,
                        })

            total_workitems = session.run("MATCH (wi:WorkItem) RETURN count(wi) AS c").single()["c"]
            orphan_workitems = session.run("MATCH (wi:WorkItem) WHERE NOT (wi)<-[:COVERS]-() RETURN count(wi) AS c").single()["c"]

    finally:
        driver.close()

    total_us = len(user_stories)
    consistency_rate = (total_us - len(mismatches) - len(errors)) / total_us if total_us else 0
    orphan_workitem_rate = orphan_workitems / total_workitems if total_workitems else 0
    zero_direct_rate = len(zero_direct) / total_us if total_us else 0
    zero_functions_rate = len(zero_functions) / total_us if total_us else 0
    confirmed_screen_source_rate = (total_us - len(non_confirmed_screen_source) - len(errors)) / total_us if total_us else 0

    metrics = {
        "consistency_rate": consistency_rate,
        "orphan_workitem_rate": orphan_workitem_rate,
        "zero_direct_rate": zero_direct_rate,
        "zero_functions_rate": zero_functions_rate,
        "confirmed_screen_source_rate": confirmed_screen_source_rate,
        "p95_total_ms": percentile(total_ms_values, 95),
        "p95_total_functions": percentile(total_function_values, 95),
        "total_user_stories": total_us,
        "total_workitems": total_workitems,
        "orphan_workitems": orphan_workitems,
        "errors": len(errors),
        "mismatches": len(mismatches),
    }

    gates = {
        "consistency_gate": metrics["consistency_rate"] >= THRESHOLDS["min_consistency_rate"],
        "orphan_workitem_gate": metrics["orphan_workitem_rate"] <= THRESHOLDS["max_orphan_workitem_rate"],
        "zero_direct_gate": metrics["zero_direct_rate"] <= THRESHOLDS["max_zero_direct_rate"],
        "zero_functions_gate": metrics["zero_functions_rate"] <= THRESHOLDS["max_zero_functions_rate"],
        "screen_source_gate": metrics["confirmed_screen_source_rate"] >= THRESHOLDS["min_confirmed_screen_source_rate"],
        "scope_size_ms_gate": metrics["p95_total_ms"] <= THRESHOLDS["max_p95_total_ms"],
        "scope_size_functions_gate": metrics["p95_total_functions"] <= THRESHOLDS["max_p95_total_functions"],
    }

    overall_pass = all(gates.values())

    report = {
        "overall_status": "PASS" if overall_pass else "FAIL",
        "thresholds": THRESHOLDS,
        "metrics": metrics,
        "gates": gates,
        "samples": {
            "errors_top20": errors[:20],
            "mismatches_top20": mismatches[:20],
            "zero_direct_top50": zero_direct[:50],
            "zero_functions_top50": zero_functions[:50],
        },
    }

    out = repo_root / "nrt_quality_governance_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")

    print("=" * 80)
    print("NRT QUALITY GOVERNANCE REPORT")
    print("=" * 80)
    print(f"Overall status: {report['overall_status']}")
    print(f"Consistency rate: {metrics['consistency_rate']:.3f}")
    print(f"Orphan workitem rate: {metrics['orphan_workitem_rate']:.3f}")
    print(f"Zero-direct user stories: {metrics['zero_direct_rate']:.3f}")
    print(f"Zero-functions user stories: {metrics['zero_functions_rate']:.3f}")
    print(f"Confirmed screen source rate: {metrics['confirmed_screen_source_rate']:.3f}")
    print(f"P95 total_ms: {metrics['p95_total_ms']}")
    print(f"P95 total_functions: {metrics['p95_total_functions']}")
    print(f"Report file: {out}")


if __name__ == "__main__":
    run_quality_audit(Path(__file__).resolve().parent.parent)
