"""
NRT SCOPE VISUALIZATION SYSTEM - Web Interface
Flask application for interactive NRT scope analysis
"""

from flask import Flask, render_template, request, jsonify
from neo4j import GraphDatabase
import json
from datetime import datetime

app = Flask(__name__)

# Neo4j Configuration
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "forvia2025"

class NRTAnalyzer:
    """Analyze NRT scopes from Neo4j"""
    
    def __init__(self):
        self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    def get_commit_impact(self, commit_id):
        """Get impact of a specific commit"""
        query = """
        MATCH (c:Commit {commit_id: $commit_id})-[mod:MODIFIES]->(ms:Microservice)
              -[impl:IMPLEMENTS]->(f:Function)
              -[cov:COVERS]-(wi:WorkItem)
        RETURN DISTINCT
            c.commit_id as commit_id,
            c.auteur as author,
            c.message as message,
            c.date as date,
            COLLECT(DISTINCT ms.name) as microservices,
            COUNT(DISTINCT f.id) as function_count,
            COLLECT(DISTINCT wi.id) as workitem_ids,
            COLLECT(DISTINCT wi.titre) as workitem_titles
        """
        
        with self.driver.session() as session:
            result = session.run(query, commit_id=commit_id)
            record = result.single()
            
            if record:
                return {
                    "commit_id": record['commit_id'],
                    "author": record['author'],
                    "message": record['message'],
                    "date": record['date'],
                    "microservices": record['microservices'],
                    "function_count": record['function_count'],
                    "workitem_count": len(record['workitem_ids']),
                    "workitems": list(zip(record['workitem_ids'], record['workitem_titles']))
                }
            return None
    
    def get_workitem_scope(self, workitem_id):
        """Get complete NRT scope for a WorkItem"""
        query = """
        MATCH (c:Commit)-[mod:MODIFIES]->(ms:Microservice)
              -[impl:IMPLEMENTS]->(f:Function)
              -[cov:COVERS]-(wi:WorkItem {id: $wid})
        RETURN DISTINCT
            COUNT(DISTINCT c.commit_id) as total_commits,
            COLLECT(DISTINCT ms.name) as microservices,
            COUNT(DISTINCT f.id) as total_functions,
            COLLECT(DISTINCT c.auteur) as authors,
            wi.titre as workitem_title,
            wi.statut as status
        """
        
        with self.driver.session() as session:
            result = session.run(query, wid=int(workitem_id))
            record = result.single()
            
            if record:
                return {
                    "workitem_id": workitem_id,
                    "workitem_title": record['workitem_title'],
                    "status": record['status'],
                    "total_commits": record['total_commits'],
                    "microservices": record['microservices'],
                    "total_functions": record['total_functions'],
                    "authors_count": len(record['authors']),
                    "authors": record['authors']
                }
            return None
    
    def get_statistics(self):
        """Get overall statistics"""
        with self.driver.session() as session:
            # Total nodes
            result = session.run("MATCH (n) RETURN count(n) as total")
            total_nodes = result.single()['total']
            
            # Total relations
            result = session.run("MATCH ()-[r]->() RETURN count(r) as total")
            total_relations = result.single()['total']
            
            # Microservices
            result = session.run("MATCH (ms:Microservice) RETURN count(ms) as total")
            total_ms = result.single()['total']
            
            # Functions
            result = session.run("MATCH (f:Function) RETURN count(f) as total")
            total_functions = result.single()['total']
            
            # WorkItems
            result = session.run("MATCH (wi:WorkItem) RETURN count(wi) as total")
            total_workitems = result.single()['total']
            
            # Commits
            result = session.run("MATCH (c:Commit) RETURN count(c) as total")
            total_commits = result.single()['total']
            
            return {
                "total_nodes": total_nodes,
                "total_relations": total_relations,
                "microservices": total_ms,
                "functions": total_functions,
                "workitems": total_workitems,
                "commits": total_commits
            }
    
    def search_workitems(self, query):
        """Search for WorkItems by title"""
        cypher_query = """
        MATCH (wi:WorkItem)
        WHERE toLower(wi.titre) CONTAINS toLower($q)
        RETURN wi.id as id, wi.titre as title, wi.statut as status
        LIMIT 10
        """
        
        with self.driver.session() as session:
            result = session.run(cypher_query, q=query)
            return [{"id": r['id'], "title": r['title'], "status": r['status']} for r in result]
    
    def close(self):
        self.driver.close()

# Initialize analyzer
analyzer = NRTAnalyzer()

# ==================== ROUTES ====================

@app.route('/')
def dashboard():
    """Main dashboard"""
    stats = analyzer.get_statistics()
    return render_template('dashboard.html', stats=stats)

@app.route('/commit-analysis')
def commit_analysis():
    """Commit impact analysis page"""
    return render_template('commit_analysis.html')

@app.route('/workitem-scope')
def workitem_scope():
    """WorkItem scope analysis page"""
    return render_template('workitem_scope.html')

@app.route('/graph-visualization')
def graph_viz():
    """Graph visualization page"""
    return render_template('graph_visualization.html')

@app.route('/reports')
def reports():
    """Reports and statistics page"""
    stats = analyzer.get_statistics()
    return render_template('reports.html', stats=stats)

# ==================== API ENDPOINTS ====================

@app.route('/api/commit/<commit_id>')
def api_commit(commit_id):
    """API: Get commit impact"""
    result = analyzer.get_commit_impact(commit_id)
    if result:
        return jsonify(result)
    return jsonify({"error": "Commit not found"}), 404

@app.route('/api/workitem/<int:workitem_id>')
def api_workitem(workitem_id):
    """API: Get WorkItem scope"""
    result = analyzer.get_workitem_scope(workitem_id)
    if result:
        return jsonify(result)
    return jsonify({"error": "WorkItem not found"}), 404

@app.route('/api/search')
def api_search():
    """API: Search WorkItems"""
    q = request.args.get('q', '')
    if q:
        results = analyzer.search_workitems(q)
        return jsonify({"results": results})
    return jsonify({"error": "No query provided"}), 400

@app.route('/api/statistics')
def api_statistics():
    """API: Get statistics"""
    stats = analyzer.get_statistics()
    return jsonify(stats)

# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    return "Page not found", 404

@app.errorhandler(500)
def server_error(error):
    return "Server error", 500

if __name__ == '__main__':
    try:
        print("\n" + "="*70)
        print("🚀 NRT SCOPE VISUALIZATION SYSTEM")
        print("="*70)
        print("\n📍 Starting Flask application on http://localhost:5000")
        print("\n🌐 Pages disponibles:")
        print("   1. Dashboard         → /")
        print("   2. Commit Analysis   → /commit-analysis")
        print("   3. WorkItem Scope    → /workitem-scope")
        print("   4. Graph Viz         → /graph-visualization")
        print("   5. Reports           → /reports")
        print("\n" + "="*70 + "\n")
        
        app.run(debug=True, host='localhost', port=5000)
    finally:
        analyzer.close()
