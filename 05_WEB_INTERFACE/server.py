"""
NRT SCOPE VISUALIZATION SYSTEM - Lightweight Web Server
Pure Python HTTP Server without external dependencies (except neo4j)
Serves HTML templates and provides JSON API endpoints
"""

import http.server
import socketserver
import json
import os
import urllib.parse
from pathlib import Path
import mimetypes
from neo4j import GraphDatabase
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Neo4j Configuration from environment variables
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "forvia2025")

class NRTAnalyzer:
    """Analyze NRT scopes from Neo4j"""
    
    def __init__(self):
        try:
            self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
            self.driver.verify_connectivity()
            print("✅ Connected to Neo4j database")
        except Exception as e:
            print(f"❌ Failed to connect to Neo4j: {e}")
            self.driver = None
        
        # Load workitems with parent-child relationships
        self.workitems_data = self._load_workitems()
        print(f"✅ Loaded {len(self.workitems_data)} WorkItems from JSON")
    
    def _load_workitems(self):
        """Load WorkItems from JSON file with parent-child relationships"""
        try:
            json_path = Path(__file__).parent.parent / "work_items.json"
            with open(json_path, 'r', encoding='utf-8') as f:
                items = json.load(f)
            
            # Index by ID for fast lookup
            indexed = {}
            for item in items:
                indexed[item['id']] = item
            
            print(f"   - Loaded {len(indexed)} WorkItems")
            return indexed
        except Exception as e:
            print(f"⚠️  Could not load WorkItems JSON: {e}")
            return {}
    
    def get_workitem_children(self, workitem_id, commit_id=None):
        """Get child WorkItems for a parent (optionally filtered by commit)"""
        if workitem_id not in self.workitems_data:
            return []
        
        parent = self.workitems_data[workitem_id]
        
        # Si un commit_id est fourni, retourner les enfants spécifiques au commit
        if commit_id:
            commit_children = parent.get('children_by_commit', {})
            # Extraire les 8 premiers caractères du commit pour chercher dans le dictionnaire
            commit_short = commit_id[:8] if len(commit_id) > 8 else commit_id
            children_ids = commit_children.get(commit_short, parent.get('children_ids', []))
        else:
            # Sinon, retourner tous les enfants
            children_ids = parent.get('children_ids', [])
        
        children = []
        for child_id in children_ids:
            if child_id in self.workitems_data:
                child = self.workitems_data[child_id]
                children.append({
                    'id': child['id'],
                    'type': child['type'],
                    'titre': child['titre'],
                    'statut': child['statut'],
                    'priorite': child['priorite'],
                    'assigne_a': child.get('assigne_a', ''),
                    'tags': child.get('tags', '')
                })
        
        return children
    
    def get_statistics(self):
        """Get overall system statistics"""
        if not self.driver:
            return {}
        
        try:
            with self.driver.session() as session:
                # Basic counts
                result = session.run("""
                MATCH (n) RETURN COUNT(n) as nodes
                """)
                nodes_count = result.single()['nodes']
                
                result = session.run("""
                MATCH ()-[r]->() RETURN COUNT(r) as relations
                """)
                relations_count = result.single()['relations']
                
                result = session.run("""
                MATCH (c:Commit) RETURN COUNT(c) as count
                """)
                commit_count = result.single()['count'] or 0
                
                result = session.run("""
                MATCH (ms:Microservice) RETURN COUNT(ms) as count
                """)
                microservice_count = result.single()['count'] or 0
                
                result = session.run("""
                MATCH (f:Function) RETURN COUNT(f) as count
                """)
                function_count = result.single()['count'] or 0
                
                result = session.run("""
                MATCH (wi:WorkItem) RETURN COUNT(wi) as count
                """)
                workitem_count = result.single()['count'] or 0
                
                result = session.run("""
                MATCH ()-[r:MODIFIES]->() RETURN COUNT(r) as count
                """)
                modifies_count = result.single()['count'] or 0
                
                result = session.run("""
                MATCH ()-[r:IMPLEMENTS]->() RETURN COUNT(r) as count
                """)
                implements_count = result.single()['count'] or 0
                
                result = session.run("""
                MATCH ()-[r:COVERS]->() RETURN COUNT(r) as count
                """)
                covers_count = result.single()['count'] or 0
                
                return {
                    "total_nodes": nodes_count,
                    "total_relations": relations_count,
                    "commit_count": commit_count,
                    "microservice_count": microservice_count,
                    "function_count": function_count,
                    "workitem_count": workitem_count,
                    "modifies_count": modifies_count,
                    "implements_count": implements_count,
                    "covers_count": covers_count
                }
        except Exception as e:
            print(f"Error getting statistics: {e}")
            return {"error": str(e)}
    
    def get_commit_impact(self, commit_id, workitem_id=None):
        """Get impact of a specific commit - IDENTICAL to Neo4j queries"""
        if not self.driver:
            return {"error": "Database not connected"}
        
        try:
            with self.driver.session() as session:
                # MODE 1: Specific WorkItem (EXACT same logic as Neo4j graph)
                if workitem_id:
                    try:
                        workitem_id = int(workitem_id)
                    except:
                        return {"error": f"Invalid WorkItem ID: {workitem_id}"}
                    
                    result = session.run("""
                    MATCH (c:Commit {commit_id: $commit_id})-[mod:MODIFIES]->(ms:Microservice)
                          -[impl:IMPLEMENTS]->(f:Function)
                          -[cov:COVERS]-(wi:WorkItem {id: $workitem_id})
                    RETURN 
                        c.commit_id as commit_id,
                        c.auteur as author,
                        c.message as message,
                        c.date as date,
                        COLLECT(DISTINCT ms.name) as microservices,
                        COLLECT(DISTINCT {id: f.id, name: COALESCE(f.nom, f.name, f.classe, split(f.id, ':')[-1]), microservice: ms.name}) as functions,
                        COUNT(DISTINCT f.id) as function_count,
                        COUNT(DISTINCT ms.name) as microservice_count,
                        COLLECT(DISTINCT {id: wi.id, titre: wi.titre}) as workitems,
                        COUNT(DISTINCT wi.id) as workitem_count
                    """, commit_id=commit_id, workitem_id=workitem_id)
                    
                    record = result.single()
                    if record:
                        functions = record['functions'] or []
                        microservices = [ms for ms in record['microservices'] if ms] if record['microservices'] else []
                        workitems = record['workitems'] or []
                        
                        # Get children of this WorkItem (filtered by commit if available)
                        children_to_test = self.get_workitem_children(workitem_id, commit_id)
                        
                        return {
                            "commit_id": record['commit_id'],
                            "author": record['author'] or "Unknown",
                            "message": record['message'] or "No message",
                            "date": record['date'] or datetime.now().isoformat(),
                            "microservices": [{"name": ms} for ms in microservices],
                            "microservice_count": record['microservice_count'],
                            "functions": functions,
                            "function_count": record['function_count'],
                            "workitems": [{"id": wi['id'], "titre": wi['titre']} for wi in workitems if wi],
                            "workitem_count": record['workitem_count'],
                            "workitem_id": workitem_id,
                            "children_to_test": children_to_test,
                            "children_count": len(children_to_test),
                            "impact_path": f"Commit → Microservice → Function → WorkItem {workitem_id} ✓",
                            "logical_validation": "✓ IDENTICAL to Neo4j (Symbolic AI coherent)",
                            "mode": "SPECIFIC (Neo4j aligned)"
                        }
                    return {"error": f"No impact chain found for commit {commit_id[:8]}... with workitem {workitem_id}"}
                
                # MODE 2: All WorkItems (EXACT same logic as Neo4j, NO LIMIT)
                else:
                    result = session.run("""
                    MATCH (c:Commit {commit_id: $commit_id})-[mod:MODIFIES]->(ms:Microservice)
                          -[impl:IMPLEMENTS]->(f:Function)
                          -[cov:COVERS]-(wi:WorkItem)
                    RETURN
                        c.commit_id as commit_id,
                        c.auteur as author,
                        c.message as message,
                        c.date as date,
                        COLLECT(DISTINCT ms.name) as microservices,
                        COLLECT(DISTINCT {id: f.id, name: COALESCE(f.nom, f.name, f.classe, split(f.id, ':')[-1]), microservice: ms.name}) as functions,
                        COUNT(DISTINCT f.id) as function_count,
                        COUNT(DISTINCT ms.name) as microservice_count,
                        COLLECT(DISTINCT {id: wi.id, titre: wi.titre}) as workitems,
                        COUNT(DISTINCT wi.id) as workitem_count
                    """, commit_id=commit_id)
                    
                    record = result.single()
                    if record:
                        functions = record['functions'] or []
                        microservices = [ms for ms in record['microservices'] if ms] if record['microservices'] else []
                        workitems = record['workitems'] or []
                        
                        return {
                            "commit_id": record['commit_id'],
                            "author": record['author'] or "Unknown",
                            "message": record['message'] or "No message",
                            "date": record['date'] or datetime.now().isoformat(),
                            "microservices": [{"name": ms} for ms in microservices],
                            "microservice_count": record['microservice_count'],
                            "functions": functions,
                            "function_count": record['function_count'],
                            "workitems": [{"id": wi['id'], "titre": wi['titre']} for wi in workitems if wi],
                            "workitem_count": record['workitem_count'],
                            "impact_path": "Commit → Microservice → Function → All WorkItems",
                            "logical_validation": "✓ IDENTICAL to Neo4j (Symbolic AI coherent)",
                            "mode": "FULL (Neo4j aligned, NO LIMITS)"
                        }
                    return {"error": "Commit not found"}
        except Exception as e:
            return {"error": str(e)}
    
    def get_workitem_scope(self, workitem_id):
        """Get complete NRT scope for a WorkItem"""
        if not self.driver:
            return {"error": "Database not connected"}
        
        try:
            workitem_id = int(workitem_id)
            with self.driver.session() as session:
                # Get workitem basic info
                result = session.run("""
                MATCH (wi:WorkItem {id: $workitem_id})
                RETURN wi.titre as title, wi.id as id
                """, workitem_id=workitem_id)
                
                wi_record = result.single()
                if not wi_record:
                    return {"error": "WorkItem not found"}
                
                # Get all related commits, microservices, functions
                result = session.run("""
                MATCH (c:Commit)-[r:MODIFIES]->(ms:Microservice)
                      -[r2:IMPLEMENTS]->(f:Function)
                      -[r3:COVERS]-(wi:WorkItem {id: $workitem_id})
                RETURN DISTINCT
                    COLLECT(DISTINCT c.commit_id) as commits,
                    COLLECT(DISTINCT ms.name) as microservices,
                    COLLECT(DISTINCT f.id) as functions,
                    COLLECT(DISTINCT c.auteur) as authors,
                    COUNT(DISTINCT c.commit_id) as commit_count
                """, workitem_id=workitem_id)
                
                record = result.single()
                if record:
                    commits = [c for c in record['commits'] if c]
                    microservices = [ms for ms in record['microservices'] if ms]
                    functions = [f for f in record['functions'] if f]
                    authors = [a for a in record['authors'] if a]
                    
                    return {
                        "workitem_id": workitem_id,
                        "title": wi_record['title'],
                        "commits": commits,
                        "microservices": microservices,
                        "functions": functions,
                        "authors": authors,
                        "commit_count": len(set(commits)),
                        "microservice_count": len(set(microservices)),
                        "function_count": len(set(functions)),
                        "author_count": len(set(authors))
                    }
                
                return {"error": "No scope data found"}
        except Exception as e:
            return {"error": str(e)}
    
    def search_workitems(self, query):
        """Search workitems by title"""
        if not self.driver:
            return {"error": "Database not connected"}
        
        try:
            with self.driver.session() as session:
                result = session.run("""
                MATCH (wi:WorkItem)
                WHERE wi.titre CONTAINS $query
                RETURN wi.id as id, wi.titre as title
                LIMIT 10
                """, query=query)
                
                return {
                    "results": [{"id": record['id'], "title": record['title']} for record in result]
                }
        except Exception as e:
            return {"error": str(e)}
    
    def get_workitem_types(self):
        """Get only User Story and Bug types"""
        if not self.workitems_data:
            return {"error": "No WorkItems loaded"}
        
        try:
            types = {"User Story": 0, "Bug": 0}
            
            for wi in self.workitems_data.values():
                wi_type = wi.get('type', '')
                if wi_type in types:
                    types[wi_type] += 1
            
            return {
                "types": [{"name": k, "count": v} for k, v in sorted(types.items()) if v > 0]
            }
        except Exception as e:
            return {"error": str(e)}
    
    def get_workitems_by_type(self, workitem_type):
        """Get all WorkItems filtered by type"""
        if not self.workitems_data:
            return {"error": "No WorkItems loaded"}
        
        try:
            filtered = []
            for wi in self.workitems_data.values():
                if wi.get('type', '').lower() == workitem_type.lower():
                    filtered.append({
                        'id': wi['id'],
                        'titre': wi['titre'],
                        'type': wi.get('type', ''),
                        'statut': wi.get('statut', ''),
                        'priorite': wi.get('priorite', 0),
                        'assigne_a': wi.get('assigne_a', '')
                    })
            
            filtered.sort(key=lambda x: x['id'])
            
            return {
                "type": workitem_type,
                "count": len(filtered),
                "results": filtered
            }
        except Exception as e:
            return {"error": str(e)}
    
    def get_nrt_scope(self, workitem_id):
        """NEW ARCHITECTURE: Get complete NRT scope for a WorkItem
        INPUT: WorkItem ID only
        OUTPUT: Direct + Indirect Microservices with Functions"""
        if not self.driver:
            return {"error": "Database not connected"}
        
        try:
            workitem_id = int(workitem_id)
            
            # 1. Verify WorkItem exists
            with self.driver.session() as session:
                result = session.run("""
                MATCH (wi:WorkItem {id: $wi_id})
                RETURN wi.id as id, wi.titre as title, wi.type as type
                """, wi_id=workitem_id)
                
                wi_record = result.single()
                if not wi_record:
                    return {"error": f"WorkItem {workitem_id} not found"}
            
            # 2. Find DIRECT Microservices
            direct_ms = self.find_direct_microservices_with_functions(workitem_id)
            
            # 3. Find INDIRECT Microservices via dependencies
            indirect_ms = self.find_indirect_microservices_with_functions(workitem_id, direct_ms)
            
            # 4. Calculate statistics
            total_functions = sum(ms.get("function_count", 0) for ms in direct_ms + indirect_ms)
            
            return {
                "status": "SUCCESS",
                "workitem": {
                    "id": wi_record['id'],
                    "title": wi_record['title'],
                    "type": wi_record['type']
                },
                "direct_microservices": direct_ms,
                "indirect_microservices": indirect_ms,
                "summary": {
                    "direct_ms_count": len(direct_ms),
                    "indirect_ms_count": len(indirect_ms),
                    "total_ms_count": len(direct_ms) + len(indirect_ms),
                    "total_functions": total_functions,
                    "estimated_test_cases": len(direct_ms) + len(indirect_ms) * 2 + total_functions
                }
            }
        except Exception as e:
            return {"error": str(e)}
    
    def find_direct_microservices_with_functions(self, workitem_id):
        """Find DIRECT microservices for a WorkItem with their functions"""
        if not self.driver:
            return []
        
        try:
            direct_ms = {}
            
            with self.driver.session() as session:
                result = session.run("""
                MATCH (wi:WorkItem {id: $wi_id})<-[:COVERS]-(f:Function)
                                                    <-[:IMPLEMENTS]-(ms:Microservice)
                RETURN DISTINCT ms.name as ms_name, 
                       COLLECT({
                           id: f.id, 
                           name: COALESCE(f.name, f.nom, f.classe, split(f.id, ':')[-1])
                       }) as functions
                ORDER BY ms_name
                """, wi_id=workitem_id)
                
                for record in result:
                    ms_name = record['ms_name']
                    functions = record['functions'] or []
                    
                    direct_ms[ms_name] = {
                        "name": ms_name,
                        "type": "DIRECT",
                        "functions": functions,
                        "function_count": len(functions),
                        "test_cases_to_generate": len(functions)
                    }
            
            return list(direct_ms.values())
        except Exception as e:
            print(f"Error in find_direct_microservices_with_functions: {e}")
            return []
    
    def find_indirect_microservices_with_functions(self, workitem_id, direct_ms_list):
        """Find INDIRECT microservices via dependencies (API_CALLS, MESSAGE_BROKER, INGESTION)"""
        if not self.driver:
            return []
        
        try:
            indirect_ms = {}
            
            # Get list of direct MS names
            direct_ms_names = [ms["name"] for ms in direct_ms_list]
            
            with self.driver.session() as session:
                for ms_name in direct_ms_names:
                    # Find dependencies from this MS
                    result = session.run("""
                    MATCH (ms1:Microservice {name: $ms_name})-[rel:API_CALLS|MESSAGE_BROKER|INGESTION]->(ms2:Microservice)
                    OPTIONAL MATCH (ms2)-[:IMPLEMENTS]->(f:Function)
                    RETURN DISTINCT ms2.name as target_ms, 
                           type(rel) as dep_type,
                           COLLECT({
                               id: f.id,
                               name: COALESCE(f.name, f.nom, f.classe, split(f.id, ':')[-1])
                           }) as functions
                    """, ms_name=ms_name)
                    
                    for record in result:
                        target_ms = record['target_ms']
                        
                        # Avoid duplicates: store only once
                        if target_ms not in indirect_ms:
                            functions = [f for f in (record['functions'] or []) if f['id']]
                            
                            indirect_ms[target_ms] = {
                                "name": target_ms,
                                "type": "INDIRECT",
                                "source_microservice": ms_name,
                                "dependency_type": record['dep_type'],
                                "functions": functions,
                                "function_count": len(functions),
                                "test_cases_to_generate": len(functions)
                            }
            
            return list(indirect_ms.values())
        except Exception as e:
            print(f"Error in find_indirect_microservices_with_functions: {e}")
            return []

