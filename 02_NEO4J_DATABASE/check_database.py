#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script pour vérifier les nœuds dans Neo4j
"""

from neo4j import GraphDatabase

URI = "neo4j://localhost:7687"
AUTH = ("neo4j", "forvia2025")

def check_database():
    """Vérifier ce qui existe dans la base"""
    driver = GraphDatabase.driver(URI, auth=AUTH)
    
    with driver.session() as session:
        # Compter les nœuds
        count_query = """
        MATCH (n)
        RETURN labels(n) as label, count(n) as count
        """
        
        print("\n📊 NŒUDS DANS NEO4J:\n")
        print(f"{'LABEL':<30} | {'COUNT'}")
        print("-" * 50)
        
        results = session.run(count_query)
        for record in results:
            label = record["label"][0] if record["label"] else "Unknown"
            count = record["count"]
            print(f"{label:<30} | {count}")
        
        # Compter les relations
        print("\n\n🔗 RELATIONS DANS NEO4J:\n")
        print(f"{'TYPE':<30} | {'COUNT'}")
        print("-" * 50)
        
        rel_query = """
        MATCH ()-[r]->()
        RETURN type(r) as relationship_type, count(r) as count
        ORDER BY count DESC
        """
        
        results = session.run(rel_query)
        for record in results:
            rel_type = record["relationship_type"]
            count = record["count"]
            print(f"{rel_type:<30} | {count}")
    
    driver.close()

if __name__ == "__main__":
    print("\n" + "="*50)
    print("🔍 VÉRIFICATION DE LA BASE NEO4J")
    print("="*50)
    check_database()
