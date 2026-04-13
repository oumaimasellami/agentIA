#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script pour afficher les relations IMPLEMENTS
Microservice IMPLEMENTS Function
"""

from neo4j import GraphDatabase
import json

URI = "neo4j://localhost:7687"
AUTH = ("neo4j", "forvia2025")

def view_implements_relations():
    """Affiche toutes les relations IMPLEMENTS"""
    driver = GraphDatabase.driver(URI, auth=AUTH)
    
    with driver.session() as session:
        # Compter les relations
        count_query = """
        MATCH (ms:Microservice)-[r:IMPLEMENTS]->(f:Function)
        RETURN count(r) as total
        """
        result = session.run(count_query)
        total = result.single()["total"]
        print(f"\n✅ TOTAL RELATIONS IMPLEMENTS: {total}\n")
        
        # Afficher les relations avec détails
        query = """
        MATCH (ms:Microservice)-[r:IMPLEMENTS]->(f:Function)
        RETURN ms.name as microservice, f.nom as function, f.type as type
        ORDER BY ms.name, f.nom
        LIMIT 100
        """
        
        print("=" * 100)
        print(f"{'MICROSERVICE':<40} | {'FUNCTION':<40} | {'TYPE'}")
        print("=" * 100)
        
        results = session.run(query)
        for record in results:
            ms = record["microservice"] or "N/A"
            func = record["function"] or "N/A"
            func_type = record["type"] or "unknown"
            print(f"{ms:<40} | {func:<40} | {func_type}")
        
        print("=" * 100)
        print(f"\n📊 Affichage des 100 premières relations sur {total} total\n")
        
        # Statistiques par microservice
        print("\n📈 TOP 10 MICROSERVICES AVEC PLUS DE FUNCTIONS:\n")
        stats_query = """
        MATCH (ms:Microservice)-[r:IMPLEMENTS]->(f:Function)
        RETURN ms.name as microservice, count(f) as function_count
        ORDER BY function_count DESC
        LIMIT 10
        """
        
        print(f"{'MICROSERVICE':<40} | {'NB FUNCTIONS'}")
        print("-" * 55)
        
        results = session.run(stats_query)
        for record in results:
            ms = record["microservice"]
            count = record["function_count"]
            print(f"{ms:<40} | {count}")
    
    driver.close()

if __name__ == "__main__":
    print("\n" + "="*100)
    print("🔗 RELATIONS IMPLEMENTS (Microservice → Function)")
    print("="*100)
    view_implements_relations()
