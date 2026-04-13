import json

with open('graph_data.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
    covers = data.get('relations_covers', [])
    print(f'Total COVERS relations: {len(covers)}')
    if covers:
        print('\nPremières 3 relations COVERS:')
        for c in covers[:3]:
            src = c.get('source')
            tgt = c.get('target')
            print(f'  source: {src}')
            print(f'  target: {tgt}')
            print()
        
        # Vérifier aussi les Functions
        functions = data.get('fonctions', [])
        print(f'\nTotal Functions: {len(functions)}')
        if functions:
            print('Premières 3 Functions:')
            for f in functions[:3]:
                fid = f.get('id')
                print(f'  id: {fid}')
