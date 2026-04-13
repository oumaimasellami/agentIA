#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de vérification des relations dans Neo4j
Vérifie si les relations MESSAGE_BROKER et INGESTION ont été créées
"""

from neo4j import GraphDatabase
import json

# Configuration Neo4j
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "forvia2025"

def verify_relations():
    """Vérifie les relations dans la base de données"""
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    try:
        with driver.session() as session:
            print("\n" + "="*80)
            print("🔍 VÉRIFICATION DES RELATIONS DANS NEO4J")
            print("="*80)
            
            # 1. Toutes les relations
            print("\n1️⃣  TOUS LES TYPES DE RELATIONS:")
            result = session.run("""
                MATCH ()-[r]->() 
                RETURN DISTINCT type(r) as relation_type, count(r) as count
                ORDER BY count DESC
            """)
            
            for record in result:
                print(f"   • {record['relation_type']:30} : {record['count']:5} relations")
            
            # 2. Relations MESSAGE_BROKER spécifiquement
            print("\n2️⃣  RELATIONS MESSAGE_BROKER:")
            result = session.run("""
                MATCH (ms:Microservice)-[r:MESSAGE_BROKER]->(target:Microservice)
                RETURN ms.name, r.broker_type, r.topics, target.name
            """)
            
            count = 0
            for record in result:
                count += 1
                print(f"   • {record['ms.name']:40} → {record['target.name']:40}")
                print(f"     ├─ Type: {record['r.broker_type']}")
                print(f"     └─ Topics: {record['r.topics']}")
            
            if count == 0:
                print("   ❌ Aucune relation MESSAGE_BROKER trouvée!")
            else:
                print(f"   ✅ {count} relations MESSAGE_BROKER trouvées")
            
            # 3. Relations INGESTION spécifiquement
            print("\n3️⃣  RELATIONS INGESTION:")
            result = session.run("""
                MATCH (ms:Microservice)-[r:INGESTION]->(target:Microservice)
                RETURN ms.name, r.ingestion_type, r.frequency, target.name
            """)
            
            count = 0
            for record in result:
                count += 1
                print(f"   • {record['ms.name']:40} → {record['target.name']:40}")
                print(f"     ├─ Type: {record['r.ingestion_type']}")
                print(f"     └─ Frequency: {record['r.frequency']}")
            
            if count == 0:
                print("   ❌ Aucune relation INGESTION trouvée!")
            else:
                print(f"   ✅ {count} relations INGESTION trouvées")
            
            # 4. Vérifier les microservices
            print("\n4️⃣  MICROSERVICES EXISTANTS:")
            result = session.run("""
                MATCH (ms:Microservice)
                RETURN COUNT(ms) as count
            """)
            
            count = result.single()['count']
            print(f"   • Total: {count} microservices")
            
            # 5. Charger le fichier JSON pour comparer
            print("\n5️⃣  VÉRIFICATION DU FICHIER JSON:")
            try:
                with open("../01_EXTRACTION/broker_ingestion_relations.json", "r", encoding="utf-8") as f:
                    broker_data = json.load(f)
                
                print(f"   • Fichier contient: {len(broker_data)} relations")
                
                message_broker = [r for r in broker_data if r["type"] == "MESSAGE_BROKER"]
                ingestion = [r for r in broker_data if r["type"] in ["INGESTION", "DATABASE_INGESTION"]]
                
                print(f"   • MESSAGE_BROKER: {len(message_broker)} relations")
                print(f"   • INGESTION: {len(ingestion)} relations")
                
                # Vérifier si les noms existent dans Neo4j
                print("\n6️⃣  VÉRIFICATION DES NOMS DE MICROSERVICES:")
                result = session.run("MATCH (ms:Microservice) RETURN ms.name ORDER BY ms.name")
                existing_ms = {record['ms.name'] for record in result}
                
                # Vérifier les relations MESSAGE_BROKER
                print("   MESSAGE_BROKER - Vérification des noms:")
                for rel in message_broker:
                    source_exists = rel['source'] in existing_ms
                    target_exists = rel['cible'] in existing_ms
                    status = "✅" if (source_exists and target_exists) else "❌"
                    print(f"   {status} {rel['source']:40} → {rel['cible']:40}")
                    if not source_exists:
                        print(f"      ⚠️  Source '{rel['source']}' n'existe pas")
                    if not target_exists:
                        print(f"      ⚠️  Target '{rel['cible']}' n'existe pas")
                
                # Vérifier les relations INGESTION
                print("\n   INGESTION - Vérification des noms:")
                for rel in ingestion:
                    source_exists = rel['source'] in existing_ms
                    target_exists = rel['cible'] in existing_ms
                    status = "✅" if (source_exists and target_exists) else "❌"
                    print(f"   {status} {rel['source']:40} → {rel['cible']:40}")
                    if not source_exists:
                        print(f"      ⚠️  Source '{rel['source']}' n'existe pas")
                    if not target_exists:
                        print(f"      ⚠️  Target '{rel['cible']}' n'existe pas")
                
            except FileNotFoundError:
                print("   ❌ Fichier broker_ingestion_relations.json non trouvé!")
            
            print("\n" + "="*80)
            print("✨ VÉRIFICATION TERMINÉE")
            print("="*80)
    
    finally:
        driver.close()

if __name__ == "__main__":
    verify_relations()
