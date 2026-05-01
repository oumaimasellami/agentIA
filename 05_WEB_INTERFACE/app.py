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
NEO4J_PASSWORD = "neo4j2026!"

class NRTAnalyzer:
    """Analyze NRT scopes from Neo4j"""
    
    def __init__(self):
        self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    def get_commit_impact(self, commit_id):
        """Get impact of a specific commit"""
        query = """
        MATCH (c:Commit {commit_id: $commit_id})-[:MODIFIES]->(ms:Microservice)
        OPTIONAL MATCH (c)-[:TOUCHES_FUNCTION]->(f:Function)
        OPTIONAL MATCH (wi:WorkItem)-[:LINKED_TO_COMMIT]->(c)
        OPTIONAL MATCH (wi)-[:LINKED_TO_PULL_REQUEST]->(:PullRequest)-[:CONTAINS_COMMIT]->(c)
        RETURN DISTINCT
            c.commit_id as commit_id,
            c.auteur as author,
            c.message as message,
            c.date as date,
            COLLECT(DISTINCT ms.name) as microservices,
            COLLECT(DISTINCT f.id) as function_ids,
            COLLECT(DISTINCT wi.id) as workitem_ids,
            COLLECT(DISTINCT wi.titre) as workitem_titles
        """
        
        with self.driver.session() as session:
            result = session.run(query, commit_id=commit_id)
            record = result.single()
            
            if record:
                filtered_function_ids = [fid for fid in (record['function_ids'] or []) if fid]
                raw_workitem_ids = list(record['workitem_ids'] or [])
                raw_workitem_titles = list(record['workitem_titles'] or [])
                workitems = []
                if raw_workitem_ids:
                    # Keep API shape stable while removing null/duplicate pairs.
                    seen = set()
                    for index, wid in enumerate(raw_workitem_ids):
                        if wid is None:
                            continue
                        title = raw_workitem_titles[index] if index < len(raw_workitem_titles) else None
                        if wid in seen:
                            continue
                        seen.add(wid)
                        workitems.append((wid, title))

                return {
                    "commit_id": record['commit_id'],
                    "author": record['author'],
                    "message": record['message'],
                    "date": record['date'],
                    "microservices": record['microservices'],
                    "function_count": len(filtered_function_ids),
                    "workitem_count": len(workitems),
                    "workitems": workitems
                }
            return None
    
    def get_workitem_scope(self, workitem_id):
        """Get complete NRT scope for a WorkItem"""
        query = """
        MATCH (wi:WorkItem {id: $wid})
        OPTIONAL MATCH (wi)-[:LINKED_TO_PULL_REQUEST]->(pr:PullRequest)-[:CONTAINS_COMMIT]->(c_from_pr:Commit)-[:MODIFIES]->(ms_from_pr:Microservice)
        OPTIONAL MATCH (wi)-[:LINKED_TO_COMMIT]->(c_direct:Commit)-[:MODIFIES]->(ms_direct:Microservice)
        OPTIONAL MATCH (c_from_pr)-[:TOUCHES_FUNCTION]->(f_from_pr:Function)
        OPTIONAL MATCH (c_direct)-[:TOUCHES_FUNCTION]->(f_direct:Function)
        WITH wi,
             COLLECT(DISTINCT pr.pr_id) AS pr_ids,
             COLLECT(DISTINCT c_from_pr.commit_id) + COLLECT(DISTINCT c_direct.commit_id) AS commit_ids,
             COLLECT(DISTINCT ms_from_pr.name) + COLLECT(DISTINCT ms_direct.name) AS microservice_names,
             COLLECT(DISTINCT f_from_pr.id) + COLLECT(DISTINCT f_direct.id) AS function_ids,
             COLLECT(DISTINCT c_from_pr.auteur) + COLLECT(DISTINCT c_direct.auteur) AS authors
        RETURN
            wi.titre as workitem_title,
            wi.statut as status,
            wi.type as workitem_type,
            [x IN pr_ids WHERE x IS NOT NULL] as pull_request_ids,
            [x IN commit_ids WHERE x IS NOT NULL] as commit_ids,
            [x IN microservice_names WHERE x IS NOT NULL] as microservice_names,
            [x IN function_ids WHERE x IS NOT NULL] as function_ids,
            [x IN authors WHERE x IS NOT NULL] as authors
        """
        
        with self.driver.session() as session:
            result = session.run(query, wid=int(workitem_id))
            record = result.single()
            
            if record:
                pull_request_ids = sorted(set([x for x in (record['pull_request_ids'] or []) if x is not None]))
                commit_ids = sorted(set([x for x in (record['commit_ids'] or []) if x]))
                microservice_names = sorted(set([x for x in (record['microservice_names'] or []) if x]))
                function_ids = sorted(set([x for x in (record['function_ids'] or []) if x]))
                authors = sorted(set([x for x in (record['authors'] or []) if x]))

                return {
                    "workitem_id": workitem_id,
                    "workitem_title": record['workitem_title'],
                    "status": record['status'],
                    "workitem_type": record['workitem_type'],
                    "pull_request_ids": pull_request_ids,
                    "total_pull_requests": len(pull_request_ids),
                    "commit_ids": commit_ids,
                    "total_commits": len(commit_ids),
                    "microservices": microservice_names,
                    "total_functions": len(function_ids),
                    "authors_count": len(authors),
                    "authors": authors
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

            # Pull Requests
            result = session.run("MATCH (pr:PullRequest) RETURN count(pr) as total")
            total_pull_requests = result.single()['total']
            
            return {
                "total_nodes": total_nodes,
                "total_relations": total_relations,
                "microservices": total_ms,
                "functions": total_functions,
                "workitems": total_workitems,
                "commits": total_commits,
                "pull_requests": total_pull_requests
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
