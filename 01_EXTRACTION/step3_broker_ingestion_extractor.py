#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔═════════════════════════════════════════════════════════════════════════════╗
║                                                                             ║
║    🔄 EXTRACTEUR DE RELATIONS MESSAGE BROKER ET DATA INGESTION              ║
║                                                                             ║
║    PFE 2025: Analyse des communications asynchrones (MQ & Ingestion)        ║
║    Cœur: Extrait des fichiers source JSON les relations non-REST           ║
║                                                                             ║
║    ANALYSE:                                                                ║
║    ✓ Azure Event Hubs (Dapr bindings & Pub/Sub)                            ║
║    ✓ Redis Streams & Pub/Sub                                               ║
║    ✓ Data Ingestion Services                                               ║
║    ✓ Database Ingestion (Materialized Views)                               ║
║    ✓ Input/Output Bindings                                                 ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Set, Tuple
from datetime import datetime

class BrokerIngestionExtractor:
    """Extrait les relations Message Broker et Ingestion des fichiers source"""
    
    def __init__(self, source_files_dir: str = "."):
        # Gérer les chemins correctement
        base_path = Path(source_files_dir).resolve()
        
        # Si on est dans 01_EXTRACTION, aller au parent
        if base_path.name == "01_EXTRACTION":
            self.source_dir = base_path.parent
        else:
            self.source_dir = base_path
            
        self.microservices: Set[str] = set()
        self.relations: List[Dict] = []
        self.analyzed_files = 0
        
    def load_microservices(self) -> None:
        """Charge la liste des microservices"""
        print("📂 Chargement des microservices...")
        try:
            with open(self.source_dir / "01_EXTRACTION" / "microservices.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            self.microservices = {ms["name"] for ms in data}
            print(f"✓ {len(self.microservices)} microservices chargés")
        except FileNotFoundError as e:
            print(f"⚠ Fichier microservices.json non trouvé: {e}")

    def extract_from_source_files(self) -> None:
        """Extrait les relations des fichiers source JSON"""
        print("\n🔍 Extraction des relations Message Broker et Ingestion...")
        
        # Parcourir tous les fichiers source_*.json
        source_files = list(self.source_dir.glob("source_*.json"))
        print(f"📄 Analysant {len(source_files)} fichiers source...")
        
        for source_file in source_files:
            self._analyze_source_file(source_file)

    def _analyze_source_file(self, file_path: Path) -> None:
        """Analyse un fichier source pour extraire les relations"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = json.load(f)
            
            if not isinstance(content, list):
                content = [content]
            
            # Extraire le nom du microservice du nom de fichier
            microservice = self._extract_microservice_name(file_path.stem)
            
            for item in content:
                if not isinstance(item, dict):
                    continue
                
                # Analyser le contenu textuel
                texte = str(item.get('contenu', '')).lower()
                
                # Détection dans le texte
                self._detect_brokers_in_text(texte, microservice)
                self._detect_ingestion_in_text(texte, microservice)
                
            self.analyzed_files += 1
        
        except Exception as e:
            pass  # Ignorer les erreurs silencieusement

    def _extract_microservice_name(self, filename: str) -> str:
        """Extrait le nom du microservice du nom de fichier"""
        # Format: source_mesx_admin_backend.json
        match = re.match(r"source_mesx_(.+?)\.json$", filename)
        if match:
            return f"mesx-{match.group(1).replace('_', '-')}"
        return filename

    def _detect_brokers_in_text(self, texte: str, source_ms: str) -> None:
        """Détecte les références à Message Brokers dans le texte"""
        
        # === AZURE SERVICE BUS / EVENT HUBS ===
        if any(kw in texte for kw in ['service.bus', 'servicebus', 'event-hubs', 'eventhubs', 
                                        'servicebus_connection_string', 'servicebus_secret']):
            # Les services liés au bus sont généralement des data ingestion
            ingestion_targets = [
                'mesx-order-data-ingestion',
                'mesx-picking-data-ingestion',
                'mesx-resource-data-ingestion',
                'mesx-production-data-ingestion',
                'mesx-quality-data-ingestion',
                'mesx-plant-hierarchy-data-ingestion',
                'mesx-shipment-data-ingestion',
                'mesx-transfer-data-ingestion',
                'mesx-master-data-ingestion',
            ]
            for target in ingestion_targets:
                if target in texte and target != source_ms and target in self.microservices:
                    self._add_relation(source_ms, target, "MESSAGE_BROKER", "Azure Service Bus")
        
        # === REDIS ===
        if any(kw in texte for kw in ['redis', 'redisstream', 'redis-stream', 'redis_stream', 
                                        'pubsub.redis', 'redis.pubsub']):
            # Chercher les services mentionnés
            service_refs = re.findall(r'mesx-[\w-]+', texte)
            for ref in set(service_refs):
                if ref != source_ms and ref in self.microservices:
                    self._add_relation(source_ms, ref, "MESSAGE_BROKER", "Redis Stream")

    def _detect_ingestion_in_text(self, texte: str, source_ms: str) -> None:
        """Détecte les références à Data Ingestion dans le texte"""
        
        # Les services d'ingestion connus
        ingestion_services = [
            'mesx-order-data-ingestion',
            'mesx-order-data-ingestion-actor',
            'mesx-order-data-ingestion-router',
            'mesx-order-data-ingestion-v2',
            'mesx-plant-hierarchy-data-ingestion',
            'mesx-resource-data-ingestion',
            'mesx-resource-data-ingestion-v2',
            'mesx-production-data-ingestion',
            'mesx-quality-data-ingestion',
            'mesx-shipment-data-ingestion',
            'mesx-transfer-data-ingestion',
            'mesx-picking-data-ingestion',
            'mesx-master-data-ingestion',
            'mesx-master-integration',
            'mesx-picking-mv-processor',
            'mesx-picking-materialized-view',
            'mesx-leveling-board-mv-processor',
            'mesx-leveling-board-materialized-view',
            'mesx-downtime-materialized-view',
            'mesx-mv-orchestrator',
            'mesx-datahub',
            'mesx-input-binding',
            'mesx-ops-mv-update',
        ]
        
        # Si le texte mentionne data-ingestion ou ingestion, chercher les liens
        if any(kw in texte for kw in ['data.ingestion', 'data-ingestion', 'data_ingestion',
                                        'ingest', 'ingestion', 'router', 'actor',
                                        'mv.processor', 'mv-processor', 'materialized.view', 'materialized-view']):
            for ing_svc in ingestion_services:
                if ing_svc in texte and ing_svc != source_ms and ing_svc in self.microservices:
                    self._add_relation(source_ms, ing_svc, "INGESTION", "Data Ingestion")

    def _add_relation(self, source: str, cible: str, rel_type: str, sous_type: str) -> None:
        """Ajoute une relation en évitant les doublons"""
        
        # Créer la clé pour la déduplication
        key = (source, cible, rel_type)
        
        # Vérifier si la relation existe déjà
        for rel in self.relations:
            if (rel['source'], rel['cible'], rel['type']) == key:
                return
        
        # Ajouter la relation
        relation = {
            'source': source,
            'cible': cible,
            'type': rel_type,
            'timestamp': datetime.now().isoformat(),
        }
        
        if rel_type == "MESSAGE_BROKER":
            relation['broker_type'] = sous_type
        else:
            relation['ingestion_type'] = sous_type
        
        self.relations.append(relation)

    def generate_relations(self) -> List[Dict]:
        """Génère les relations finales"""
        print("🔗 Génération des relations...")
        return self.relations

    def save_to_file(self, output_path: str = "broker_ingestion_relations.json") -> None:
        """Sauvegarde les relations dans un fichier JSON"""
        relations = self.generate_relations()
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(relations, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ {len(relations)} relations sauvegardées dans {output_path}")
        
        # Afficher un résumé
        print("\n📊 RÉSUMÉ DES RELATIONS EXTRAITES:")
        
        message_broker = [r for r in relations if r["type"] == "MESSAGE_BROKER"]
        ingestion = [r for r in relations if r["type"] == "INGESTION"]
        
        print(f"   • Message Broker: {len(message_broker)} relations")
        print(f"   • Data Ingestion: {len(ingestion)} relations")
        print(f"   • Total: {len(relations)} relations")
        
        if message_broker:
            print("\n   🟠 Message Broker detections:")
            for rel in message_broker[:5]:
                print(f"      {rel['source']:40} → {rel['cible']:40}")
        
        if ingestion:
            print("\n   🟡 Data Ingestion detections:")
            for rel in ingestion[:5]:
                print(f"      {rel['source']:40} → {rel['cible']:40}")


def main():
    """Fonction principale"""
    print("\n" + "="*80)
    print("🔄 EXTRACTEUR DE RELATIONS MESSAGE BROKER ET DATA INGESTION")
    print("="*80)
    
    extractor = BrokerIngestionExtractor(".")
    
    print("\n📋 ÉTAPES:")
    print("  1️⃣  Chargement des microservices")
    extractor.load_microservices()
    
    print("\n  2️⃣  Extraction des relations des fichiers source")
    extractor.extract_from_source_files()
    
    print("\n  3️⃣  Génération des relations finales")
    relations = extractor.generate_relations()
    
    print("\n  4️⃣  Sauvegarde dans JSON")
    extractor.save_to_file()
    
    print("\n" + "="*80)
    print("✅ PROCESSUS TERMINÉ")
    print("="*80)
    print("\n💡 Prochaine étape:")
    print("   cd ../02_NEO4J_DATABASE")
    print("   python build_graph_database.py")
    print("\n   Cela créera le graphe Neo4j avec les nouvelles relations!")


if __name__ == "__main__":
    main()
