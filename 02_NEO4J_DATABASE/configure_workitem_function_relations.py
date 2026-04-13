#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Script pour configurer les relations entre WorkItems et Functions
Crée la relation directe: WorkItem --[COVERED_BY]--> Function
"""

import json
from neo4j import GraphDatabase

# Configuration Neo4j
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "forvia2025"

class WorkItemFunctionConfigurator:
    def __init__(self):
        self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        self.load_data()
    
    def load_data(self):
        """Charge les données du fichier graph_data.json"""
        print("📂 Chargement des données...")
        try:
            with open("graph_data.json", "r", encoding="utf-8") as f:
                self.data = json.load(f)
            print(f"✅ Données chargées")
            print(f"   - {len(self.data.get('microservices', []))} Microservices")
            print(f"   - {len(self.data.get('fonctions', []))} Functions")
            print(f"   - {len(self.data.get('work_items', []))} WorkItems")
        except Exception as e:
            print(f"❌ Erreur lors du chargement: {e}")
            self.data = {}
    
    def create_workitem_nodes(self):
        """Crée d'abord les nœuds WorkItem dans Neo4j"""
        print("\n" + "="*80)
        print("🟢 CRÉATION DES NŒUDS WorkItem")
        print("="*80)
        
        with self.driver.session() as session:
            # Charger les WorkItems depuis work_items.json
            try:
                with open("../work_items.json", "r", encoding="utf-8") as f:
                    work_items = json.load(f)
                
                print(f"📂 {len(work_items)} WorkItems à créer")
                
                # Créer les nœuds par batch
                batch_size = 100
                for i in range(0, len(work_items), batch_size):
                    batch = work_items[i:i+batch_size]
                    
                    query = """
                    UNWIND $items as item
                    CREATE (wi:WorkItem {
                        id: item.id,
                        titre: item.get('titre', ''),
                        type: item.get('type', ''),
                        statut: item.get('statut', ''),
                        description: item.get('description', ''),
                        area: item.get('area', ''),
                        iteration: item.get('iteration', ''),
                        created_at: datetime()
                    })
                    RETURN COUNT(*) as created
                    """
                    
                    result = session.run(query, items=batch)
                    record = result.single()
                    if record:
                        print(f"  ✅ Batch {i//batch_size + 1}: {record['created']} WorkItems créés")
                
                print(f"\n✅ Tous les WorkItems créés!")
                
            except Exception as e:
                print(f"⚠️  Erreur lors du chargement des WorkItems: {e}")
    
    def create_workitem_function_relations(self):
        """Crée les relations WorkItem --[COVERED_BY]--> Function"""
        print("\n" + "="*80)
        print("🔗 CRÉATION DES RELATIONS WorkItem --[COVERED_BY]--> Function")
        print("="*80)
        
        with self.driver.session() as session:
            # Créer les relations spécifiques pour WI 127835
            self.create_specific_workitem_relations(session)
    
    def create_specific_workitem_relations(self, session):
        """Crée les relations spécifiques pour WorkItem 127835"""
        print("\n" + "-"*80)
        print("🎯 RELATIONS SPÉCIFIQUES - WorkItem 127835")
        print("-"*80)
        
        # Functions qui couvrent le WI 127835
        wi_127835_functions = [
            "ApiOperation",
            "getWidgetPlacementRule",
            "validatePosition",
            "calculateGridCoordinates",
            "updateGridLayout",
            "UseFilters",
            "getAllMaterial",
            "getAllWorkcenters",
        ]
        
        for func_name in wi_127835_functions:
            query = """
            MATCH (wi:WorkItem {id: 127835})
            MATCH (f:Function)
            WHERE f.nom CONTAINS $func_name OR f.id CONTAINS $func_name
            MERGE (f)-[rel:COVERED_BY]->(wi)
            ON CREATE SET rel.created_at = datetime()
            RETURN f.nom as function_name, wi.id as workitem_id
            """
            
            result = session.run(query, func_name=func_name)
            records = result.fetchall()
            
            if records:
                for record in records:
                    print(f"  ✅ {record['function_name']} --[COVERED_BY]--> WI {record['workitem_id']}")
    
    def verify_relations(self):
        """Vérifie que les relations ont été créées"""
        print("\n" + "="*80)
        print("✅ VÉRIFICATION DES RELATIONS")
        print("="*80)
        
        with self.driver.session() as session:
            # Compter les relations COVERED_BY
            query = """
            MATCH (f:Function)-[r:COVERED_BY]->(wi:WorkItem)
            RETURN COUNT(r) as total_covered_by,
                   COUNT(DISTINCT f) as unique_functions,
                   COUNT(DISTINCT wi) as unique_workitems
            """
            
            result = session.run(query)
            record = result.single()
            
            print(f"\n📊 Statistiques:")
            print(f"   ✅ Relations COVERED_BY: {record['total_covered_by']}")
            print(f"   ✅ Functions uniques: {record['unique_functions']}")
            print(f"   ✅ WorkItems uniques: {record['unique_workitems']}")
            
            # Afficher les relations pour WI 127835
            query_wi = """
            MATCH (f:Function)-[r:COVERED_BY]->(wi:WorkItem {id: 127835})
            RETURN f.nom as function_name, wi.titre as workitem_title
            ORDER BY f.nom
            """
            
            result = session.run(query_wi)
            records = result.fetchall()
            
            print(f"\n🎯 WorkItem 127835 est couvert par {len(records)} Functions:")
            for record in records:
                print(f"   • {record['function_name']}")
    
    def show_neo4j_query(self):
        """Affiche la requête Neo4j pour visualiser les relations"""
        print("\n" + "="*80)
        print("📝 REQUÊTE NEO4J POUR VISUALISER")
        print("="*80)
        
        query = """
MATCH (wi:WorkItem {id: 127835})
OPTIONAL MATCH (f:Function)-[cov:COVERED_BY]->(wi)
OPTIONAL MATCH (ms:Microservice)-[impl:IMPLEMENTS]->(f)
OPTIONAL MATCH (ms)-[r:API_CALLS|MESSAGE_BROKER|INGESTION]->(other_ms:Microservice)
OPTIONAL MATCH (c:Commit)-[mod:MODIFIES]->(ms)
RETURN wi, f, cov, ms, impl, r, other_ms, c, mod
LIMIT 500
        """
        
        print("\n✅ Copier-collez cette requête dans Neo4j Browser:")
        print("-"*80)
        print(query)
        print("-"*80)
    
    def close(self):
        """Ferme la connexion"""
        self.driver.close()

def main():
    print("\n" + "="*80)
    print("⚙️  CONFIGURATION DES RELATIONS WorkItem <-> Function")
    print("="*80)
    
    try:
        configurator = WorkItemFunctionConfigurator()
        
        # 1. Créer les nœuds WorkItem
        configurator.create_workitem_nodes()
        
        # 2. Créer les relations
        configurator.create_workitem_function_relations()
        
        # Vérifier
        configurator.verify_relations()
        
        # Afficher la requête Neo4j
        configurator.show_neo4j_query()
        
        print("\n✅ Configuration terminée avec succès!")
        
        configurator.close()
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
