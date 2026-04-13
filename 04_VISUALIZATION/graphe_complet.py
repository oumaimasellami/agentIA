#!/usr/bin/env python3
"""
Génère un graphe complet INTERACTIF avec tous les nœuds et relations
Microservices + Fonctions + Use Cases dans une seule visualisation
"""

from neo4j import GraphDatabase
import json

URI = "bolt://localhost:7687"
USER = "neo4j"
PASSWORD = "forvia2025"

driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
session = driver.session()

print("\n" + "="*80)
print("📊 GÉNÉRATION DU GRAPHE COMPLET (81 MS + 193 FONCTIONS + 310 USE CASES)")
print("="*80)

# 1. Récupérer tous les microservices
print("\n1️⃣  Récupération des 81 microservices...")
r = session.run("""
MATCH (m:Microservice)
RETURN m.name as name, m.project as project
ORDER BY m.name
""")
microservices = r.data()
print(f"   ✓ {len(microservices)} microservices")

# 2. Récupérer toutes les fonctions
print("\n2️⃣  Récupération des 193 fonctions...")
r = session.run("""
MATCH (f:Function)
RETURN f.id as id, f.nom as nom, f.microservice as microservice, f.path as path
ORDER BY f.nom
""")
functions = r.data()
print(f"   ✓ {len(functions)} fonctions")

# 3. Récupérer tous les use cases
print("\n3️⃣  Récupération des 310 use cases...")
r = session.run("""
MATCH (u:UseCase)
RETURN u.id as id, u.titre as titre, u.priorite as priorite, u.area_path as area_path
ORDER BY u.titre
""")
use_cases = r.data()
print(f"   ✓ {len(use_cases)} use cases")

# 4. Récupérer tous les liens
print("\n4️⃣  Récupération des relations...")
# API_CALLS: Microservice -> Microservice
r = session.run("""
MATCH (a:Microservice)-[r:API_CALLS]->(b:Microservice)
RETURN a.name as source, b.name as target, type(r) as rel_type, count(*) as count
""")
api_calls = r.data()

# IMPLEMENTS: Microservice -> Function
r = session.run("""
MATCH (m:Microservice)-[r:IMPLEMENTS]->(f:Function)
RETURN m.name as source, f.id as target, type(r) as rel_type, count(*) as count
""")
implements = r.data()

# COVERS: Function -> UseCase
r = session.run("""
MATCH (f:Function)-[r:COVERS]->(u:UseCase)
RETURN f.id as source, u.id as target, type(r) as rel_type, count(*) as count
""")
covers = r.data()

print(f"   ✓ {len(api_calls)} API_CALLS")
print(f"   ✓ {len(implements)} IMPLEMENTS")
print(f"   ✓ {len(covers)} COVERS")

# Créer les données pour D3.js
nodes = []
nodes_dict = {}

# Ajouter les microservices
for i, ms in enumerate(microservices):
    node = {
        "id": ms['name'],
        "name": ms['name'],
        "type": "Microservice",
        "group": 1,
        "project": ms['project'] or "Unknown",
        "size": 15
    }
    nodes.append(node)
    nodes_dict[ms['name']] = node

# Ajouter les fonctions
for i, func in enumerate(functions):
    node = {
        "id": f"func_{func['id']}",
        "name": func['nom'],
        "type": "Function",
        "group": 2,
        "microservice": func['microservice'],
        "path": func['path'],
        "size": 10
    }
    nodes.append(node)
    nodes_dict[f"func_{func['id']}"] = node

# Ajouter les use cases
for i, uc in enumerate(use_cases):
    node = {
        "id": f"uc_{uc['id']}",
        "name": uc['titre'],
        "type": "UseCase",
        "group": 3,
        "priorite": uc['priorite'],
        "area_path": uc['area_path'],
        "size": 8
    }
    nodes.append(node)
    nodes_dict[f"uc_{uc['id']}"] = node

# Créer les liens
links = []

