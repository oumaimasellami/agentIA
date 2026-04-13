#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Visualiser le graphe complet avec tous les microservices et relations
"""

from neo4j import GraphDatabase
import json

class GraphVisualizer:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        
    def close(self):
        self.driver.close()
    
    def get_complete_graph(self):
        """Récupère le graphe complet avec tous les microservices et relations"""
        with self.driver.session() as session:
            query = """
            MATCH (m:Microservice)
            OPTIONAL MATCH (m)-[api:API_CALLS]->(target:Microservice)
            OPTIONAL MATCH (m)-[impl:IMPLEMENTS]->(func:Function)
            OPTIONAL MATCH (func)-[cov:COVERS]->(uc:UseCase)
            OPTIONAL MATCH (c:Commit)-[mod:MODIFIES]->(m)
            RETURN m, api, target, impl, func, cov, uc, mod, c
            """
            
            result = session.run(query)
            
            # Collecte les données
            microservices = {}
            relations = {
                'API_CALLS': [],
                'IMPLEMENTS': [],
                'COVERS': [],
                'MODIFIES': []
            }
            
            for record in result:
                m = record['m']
                target = record['target']
                func = record['func']
                uc = record['uc']
                c = record['c']
                api = record['api']
                impl = record['impl']
                cov = record['cov']
                mod = record['mod']
                
                # Ajouter le Microservice
                if m and m['name'] not in microservices:
                    microservices[m['name']] = {
                        'type': 'Microservice',
                        'properties': dict(m)
                    }
                
                # Ajouter les relations API_CALLS
                if api and target:
                    relations['API_CALLS'].append({
                        'from': m['name'],
                        'to': target['name'],
                        'type': 'API_CALLS',
                        'properties': dict(api) if api else {}
                    })
                
                # Ajouter les relations IMPLEMENTS
                if impl and func:
                    relations['IMPLEMENTS'].append({
                        'from': m['name'],
                        'to': func.get('nom', 'Unknown'),
                        'type': 'IMPLEMENTS',
                        'function_type': func.get('type', 'N/A'),
                        'properties': dict(impl) if impl else {}
                    })
                
                # Ajouter les relations COVERS
                if cov and uc:
                    relations['COVERS'].append({
                        'from': func.get('nom', 'Unknown'),
                        'to': uc.get('nom', 'Unknown'),
                        'type': 'COVERS',
                        'properties': dict(cov) if cov else {}
                    })
                
                # Ajouter les relations MODIFIES
                if mod and c:
                    relations['MODIFIES'].append({
                        'commit_id': c.get('commit_id', 'Unknown'),
                        'commit_auteur': c.get('auteur', 'Unknown'),
                        'to_microservice': m['name'],
                        'type': 'MODIFIES',
                        'properties': dict(mod) if mod else {}
                    })
            
            return microservices, relations
    
    def print_summary(self):
        """Affiche un résumé du graphe"""
        microservices, relations = self.get_complete_graph()
        
        print("\n" + "="*80)
        print("📊 GRAPHE COMPLET DU SYSTÈME AVEC 81 MICROSERVICES")
        print("="*80)
        
        print(f"\n✅ MICROSERVICES: {len(microservices)}")
        print("─" * 80)
        for i, ms_name in enumerate(sorted(microservices.keys()), 1):
            print(f"  {i:2d}. {ms_name}")
        
        print(f"\n✅ RELATIONS TOTALES: {sum(len(v) for v in relations.values())}")
        print("─" * 80)
        print(f"  📡 API_CALLS:     {len(relations['API_CALLS']):4d} relations")
        print(f"  ⚙️  IMPLEMENTS:    {len(relations['IMPLEMENTS']):4d} relations")
        print(f"  🎯 COVERS:        {len(relations['COVERS']):4d} relations")
        print(f"  📝 MODIFIES:      {len(relations['MODIFIES']):4d} relations")
        
        print(f"\n📡 DÉTAILS DES RELATIONS API_CALLS:")
        print("─" * 80)
        for rel in relations['API_CALLS'][:20]:
            print(f"  {rel['from']} ──[API_CALLS]──> {rel['to']}")
        if len(relations['API_CALLS']) > 20:
            print(f"  ... et {len(relations['API_CALLS']) - 20} autres")
        
        print(f"\n⚙️  DÉTAILS DES RELATIONS IMPLEMENTS (top 20):")
        print("─" * 80)
        for rel in relations['IMPLEMENTS'][:20]:
            print(f"  {rel['from']:35s} ──[IMPLEMENTS]──> {rel['to']:30s} ({rel['function_type']})")
        if len(relations['IMPLEMENTS']) > 20:
            print(f"  ... et {len(relations['IMPLEMENTS']) - 20} autres")
        
        print(f"\n🎯 DÉTAILS DES RELATIONS COVERS (top 20):")
        print("─" * 80)
        for rel in relations['COVERS'][:20]:
            print(f"  {rel['from']:40s} ──[COVERS]──> {rel['to']}")
        if len(relations['COVERS']) > 20:
            print(f"  ... et {len(relations['COVERS']) - 20} autres")
        
        print(f"\n📝 DÉTAILS DES RELATIONS MODIFIES (top 20):")
        print("─" * 80)
        for rel in relations['MODIFIES'][:20]:
            print(f"  Commit {rel['commit_id'][:8]}... by {rel['commit_auteur']:20s} ──[MODIFIES]──> {rel['to_microservice']}")
        if len(relations['MODIFIES']) > 20:
            print(f"  ... et {len(relations['MODIFIES']) - 20} autres")
        
        print("\n" + "="*80)
        print("🚀 GRAPHE COMPLET PRÊT!")
        print("="*80)
        print("\n💡 Ouvrez Neo4j Browser et exécutez cette requête:")
        print("""
MATCH (m:Microservice)
OPTIONAL MATCH (m)-[api:API_CALLS]->(target:Microservice)
OPTIONAL MATCH (m)-[impl:IMPLEMENTS]->(func:Function)
OPTIONAL MATCH (func)-[cov:COVERS]->(uc:UseCase)
OPTIONAL MATCH (c:Commit)-[mod:MODIFIES]->(m)
RETURN m, api, target, impl, func, cov, uc, mod, c
        """)
        print("\n")

def main():
    visualizer = GraphVisualizer(
        uri="neo4j://localhost:7687",
        user="neo4j",
        password="forvia2025"
    )
    
    try:
        visualizer.print_summary()
    finally:
        visualizer.close()

if __name__ == "__main__":
    main()
