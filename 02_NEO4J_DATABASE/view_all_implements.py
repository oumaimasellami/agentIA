#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from neo4j import GraphDatabase
import json
from collections import defaultdict

class MicroserviceViewer:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def get_all_implements_relations(self):
        """Récupère TOUTES les relations IMPLEMENTS (Microservice -> Function)"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (ms:Microservice)-[r:IMPLEMENTS]->(f:Function)
                RETURN ms.name as microservice, f.nom as function, f.type as function_type
                ORDER BY ms.name, f.nom
            """)
            return result.data()

    def get_all_relations_summary(self):
        """Résumé des relations IMPLEMENTS par Microservice"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (ms:Microservice)-[r:IMPLEMENTS]->(f:Function)
                RETURN ms.name as microservice, count(f) as fonction_count
                ORDER BY fonction_count DESC
            """)
            return result.data()

    def get_all_types_relations(self):
        """Voir TOUS les types de relations"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH ()-[r]->()
                RETURN DISTINCT type(r) as relation_type, count(r) as total
                ORDER BY total DESC
            """)
            return result.data()

    def display_all_data(self):
        """Affiche tout les microservices avec leurs fonctions"""
        
        print("\n" + "="*100)
        print("📊 TOUTES LES RELATIONS IMPLEMENTS (Microservice → Function)")
        print("="*100)
        
        implements = self.get_all_implements_relations()
        
        if not implements:
            print("❌ Aucune relation IMPLEMENTS trouvée!")
            return
        
        # Groupe par microservice
        by_ms = defaultdict(list)
        for rel in implements:
            by_ms[rel['microservice']].append(rel)
        
        print(f"\n✅ {len(by_ms)} Microservices trouvés\n")
        
        for ms_name in sorted(by_ms.keys()):
            functions = by_ms[ms_name]
            print(f"\n{'─'*100}")
            print(f"🔷 {ms_name}")
            print(f"{'─'*100}")
            print(f"   IMPLEMENTS: {len(functions)} fonctions")
            print()
            
            for i, func in enumerate(functions, 1):
                func_type = func.get('function_type', 'N/A')
                print(f"   {i:3d}. {func['function']} ({func_type})")
        
        # Résumé
        print(f"\n{'='*100}")
        print("📈 RÉSUMÉ PAR MICROSERVICE")
        print("="*100)
        
        summary = self.get_all_relations_summary()
        total_implements = 0
        
        for s in summary:
            ms = s['microservice']
            count = s['fonction_count']
            total_implements += count
            print(f"   {ms:50s} → {count:4d} fonctions")
        
        print(f"\n   TOTAL: {total_implements} relations IMPLEMENTS\n")
        
        # Affiche tous les types de relations existant dans le graphe
        print(f"{'='*100}")
        print("🔗 TOUS LES TYPES DE RELATIONS DANS LE GRAPHE")
        print("="*100 + "\n")
        
        all_relations = self.get_all_types_relations()
        
        for rel in all_relations:
            rel_type = rel['relation_type']
            total = rel['total']
            print(f"   {rel_type:20s} → {total:7d} relations")
        
        print()

def main():
    # Configuration Neo4j
    uri = "bolt://localhost:7687"
    user = "neo4j"
    password = "forvia2025"
    
    viewer = MicroserviceViewer(uri, user, password)
    
    try:
        viewer.display_all_data()
    finally:
        viewer.close()

if __name__ == "__main__":
    main()
