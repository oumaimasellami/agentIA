#!/usr/bin/env python3
"""
Script pour créer les relations COVERS entre WorkItems et Functions
"""

from neo4j import GraphDatabase
import json

class CoverRelationCreator:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
    
    def close(self):
        self.driver.close()
    
    def execute_query(self, query, params=None):
        with self.driver.session() as session:
            result = session.run(query, params or {})
            return [record.data() for record in result]
    
    def create_covers_relations_simple(self):
        """Crée les relations COVERS de manière simple"""
        print("\n" + "="*80)
        print("🔵 Création des relations COVERS (WorkItem → Function)...")
        print("="*80)
        
        # Stratégie: Chaque WorkItem couvre 1-3 Functions aléatoires
        query = """
        MATCH (wi:WorkItem)
        MATCH (f:Function)
        WITH wi, f, rand() as r
        ORDER BY wi.id, r
        WITH wi, collect(f)[0..3] as functions
        UNWIND functions as f
        MERGE (wi)-[rel:COVERS]->(f)
        RETURN count(rel) as total_relations
        """
        
        result = self.execute_query(query)
        if result:
            total = result[0].get('total_relations', 0)
            print(f"✅ {total} relations COVERS créées!")
            return total
        return 0
    
    def verify_covers_relations(self):
        """Vérifie les relations créées"""
        query = """
        MATCH (wi:WorkItem)-[rel:COVERS]->(f:Function)
        RETURN count(rel) as total_covers,
               count(DISTINCT wi) as workitems_with_covers,
               count(DISTINCT f) as functions_covered
        """
        
        result = self.execute_query(query)
        if result:
            data = result[0]
            print("\n" + "="*80)
            print("✅ VÉRIFICATION DES RELATIONS COVERS")
            print("="*80)
            print(f"Total relations COVERS: {data['total_covers']}")
            print(f"WorkItems avec relations: {data['workitems_with_covers']}")
            print(f"Functions couvertes: {data['functions_covered']}")
            print("="*80)

def main():
    # Connexion à Neo4j
    creator = CoverRelationCreator(
        "bolt://localhost:7687",
        "neo4j",
        "forvia2025"
    )
    
    try:
        # Créer les relations
        creator.create_covers_relations_simple()
        
        # Vérifier
        creator.verify_covers_relations()
        
    except Exception as e:
        print(f"❌ Erreur: {e}")
    finally:
        creator.close()

if __name__ == "__main__":
    main()
