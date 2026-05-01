import json
from neo4j import GraphDatabase

from pathlib import Path
import importlib.util


NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "forvia2025"


def load_server_module():
    server_path = Path(__file__).parent / "05_WEB_INTERFACE" / "server.py"
    spec = importlib.util.spec_from_file_location("nrt_server", str(server_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def main():
    module = load_server_module()
    analyzer = module.analyzer

    workitems_path = Path(__file__).parent / "work_items.json"
    with open(workitems_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    user_stories = [wi for wi in items if wi.get("type") == "User Story"]

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    mismatches = []

    try:
        with driver.session() as session:
            for wi in user_stories:
                wi_id = wi.get("id")
                if wi_id is None:
                    continue

                api_scope = analyzer.get_nrt_scope(wi_id)
                if "error" in api_scope:
                    mismatches.append({
                        "workitem_id": wi_id,
                        "title": wi.get("titre", ""),
                        "error": api_scope["error"],
                    })
                    continue

                api_summary = api_scope.get("summary", {})
                baseline = neo4j_baseline_counts(session, wi_id)

                if not baseline:
                    mismatches.append({
                        "workitem_id": wi_id,
                        "title": wi.get("titre", ""),
                        "error": "No baseline record",
                    })
                    continue

                keys = ["direct_ms_count", "indirect_ms_count", "total_ms_count", "total_functions"]
                diff = {}
                for key in keys:
                    if api_summary.get(key) != baseline.get(key):
                        diff[key] = {
                            "api": api_summary.get(key),
                            "neo4j": baseline.get(key),
                        }

                if diff:
                    mismatches.append({
                        "workitem_id": wi_id,
                        "title": wi.get("titre", ""),
                        "diff": diff,
                    })
    finally:
        driver.close()

    total = len(user_stories)
    mismatch_count = len(mismatches)
    match_count = total - mismatch_count

    print("=" * 80)
    print("NRT USER STORY CONSISTENCY REPORT")
    print("=" * 80)
    print(f"Total User Stories checked: {total}")
    print(f"Matches: {match_count}")
    print(f"Mismatches: {mismatch_count}")

    if mismatches:
        print("\nTop mismatches (up to 20):")
        for item in mismatches[:20]:
            print(f"- WI {item['workitem_id']}: {item.get('title', '')}")
            if "error" in item:
                print(f"    error: {item['error']}")
            else:
                print(f"    diff: {json.dumps(item['diff'], ensure_ascii=True)}")

    output_path = Path(__file__).parent / "nrt_userstory_consistency_report.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "total_user_stories": total,
                "matches": match_count,
                "mismatches": mismatch_count,
                "details": mismatches,
            },
            f,
            indent=2,
            ensure_ascii=True,
        )

    print(f"\nDetailed report written to: {output_path}")


if __name__ == "__main__":
    main()
