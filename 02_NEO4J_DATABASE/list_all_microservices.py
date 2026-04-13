#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Script pour afficher tous les microservices du graphe Neo4j
"""

from neo4j import GraphDatabase
import json

class MicroserviceLister:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def list_all_microservices(self):
        """Affiche tous les microservices avec leurs informations"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (ms:Microservice)
                RETURN ms.name as name, ms.type as type, ms.team as team
                ORDER BY ms.name
            """)
            
            microservices = []
            for record in result:
                microservices.append({
                    "name": record["name"],
                    "type": record["type"],
                    "team": record["team"]
                })
            
            return microservices

    def list_microservices_by_type(self):
        """Affiche les microservices groupés par type"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (ms:Microservice)
                RETURN ms.type as type, count(ms) as count, 
                       collect(ms.name) as microservices
                ORDER BY count DESC
            """)
            
            types_data = []
            for record in result:
                types_data.append({
                    "type": record["type"],
                    "count": record["count"],
                    "microservices": sorted(record["microservices"])
                })
            
            return types_data

    def get_microservice_details(self, name):
        """Affiche les détails d'un microservice"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (ms:Microservice {name: $name})
                OPTIONAL MATCH (ms)-[api:API_CALLS]->(target)
                OPTIONAL MATCH (ms)-[msg:MESSAGE_BROKER]->(target2)
                OPTIONAL MATCH (ms)-[ing:INGESTION]->(target3)
                OPTIONAL MATCH (ms)-[impl:IMPLEMENTS]->(func)
                RETURN ms, 
                       count(DISTINCT api) as api_calls_count,
                       count(DISTINCT msg) as message_broker_count,
                       count(DISTINCT ing) as ingestion_count,
                       count(DISTINCT impl) as implements_count
            """, name=name)
            
            records = list(result)
            if records:
                return records[0]
            return None

    def get_statistics(self):
        """Affiche les statistiques globales"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (ms:Microservice)
                OPTIONAL MATCH (c:Commit)
                OPTIONAL MATCH (f:Function)
                OPTIONAL MATCH (u:UseCase)
                RETURN count(DISTINCT ms) as microservices,
                       count(DISTINCT c) as commits,
                       count(DISTINCT f) as functions,
                       count(DISTINCT u) as usecases
            """)
            
            record = result.single()
            return {
                "microservices": record["microservices"],
                "commits": record["commits"],
                "functions": record["functions"],
                "usecases": record["usecases"]
            }

def main():
    # Configuration Neo4j
    uri = "bolt://localhost:7687"
    user = "neo4j"
    password = "forvia2025"
    
    lister = MicroserviceLister(uri, user, password)
    
    try:
        # Afficher statistiques
        print("\n" + "="*80)
        print("📊 STATISTIQUES GLOBALES")
        print("="*80)
        stats = lister.get_statistics()
        print(f"  Microservices: {stats['microservices']}")
        print(f"  Commits: {stats['commits']}")
        print(f"  Functions: {stats['functions']}")
        print(f"  Use Cases: {stats['usecases']}")
        
        # Afficher microservices par type
        print("\n" + "="*80)
        print("📂 MICROSERVICES PAR TYPE")
        print("="*80)
        types_data = lister.list_microservices_by_type()
        for type_info in types_data:
            print(f"\n  {type_info['type']} ({type_info['count']} MS)")
            print("  " + "-" * 60)
            for ms in type_info['microservices']:
                print(f"    • {ms}")
        
        # Afficher liste complète
        print("\n" + "="*80)
        print("📋 LISTE COMPLÈTE DES MICROSERVICES")
        print("="*80)
        microservices = lister.list_all_microservices()
        for i, ms in enumerate(microservices, 1):
            team_info = f" (Team: {ms['team']})" if ms['team'] else ""
            print(f"  {i:2d}. {ms['name']:<50} [{ms['type']}]{team_info}")
        
        print("\n" + "="*80)
        print(f"✅ TOTAL: {len(microservices)} microservices")
        print("="*80 + "\n")
        
    finally:
        lister.close()

if __name__ == "__main__":
    main()
