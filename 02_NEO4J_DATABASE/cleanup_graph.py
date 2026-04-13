#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Script pour nettoyer le graphe Neo4j:
- Supprimer la redondance UseCase
- Créer les relations directes WorkItem --[COVERED_BY]--> Function
- Supprimer les nœuds UseCase et relations COVERS
"""

from neo4j import GraphDatabase
import json

class GraphCleaner:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def cleanup_graph(self):
        """Nettoie les redondances dans le graphe"""
        
        print("\n" + "="*80)
        print("🧹 NETTOYAGE DU GRAPHE NEO4J")
        print("="*80)
        
        with self.driver.session() as session:
            # 1. Créer les relations directes WorkItem --[COVERED_BY]--> Function
            print("\n1️⃣ Création des relations directes WorkItem --[COVERED_BY]--> Function...")
            result = session.run("""
                MATCH (wi:WorkItem)<-[:COVERS]-(f:Function)
                MERGE (wi)-[covered:COVERED_BY]->(f)
                SET covered.created_at = datetime()
                RETURN count(*) as relations_created
            """)
            relations_created = result.single()[0]
            print(f"   ✅ {relations_created} relations COVERED_BY créées")
            
            # 2. Avant de supprimer, vérifier combien de UseCase nodes existent
            print("\n2️⃣ Vérification des nœuds UseCase...")
            result = session.run("MATCH (u:UseCase) RETURN count(u) as usecase_count")
            usecase_count = result.single()[0]
            print(f"   📊 {usecase_count} nœuds UseCase trouvés")
            
            # 3. Supprimer les relations COVERS
            print("\n3️⃣ Suppression des relations COVERS (redondantes)...")
            result = session.run("""
                MATCH (f:Function)-[cov:COVERS]->(u:UseCase)
                DELETE cov
                RETURN count(*) as relations_deleted
            """)
            relations_deleted = result.single()[0]
            print(f"   ✅ {relations_deleted} relations COVERS supprimées")
            
            # 4. Supprimer les nœuds UseCase orphelins
            print("\n4️⃣ Suppression des nœuds UseCase orphelins...")
            result = session.run("""
                MATCH (u:UseCase)
                WHERE NOT (u)<-[:COVERS]-()
                DELETE u
                RETURN count(*) as nodes_deleted
            """)
            nodes_deleted = result.single()[0]
            print(f"   ✅ {nodes_deleted} nœuds UseCase supprimés")
            
            # 5. Vérifier les relations COVERED_BY créées
            print("\n5️⃣ Vérification des relations COVERED_BY créées...")
            result = session.run("""
                MATCH (wi:WorkItem)-[covered:COVERED_BY]->(f:Function)
                RETURN count(*) as covered_relations
            """)
            covered_relations = result.single()[0]
            print(f"   ✅ {covered_relations} relations COVERED_BY actives")
            
            # 6. Résumé final
            print("\n" + "="*80)
            print("📊 RÉSUMÉ DU NETTOYAGE")
            print("="*80)
            
            result = session.run("""
                MATCH (n)
                RETURN labels(n)[0] as NodeType, count(*) as Count
                ORDER BY Count DESC
            """)
            
            for record in result:
                node_type = record["NodeType"] or "Unknown"
                count = record["Count"]
                print(f"   {node_type:20} → {count:6} nœuds")
            
            # 7. Vérifier les relations
            print("\n📊 RELATIONS ACTIVES:")
            result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) as RelationType, count(*) as Count
                ORDER BY Count DESC
            """)
            
            for record in result:
                rel_type = record["RelationType"]
                count = record["Count"]
                print(f"   {rel_type:20} → {count:6} relations")
            
            print("\n" + "="*80)
            print("✅ NETTOYAGE TERMINÉ!")
            print("="*80)
            print("\n📝 Résumé des changements:")
            print(f"   • Relations COVERED_BY créées: {relations_created}")
            print(f"   • Relations COVERS supprimées: {relations_deleted}")
            print(f"   • Nœuds UseCase supprimés: {nodes_deleted}")
            print(f"\n✅ Le graphe est maintenant OPTIMISÉ et sans redondance!\n")

def main():
    # Configuration Neo4j
    uri = "neo4j://localhost:7687"
    user = "neo4j"
    password = "forvia2025"
    
    print("\n🔄 Connexion à Neo4j...")
    cleaner = GraphCleaner(uri, user, password)
    
    try:
        cleaner.cleanup_graph()
    finally:
        cleaner.close()

if __name__ == "__main__":
    main()
