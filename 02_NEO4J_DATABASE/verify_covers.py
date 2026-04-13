#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from neo4j import GraphDatabase

# Connexion à Neo4j
driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "forvia2025"))

def verify_covers():
    with driver.session() as session:
        # Vérifier les UseCases
        use_cases = session.run("MATCH (uc:UseCase) RETURN count(uc) as count").single()
        print(f"✅ UseCases dans Neo4j: {use_cases['count']}")
        
        # Vérifier les Relations COVERS
        covers = session.run("MATCH ()-[r:COVERS]->() RETURN count(r) as count").single()
        print(f"✅ Relations COVERS dans Neo4j: {covers['count']}")
        
        # Afficher quelques exemples
        if covers['count'] > 0:
            print("\n📋 Exemples de relations COVERS:")
            results = session.run("""
                MATCH (f:Function)-[r:COVERS]->(uc:UseCase)
                RETURN f.nom as function, uc.nom as usecase
                LIMIT 10
            """)
            for record in results:
                print(f"   {record['function']} → {record['usecase']}")
        else:
            print("❌ Aucune relation COVERS trouvée!")
            
            # Vérifier les Functions
            functions = session.run("MATCH (f:Function) RETURN count(f) as count").single()
            print(f"\nFunctions dans Neo4j: {functions['count']}")
            
            # Vérifier les nœuds généraux
            all_rels = session.run("MATCH ()-[r]->() RETURN DISTINCT type(r), count(r) as count ORDER BY count DESC").data()
            print("\n📊 Tous les types de relations:")
            for rel in all_rels:
                print(f"   {rel['type(r)']} → {rel['count']}")

driver.close()
