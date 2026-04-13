#!/usr/bin/env python3
import json

data = json.load(open('graph_data.json', encoding='utf-8'))
print(f'UsesCases dans JSON: {len(data.get("use_cases", []))}')
print(f'COVERS dans JSON: {len(data.get("relations_covers", []))}')
print(f'Functions dans JSON: {len(data.get("fonctions", []))}')
