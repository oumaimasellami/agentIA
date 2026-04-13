#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script pour remplir graph_data.json avec les fonctions et use cases
"""

import json
import os

def populate_graph_data():
    """Remplit graph_data.json avec les fonctions"""
    
    # Charger le fichier existant
    with open("graph_data.json", "r", encoding="utf-8") as f:
        graph_data = json.load(f)
    
    # Charger les fonctions depuis ms_functions.json
    try:
        with open("ms_functions.json", "r", encoding="utf-8") as f:
            ms_functions_raw = json.load(f)
        print(f"✓ {len(ms_functions_raw)} fonctions chargées depuis ms_functions.json")
    except FileNotFoundError:
        print("✗ ms_functions.json non trouvé")
        return
    
    # Transformer les fonctions de ms_functions.json en format utilisable
    functions = []
    for func in ms_functions_raw:
        func_obj = {
            "id": func.get("function_id", f"func_{len(functions)}"),
            "nom": func.get("function_name", ""),
            "microservice": func.get("microservice_id", ""),
            "type": func.get("fonction_type", "endpoint"),
            "http_method": func.get("http_method", ""),
            "path": func.get("path", ""),
            "description": f"{func.get('fonction_type', '')} - {func.get('path', '')}"
        }
        functions.append(func_obj)
    
    # Créer les use cases basés sur les fonctions
    use_cases = []
    use_cases_set = set()
    
    for func in functions:
        # Créer des use cases basiques basés sur le type de fonction
        func_type = func.get("type", "")
        path = func.get("path", "")
        
        if not path:
            continue
        
        # Extraire le use case du chemin
        path_parts = path.split("/")
        if len(path_parts) > 1:
            usecase_name = path_parts[1].title()
        else:
            usecase_name = func_type.title()
        
        # Éviter les doublons
        if usecase_name not in use_cases_set:
            use_cases_set.add(usecase_name)
            usecase_obj = {
                "id": f"uc_{usecase_name.lower().replace(' ', '_')}",
                "titre": usecase_name,
                "description": f"Use case: {usecase_name}",
                "categorie": func.get("type", "general")
            }
            use_cases.append(usecase_obj)
    
    # Créer les relations COVERS (Function → UseCase)
    covers = []
    for func in functions:
        path = func.get("path", "")
        if not path:
            continue
        
        path_parts = path.split("/")
        if len(path_parts) > 1:
            usecase_name = path_parts[1].title()
            usecase_id = f"uc_{usecase_name.lower().replace(' ', '_')}"
            
            cover_obj = {
                "source": func.get("id"),
                "target": usecase_id,
                "type": "COVERS"
            }
            covers.append(cover_obj)
    
    # Mettre à jour graph_data
    graph_data["fonctions"] = functions
    graph_data["use_cases"] = use_cases if use_cases else [
        {
            "id": "uc_general",
            "titre": "General Use Cases",
            "description": "Default use case",
            "categorie": "general"
        }
    ]
    graph_data["relations_covers"] = covers
    
    # Sauvegarder
    with open("graph_data.json", "w", encoding="utf-8") as f:
        json.dump(graph_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ graph_data.json mis à jour:")
    print(f"   • {len(functions)} Fonctions")
    print(f"   • {len(use_cases)} Use Cases")
    print(f"   • {len(covers)} Relations COVERS (Function → UseCase)")
    
    return True

if __name__ == "__main__":
    print("\n" + "="*70)
    print("📝 REMPLISSAGE DE graph_data.json AVEC FONCTIONS ET USE CASES")
    print("="*70)
    
    populate_graph_data()
    
    print("\n✨ Prêt pour la reconstruction Neo4j!")
    print("\nCommande suivante:")
    print("   cd ../02_NEO4J_DATABASE && python build_graph_database.py")