# Initialize analyzer
analyzer = NRTAnalyzer()

class NRTRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler for NRT system"""
    
    def do_GET(self):
        """Handle GET requests"""
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        query_params = urllib.parse.parse_qs(parsed_path.query)
        
        # API endpoints
        if path == '/api/statistics':
            self.send_json_response(analyzer.get_statistics())
        elif path == '/api/workitem-types':
            self.send_json_response(analyzer.get_workitem_types())
        elif path.startswith('/api/workitems-by-type/'):
            workitem_type = urllib.parse.unquote(path.split('/api/workitems-by-type/')[-1])
            self.send_json_response(analyzer.get_workitems_by_type(workitem_type))
        elif path == '/api/nrt-scope' or path.startswith('/api/nrt-scope/'):
            # NEW: NRT Scope by WorkItem ID only
            workitem_id = query_params.get('workitem_id', [None])[0]
            if not workitem_id:
                workitem_id = path.split('/api/nrt-scope/')[-1] if '/api/nrt-scope/' in path else None
            
            if workitem_id:
                self.send_json_response(analyzer.get_nrt_scope(workitem_id))
            else:
                self.send_json_response({"error": "Missing workitem_id parameter"})
        elif path.startswith('/api/commit/'):
            commit_id = path.split('/api/commit/')[-1]
            workitem_id = query_params.get('workitem', [None])[0]
            self.send_json_response(analyzer.get_commit_impact(commit_id, workitem_id))
        elif path.startswith('/api/workitem/'):
            workitem_id = path.split('/api/workitem/')[-1]
            self.send_json_response(analyzer.get_workitem_scope(workitem_id))
        elif path.startswith('/api/search'):
            query = query_params.get('q', [''])[0]
            self.send_json_response(analyzer.search_workitems(query))
        elif path == '/' or path == '/index.html':
            self.serve_template('templates/dashboard.html')
        elif path == '/commit-analysis':
            self.serve_template('templates/commit_analysis.html')
        elif path == '/workitem-scope':
            self.serve_template('templates/workitem_scope.html')
        elif path == '/graph-visualization':
            self.serve_template('templates/graph_visualization.html')
        elif path == '/reports':
            self.serve_template('templates/reports.html')
        elif path.startswith('/templates/'):
            file_path = path.lstrip('/')
            self.serve_file(file_path)
        elif path.startswith('/static/'):
            file_path = path.lstrip('/')
            self.serve_file(file_path)
        else:
            self.send_error(404, 'Not found')
    
    def serve_template(self, template_path):
        """Serve an HTML template"""
        try:
            full_path = Path(template_path)
            if full_path.exists():
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(content.encode('utf-8'))
            else:
                self.send_error(404, f'Template not found: {template_path}')
        except Exception as e:
            self.send_error(500, str(e))
    
    def serve_file(self, file_path):
        """Serve a static file"""
        try:
            full_path = Path(file_path)
            if full_path.exists() and full_path.is_file():
                mime_type, _ = mimetypes.guess_type(str(full_path))
                with open(full_path, 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-type', mime_type or 'application/octet-stream')
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_error(404)
        except Exception as e:
            self.send_error(500, str(e))
    
    def send_json_response(self, data):
        """Send JSON response"""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))
    
    def log_message(self, format, *args):
        """Custom logging"""
        print(f"[{self.log_date_time_string()}] {format % args}")
    
    def end_headers(self):
        """Add CORS headers"""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

if __name__ == '__main__':
    PORT = 5000
    Handler = NRTRequestHandler
    
    try:
        with socketserver.TCPServer(("", PORT), Handler) as httpd:
            print(f"""
╔═══════════════════════════════════════════════════════════╗
║   🚀 NRT Scope Visualization System - Server Started      ║
╠═══════════════════════════════════════════════════════════╣
║                                                           ║
║   🌐 Server: http://localhost:{PORT}                          ║
║                                                           ║
║   📄 Pages:                                              ║
║   • Dashboard: http://localhost:{PORT}/                  ║
║   • Commit Analysis: http://localhost:{PORT}/commit-analysis
║   • WorkItem Scope: http://localhost:{PORT}/workitem-scope   
║   • Graph Visualization: http://localhost:{PORT}/graph-visualization
║   • Reports: http://localhost:{PORT}/reports             ║
║                                                           ║
║   📊 API Endpoints:                                       ║
║   • GET /api/statistics                                 ║
║   • GET /api/commit/<commit_id>                         ║
║   • GET /api/workitem/<workitem_id>                     ║
║   • GET /api/search?q=<query>                           ║
║                                                           ║
║   Press Ctrl+C to stop the server                        ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
""")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n✋ Server stopped")
    except Exception as e:
        print(f"❌ Error: {e}")
