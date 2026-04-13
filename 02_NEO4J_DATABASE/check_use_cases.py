#!/usr/bin/env python3
import json

data = json.load(open('graph_data.json', encoding='utf-8'))
if data.get('use_cases'):
    print("Premiers UseCases:")
    for uc in data.get('use_cases', [])[:3]:
        print(json.dumps(uc, indent=2, ensure_ascii=False))
else:
    print("Aucun UseCase trouvé!")
