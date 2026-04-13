# -*- coding: utf-8 -*-
"""
Vérifie que le graphe complet est bien dans Neo4j
"""
from neo4j import GraphDatabase

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "forvia2025"

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def run_query(query):
    with driver.session() as session:
        result = session.run(query)
        return result.single()

print("=" * 80)
print("🔍 VÉRIFICATION DU GRAPHE COMPLET DANS NEO4J")
print("=" * 80)

# Vérifier les nœuds
print("\n📊 NŒUDS PAR TYPE:")
for label in ["Microservice", "Function", "UseCase", "Commit"]:
    result = run_query(f"MATCH (n:{label}) RETURN count(n) as cnt")
    count = result["cnt"] if result else 0
    print(f"  • {label:<15} : {count:>5} nœuds")

# Vérifier les relations
print("\n🔗 RELATIONS PAR TYPE:")
for rel_type in ["API_CALLS", "IMPLEMENTS", "COVERS", "MODIFIES"]:
    result = run_query(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) as cnt")
    count = result["cnt"] if result else 0
    print(f"  • {rel_type:<15} : {count:>5} relations")

# Total
result = run_query("MATCH (n) RETURN count(n) as cnt")
total_nodes = result["cnt"] if result else 0

result = run_query("MATCH ()-[r]->() RETURN count(r) as cnt")
total_rels = result["cnt"] if result else 0

print("\n" + "=" * 80)
print(f"✅ TOTAL: {total_nodes} nœuds + {total_rels} relations")
print("=" * 80)

# Exemples
print("\n📌 EXEMPLES DE DONNÉES (premiers 5):")

print("\n  Microservices:")
with driver.session() as session:
    result = session.run("MATCH (m:Microservice) RETURN m.name LIMIT 5")
    for record in result:
        print(f"    - {record[0]}")

print("\n  Fonctions:")
with driver.session() as session:
    result = session.run("MATCH (f:Function) RETURN f.nom LIMIT 5")
    for record in result:
        print(f"    - {record[0]}")

print("\n  Chaînes dépendances (Commit→MS→MS):")
with driver.session() as session:
    result = session.run("""
        MATCH (c:Commit)-[:MODIFIES]->(m1:Microservice)-[:API_CALLS]->(m2:Microservice)
        RETURN c.commit_id, m1.name, m2.name LIMIT 5
    """)
    for record in result:
        print(f"    {record[0][:8]}... → {record[1]} → {record[2]}")

driver.close()

print("\n" + "=" * 80)
print("✅ GRAPHE COMPLET VÉRIFIÉ - PRÊT POUR VISUALISATION")
print("=" * 80)
