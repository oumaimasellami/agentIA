#!/usr/bin/env python3
import json

data = json.load(open('graph_data.json', encoding='utf-8'))
if data.get('relations_covers'):
    print("Premiers COVERS:")
    for cov in data.get('relations_covers', [])[:5]:
        print(json.dumps(cov, indent=2, ensure_ascii=False))
else:
    print("Aucune relation COVERS trouvée!")
