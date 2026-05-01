#!/usr/bin/env python3
import os
from datetime import datetime

from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "forvia2025")


def run_query(session, query, params=None):
    record = session.run(query, params or {}).single()
    return dict(record) if record else {}


def main():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    created_at = datetime.now().isoformat()

    q_touches = """
    MATCH (c:Commit)-[:MODIFIES]->(:Microservice)-[:IMPLEMENTS]->(f:Function)
    MERGE (c)-[r:TOUCHES_FUNCTION]->(f)
    ON CREATE SET r.created_at = $created_at,
                  r.source = 'inferred_from_modifies_and_implements'
    RETURN count(r) as count
    """

    q_relates = """
    MATCH (wi:WorkItem)<-[:COVERS]-(f:Function)<-[:TOUCHES_FUNCTION]-(c:Commit)
    MERGE (wi)-[r:RELATES_TO_COMMIT]->(c)
    ON CREATE SET r.created_at = $created_at,
                  r.source = 'inferred_from_function_overlap'
    RETURN count(r) as count
    """

    q_stats = """
    MATCH ()-[r:TOUCHES_FUNCTION]->()
    WITH count(r) as touches_function_count
    MATCH ()-[r2:RELATES_TO_COMMIT]->()
    RETURN touches_function_count, count(r2) as relates_to_commit_count
    """

    try:
        with driver.session() as session:
            touches = run_query(session, q_touches, {"created_at": created_at}).get("count", 0)
            relates = run_query(session, q_relates, {"created_at": created_at}).get("count", 0)
            stats = run_query(session, q_stats)

        print("=" * 80)
        print("ENRICH COMMIT/FUNCTION/WORKITEM RELATIONS")
        print("=" * 80)
        print(f"TOUCHES_FUNCTION merged: {touches}")
        print(f"RELATES_TO_COMMIT merged: {relates}")
        print(f"Total TOUCHES_FUNCTION: {stats.get('touches_function_count', 0)}")
        print(f"Total RELATES_TO_COMMIT: {stats.get('relates_to_commit_count', 0)}")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
