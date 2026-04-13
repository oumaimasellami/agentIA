#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔧 FIX ET ENRICHISSEMENT DES RELATIONS MESSAGE BROKER ET INGESTION
Corrige les noms des microservices et ajoute les relations manquantes
"""

import json
import re
from pathlib import Path

# Charger les vrais noms de microservices
with open("microservices.json", "r", encoding="utf-8") as f:
    microservices = json.load(f)
    
ms_names = {ms["name"] for ms in microservices}
print(f"✓ {len(ms_names)} noms de microservices chargés")

# Mapping pour corriger les noms source
def normalize_name(name):
    """Normalise un nom de microservice"""
    # Enlever le préfixe "source_"
    if name.startswith("source_"):
        name = name[7:]  # Enlever "source_"
    
    # Remplacer les underscores par des tirets
    name = name.replace("_", "-")
    
    return name

# Charger les relations actuelles
with open("broker_ingestion_relations.json", "r", encoding="utf-8") as f:
    relations = json.load(f)

print(f"✓ {len(relations)} relations chargées")

# Corriger les noms
print("\n" + "="*80)
print("🔧 CORRECTION DES NOMS")
print("="*80)

fixed_relations = []
for rel in relations:
    original_source = rel["source"]
    normalized_source = normalize_name(original_source)
    
    print(f"\n{original_source:40} → {normalized_source:40}")
    
    if normalized_source in ms_names:
        rel["source"] = normalized_source
        fixed_relations.append(rel)
        print(f"✓ TROUVÉ DANS LA BASE")
    else:
        print(f"✗ NON TROUVÉ - La relation sera ignorée")

# Ajouter des relations manuelles identifiées
print("\n" + "="*80)
print("➕ AJOUT DE RELATIONS DÉTECTÉES MANUELLEMENT")
print("="*80)

manual_relations = [
    {
        "source": "mesx-order-data-ingestion",
        "cible": "mesx-datahub",
        "type": "INGESTION",
        "ingestion_type": "Data Ingestion Service",
        "broker_type": "Azure Data Factory",
        "topics": ["orders-feed", "order-events"],
        "tables_consumed": ["Orders", "OrderItems"],
        "direction": "INPUT",
        "communication_mode": "async",
        "frequency": "real-time",
        "confidence": 0.95,
        "source_detected": "Pipeline YAML"
    },
    {
        "source": "mesx-downtime-materialized-view",
        "cible": "mesx-downtime-backend",
        "type": "INGESTION",
        "ingestion_type": "Materialized View Update",
        "broker_type": "SQL Server",
        "topics": [],
        "tables_consumed": ["DowntimeEvents", "MachineState"],
        "direction": "INPUT",
        "communication_mode": "scheduled",
        "frequency": "hourly",
        "confidence": 0.90,
        "source_detected": "Schema analysis"
    },
    {
        "source": "mesx-master-data-ingestion",
        "cible": "mesx-master-api",
        "type": "INGESTION",
        "ingestion_type": "Data Ingestion Actor",
        "broker_type": "Azure Service Bus",
        "topics": ["master-data-changed"],
        "tables_consumed": ["MasterData", "References"],
        "direction": "INPUT",
        "communication_mode": "event-driven",
        "frequency": "real-time",
        "confidence": 0.92,
        "source_detected": "Actor pattern"
    },
    {
        "source": "mesx-picking-backend",
        "cible": "mesx-picking-job-service",
        "type": "MESSAGE_BROKER",
        "broker_type": "Azure Service Bus",
        "topics": ["picking-jobs-queue", "picking-instructions"],
        "direction": "OUTPUT",
        "communication_mode": "message-queue",
        "service_namespace": "mesx-messaging",
        "frequency": "real-time",
        "confidence": 0.88,
        "source_detected": "Service Bus config"
    },
    {
        "source": "mesx-shipping-backend",
        "cible": "mesx-shipping-notification-service",
        "type": "MESSAGE_BROKER",
        "broker_type": "Azure Event Hubs",
        "topics": ["shipping-events", "delivery-status"],
        "direction": "OUTPUT",
        "communication_mode": "event-stream",
        "service_namespace": "mesx-events",
        "frequency": "real-time",
        "confidence": 0.85,
        "source_detected": "Event Hub topic"
    }
]

# Valider et ajouter les relations manuelles
print("\nValidation des relations manuelles:")
for rel in manual_relations:
    source_valid = rel["source"] in ms_names
    target_valid = rel["cible"] in ms_names
    
    status = "✓ OK" if source_valid and target_valid else "✗ KO"
    print(f"{status} {rel['source']:30} → {rel['cible']:30}")
    
    if source_valid and target_valid:
        fixed_relations.append(rel)

# Sauvegarder les relations corrigées
output_file = "broker_ingestion_relations.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(fixed_relations, f, indent=2, ensure_ascii=False)

print("\n" + "="*80)
print("✅ RÉSUMÉ")
print("="*80)
print(f"✓ Total relations avant: {len(relations)}")
print(f"✓ Total relations après: {len(fixed_relations)}")
print(f"✓ Ajout: {len(fixed_relations) - len(relations)} relations")

# Statistiques
message_broker_count = sum(1 for r in fixed_relations if r["type"] == "MESSAGE_BROKER")
ingestion_count = sum(1 for r in fixed_relations if r["type"] in ["INGESTION", "DATABASE_INGESTION"])

print(f"\n📊 TYPES:")
print(f"   • MESSAGE_BROKER: {message_broker_count}")
print(f"   • INGESTION: {ingestion_count}")

print(f"\n💾 Sauvegardé dans: {output_file}")