# API_CALLS
for call in api_calls:
    links.append({
        "source": call['source'],
        "target": call['target'],
        "type": "API_CALLS",
        "value": call['count']
    })

# IMPLEMENTS
for impl in implements:
    links.append({
        "source": impl['source'],
        "target": f"func_{impl['target']}",
        "type": "IMPLEMENTS",
        "value": 1
    })

# COVERS
for cov in covers:
    links.append({
        "source": f"func_{cov['source']}",
        "target": f"uc_{cov['target']}",
        "type": "COVERS",
        "value": 1
    })

print(f"\n5️⃣  Préparation des données...")
print(f"   ✓ {len(nodes)} nœuds totaux")
print(f"   ✓ {len(links)} relations totales")

# Générer le HTML interactif
html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MESX.0 - Graphe Complet (MS + Fonctions + Use Cases)</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0f1419 0%, #1a2332 100%);
            color: #fff;
            overflow: hidden;
        }}
        
        #container {{
            display: flex;
            height: 100vh;
        }}
        
        #graph {{
            flex: 1;
            background: #0f1419;
            position: relative;
        }}
        
        #sidebar {{
            width: 380px;
            background: rgba(10, 20, 40, 0.98);
            padding: 20px;
            overflow-y: auto;
            border-left: 1px solid #1e3a5f;
            box-shadow: -3px 0 25px rgba(0,0,0,0.8);
        }}
        
        h1 {{
            font-size: 20px;
            margin-bottom: 15px;
            color: #00d4ff;
            text-shadow: 0 2px 8px rgba(0,0,0,0.8);
            border-bottom: 2px solid #00d4ff;
            padding-bottom: 10px;
        }}
        
        .stats {{
            background: linear-gradient(135deg, rgba(0, 212, 255, 0.15), rgba(77, 184, 255, 0.1));
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            border-left: 4px solid #00d4ff;
            box-shadow: 0 4px 15px rgba(0, 212, 255, 0.1);
        }}
        
        .stat-item {{
            display: flex;
            justify-content: space-between;
            margin: 8px 0;
            font-size: 13px;
            align-items: center;
        }}
        
        .stat-label {{
            color: #aaa;
            flex: 1;
        }}
        
        .stat-value {{
            color: #00d4ff;
            font-weight: bold;
            font-size: 16px;
        }}
        
        .legend {{
            background: rgba(30, 58, 95, 0.6);
            padding: 15px;
            border-radius: 8px;
            margin-top: 20px;
            border-left: 4px solid #4db8ff;
        }}
        
        .legend h3 {{
            font-size: 13px;
            color: #4db8ff;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        
        .legend-item {{
            display: flex;
            align-items: center;
            margin: 8px 0;
            font-size: 12px;
        }}
        
        .legend-color {{
            width: 14px;
            height: 14px;
            border-radius: 50%;
            margin-right: 10px;
            border: 1px solid rgba(255,255,255,0.3);
        }}
        
        .search-box {{
            margin-top: 15px;
            margin-bottom: 15px;
        }}
        
        .search-box input {{
            width: 100%;
            padding: 10px;
            background: rgba(77, 184, 255, 0.1);
            border: 1px solid #4db8ff;
            color: #fff;
            border-radius: 5px;
            font-size: 12px;
        }}
        
        .search-box input::placeholder {{
            color: #666;
        }}
        
        .search-box input:focus {{
            outline: none;
            background: rgba(77, 184, 255, 0.2);
            border-color: #00d4ff;
        }}
        
        .node-list {{
            margin-top: 15px;
            max-height: 400px;
            overflow-y: auto;
        }}
        
        .node-item {{
            padding: 8px 10px;
            margin: 4px 0;
            background: rgba(77, 184, 255, 0.05);
            border-left: 3px solid #4db8ff;
            border-radius: 4px;
            font-size: 11px;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        
        .node-item:hover {{
            background: rgba(77, 184, 255, 0.15);
            border-left-color: #00d4ff;
            padding-left: 12px;
        }}
        
        .node-item.microservice {{
            border-left-color: #ff6b6b;
        }}
        
        .node-item.function {{
            border-left-color: #4ecdc4;
        }}
        
        .node-item.usecase {{
            border-left-color: #45b7d1;
        }}
        
        svg {{
            width: 100%;
            height: 100%;
        }}
        
        .node {{
            cursor: pointer;
            stroke: #0f1419;
            stroke-width: 1.5px;
            transition: all 0.2s;
            filter: drop-shadow(0 0 3px rgba(0,0,0,0.5));
        }}
        
        .node:hover {{
            stroke-width: 2.5px;
            filter: drop-shadow(0 0 10px rgba(77, 184, 255, 0.8));
        }}
        
        .node.selected {{
            stroke-width: 3px;
            filter: drop-shadow(0 0 15px rgba(0, 212, 255, 1));
        }}
        
        .link {{
            stroke-opacity: 0.5;
            transition: all 0.2s;
        }}
        
        .link.API_CALLS {{
            stroke: rgba(255, 107, 107, 0.4);
            stroke-width: 2px;
        }}
        
        .link.IMPLEMENTS {{
            stroke: rgba(78, 205, 196, 0.3);
            stroke-width: 1.5px;
        }}
        
        .link.COVERS {{
            stroke: rgba(69, 183, 209, 0.2);
            stroke-width: 1px;
            stroke-dasharray: 5,5;
        }}
        
        .link.highlighted {{
            stroke-opacity: 0.9;
            stroke-width: 3px;
        }}
        
        .node-label {{
            pointer-events: none;
            font-size: 10px;
            fill: #fff;
            text-anchor: middle;
            opacity: 0;
            transition: opacity 0.2s;
            text-shadow: 0 0 4px rgba(0,0,0,0.8);
        }}
        
        .node-label.visible {{
            opacity: 1;
        }}
        
        .tooltip {{
            position: absolute;
            padding: 12px 16px;
            background: rgba(0, 0, 0, 0.95);
            color: #fff;
            border-radius: 6px;
            font-size: 12px;
            pointer-events: none;
            z-index: 1000;
            border: 2px solid #00d4ff;
            box-shadow: 0 8px 32px rgba(0, 212, 255, 0.2);
            max-width: 250px;
        }}
        
        .info-panel {{
            background: rgba(0, 212, 255, 0.1);
            padding: 15px;
            border-radius: 6px;
            margin-top: 15px;
            border-left: 4px solid #00d4ff;
            font-size: 12px;
        }}
        
        .info-panel.hidden {{
            display: none;
        }}
        
        .info-title {{
            color: #00d4ff;
            font-weight: bold;
            margin-bottom: 8px;
        }}
        
        .info-content {{
            color: #ccc;
            line-height: 1.5;
        }}
    </style>
</head>
<body>
    <div id="container">
        <div id="graph"></div>
        <div id="sidebar">
            <h1>🎯 MESX.0 - Graphe Complet</h1>
            
            <div class="stats">
                <div class="stat-item">
                    <span class="stat-label">🏢 Microservices:</span>
                    <span class="stat-value">{len(microservices)}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">⚙️ Fonctions:</span>
                    <span class="stat-value">{len(functions)}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">✓ Use Cases:</span>
                    <span class="stat-value">{len(use_cases)}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">🔗 Relations:</span>
                    <span class="stat-value">{len(links)}</span>
                </div>
            </div>
            
            <div class="legend">
                <h3>🔵 Types de Nœuds</h3>
                <div class="legend-item">
                    <div class="legend-color" style="background: #ff6b6b;"></div>
                    <span>Microservices (81)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background: #4ecdc4;"></div>
                    <span>Fonctions (193)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background: #45b7d1;"></div>
                    <span>Use Cases (310)</span>
                </div>
            </div>
            
            <div class="legend" style="border-left-color: #ff6b6b;">
                <h3>🔗 Types de Relations</h3>
                <div class="legend-item">
                    <div style="width: 20px; height: 3px; background: rgba(255, 107, 107, 0.8); margin-right: 10px;"></div>
                    <span>API_CALLS (MS → MS)</span>
                </div>
                <div class="legend-item">
                    <div style="width: 20px; height: 2px; background: rgba(78, 205, 196, 0.6); margin-right: 10px;"></div>
                    <span>IMPLEMENTS (MS → Fonction)</span>
                </div>
                <div class="legend-item">
                    <div style="width: 20px; height: 2px; background: rgba(69, 183, 209, 0.5); stroke-dasharray: 5,5; margin-right: 10px; border-bottom: 1px dashed rgba(69, 183, 209, 0.5);"></div>
                    <span>COVERS (Fonction → UC)</span>
                </div>
            </div>
            
            <div class="search-box">
                <input type="text" id="search" placeholder="Chercher un nœud...">
            </div>
            
            <div id="info-panel" class="info-panel hidden">
                <div class="info-title" id="info-title"></div>
                <div class="info-content" id="info-content"></div>
            </div>
        </div>
    </div>
    
    <div id="tooltip" class="tooltip" style="display: none;"></div>

    <script>
        const width = document.getElementById('graph').clientWidth;
        const height = document.getElementById('graph').clientHeight;
        
        const data = {{
            nodes: {json.dumps(nodes)},
            links: {json.dumps(links)}
        }};

        const svg = d3.select("#graph")
            .append("svg")
            .attr("width", width)
            .attr("height", height);

        const g = svg.append("g");

        const simulation = d3.forceSimulation(data.nodes)
            .force("link", d3.forceLink(data.links)
                .id(d => d.id)
                .distance(d => {{
                    if (d.type === "API_CALLS") return 80;
                    if (d.type === "IMPLEMENTS") return 100;
                    return 120;
                }})
                .strength(0.2))
            .force("charge", d3.forceManyBody()
                .strength(-500)
                .distanceMax(500))
            .force("center", d3.forceCenter(width / 2, height / 2))
            .force("collision", d3.forceCollide(15));

        // Créer les liens
        const link = g.selectAll("line")
            .data(data.links)
            .enter()
            .append("line")
            .attr("class", d => `link ${{d.type}}`)
            .attr("stroke-width", d => {{
                if (d.type === "API_CALLS") return Math.sqrt(d.value);
                return 1;
            }});

        // Créer les nœuds
        const nodeElements = g.selectAll("circle")
            .data(data.nodes)
            .enter()
            .append("circle")
            .attr("class", "node")
            .attr("r", d => {{
                if (d.type === "Microservice") return 12;
                if (d.type === "Function") return 8;
                return 6;
            }})
            .attr("fill", d => {{
                if (d.type === "Microservice") return "#ff6b6b";
                if (d.type === "Function") return "#4ecdc4";
                return "#45b7d1";
            }})
            .call(drag(simulation))
            .on("click", (event, d) => selectNode(d, event))
            .on("mouseover", (event, d) => showTooltip(event, d))
            .on("mouseout", hideTooltip);

        // Labels
        const labels = g.selectAll(".node-label")
            .data(data.nodes)
            .enter()
            .append("text")
            .attr("class", "node-label")
            .attr("dy", -8)
            .text(d => d.name.substring(0, 15));

        // Zoom
        svg.call(d3.zoom()
            .on("zoom", (event) => {{
                g.attr("transform", event.transform);
            }}));

        // Mise à jour des positions
        simulation.on("tick", () => {{
            link
                .attr("x1", d => d.source.x)
                .attr("y1", d => d.source.y)
                .attr("x2", d => d.target.x)
                .attr("y2", d => d.target.y);

            nodeElements
                .attr("cx", d => d.x)
                .attr("cy", d => d.y);

            labels
                .attr("x", d => d.x)
                .attr("y", d => d.y);
        }});

        function selectNode(d, event) {{
            nodeElements.classed("selected", false);
            link.classed("highlighted", false);
            labels.classed("visible", false);

            d3.select(event.target).classed("selected", true);
            labels.filter(label => label.id === d.id).classed("visible", true);

            link.classed("highlighted", l => 
                l.source.id === d.id || l.target.id === d.id
            );

            showInfoPanel(d);
        }}

        function showInfoPanel(d) {{
            const panel = document.getElementById("info-panel");
            const title = document.getElementById("info-title");
            const content = document.getElementById("info-content");
            
            title.textContent = `${{d.type}}: ${{d.name}}`;
            
            let info = "";
            if (d.type === "Microservice") {{
                info = `Projet: ${{d.project}}<br/>Relations: ${{data.links.filter(l => l.source.id === d.id || l.target.id === d.id).length}}`;
            }} else if (d.type === "Function") {{
                info = `Microservice: ${{d.microservice}}<br/>Path: ${{d.path || "N/A"}}`;
            }} else {{
                info = `Priorité: ${{d.priorite || "Normal"}}<br/>Area: ${{d.area_path || "N/A"}}`;
            }}
            
            content.innerHTML = info;
            panel.classList.remove("hidden");
        }}

        function showTooltip(event, d) {{
            const tooltip = document.getElementById("tooltip");
            const outgoing = data.links.filter(l => l.source.id === d.id).length;
            const incoming = data.links.filter(l => l.target.id === d.id).length;
            
            let html = `<strong style="color: #00d4ff;">${{d.name}}</strong><br/>Type: ${{d.type}}<br/>`;
            html += `📤 Out: ${{outgoing}}<br/>📥 In: ${{incoming}}`;
            
            tooltip.innerHTML = html;
            tooltip.style.display = 'block';
            tooltip.style.left = (event.pageX + 15) + 'px';
            tooltip.style.top = (event.pageY + 15) + 'px';
        }}

        function hideTooltip() {{
            document.getElementById("tooltip").style.display = 'none';
        }}

        function drag(simulation) {{
            function dragstarted(event, d) {{
                if (!event.active) simulation.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
            }}
            function dragged(event, d) {{
                d.fx = event.x;
                d.fy = event.y;
            }}
            function dragended(event, d) {{
                if (!event.active) simulation.alphaTarget(0);
                d.fx = null;
                d.fy = null;
            }}
            return d3.drag()
                .on("start", dragstarted)
                .on("drag", dragged)
                .on("end", dragended);
        }}

        // Recherche
        document.getElementById("search").addEventListener("input", (e) => {{
            const query = e.target.value.toLowerCase();
            nodeElements.classed("highlight", d => 
                d.name.toLowerCase().includes(query)
            );
        }});
    </script>
</body>
</html>
"""

# Écrire le fichier HTML
print("\n6️⃣  Génération du fichier HTML...")
with open("graphe_complet.html", "w", encoding="utf-8") as f:
    f.write(html)

print("   ✓ Fichier généré: graphe_complet.html")

print("\n" + "="*80)
print("✅ GRAPHE COMPLET CRÉÉ AVEC SUCCÈS !")
print("="*80)
print(f"\n📊 Résumé:")
print(f"   • {len(microservices)} Microservices (🔴 rouges)")
print(f"   • {len(functions)} Fonctions (🔵 cyan)")
print(f"   • {len(use_cases)} Use Cases (🔵 bleus)")
print(f"   • {len(links)} Relations totales")
print(f"\n📈 Détail des relations:")
print(f"   • {len(api_calls)} API_CALLS (MS → MS) - Lignes rouges épaisses")
print(f"   • {len(implements)} IMPLEMENTS (MS → Fonction) - Lignes cyan")
print(f"   • {len(covers)} COVERS (Fonction → UC) - Lignes bleues pointillées")

print("\n🌐 Ouvrir dans le navigateur:")
print("   → graphe_complet.html")

print("\n🎮 Interactions:")
print("   • Click sur un nœud = voir ses connexions")
print("   • Survol = voir les détails")
print("   • Scroll = zoomer")
print("   • Glisser = repositionner")
print("   • Rechercher = filtrer les nœuds")

print("\n" + "="*80)

session.close()
driver.close()
