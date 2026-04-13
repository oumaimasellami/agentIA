"""
Symbolic AI Engine - NRT Scope Generation
Generates automatic NRT scope for Azure DevOps based on dependency chain analysis
From: Commits → Microservices → Functions → WorkItems
"""

import json
from neo4j import GraphDatabase
from datetime import datetime
from collections import defaultdict

class SymbolicAIEngine:
    def __init__(self, uri="bolt://localhost:7687", user="neo4j", password="forvia2025"):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        
    def get_nrt_scope(self, workitem_id, limit=None):
        """
        Generate NRT scope for a given WorkItem by analyzing the complete dependency chain:
        Commit → Microservice → Function → WorkItem
        
        Note: Get ALL microservices (not limited) to ensure complete coverage
        """
        # First query: Get ALL commits and relationships (no limit on results)
        query = """
        MATCH (c:Commit)-[mod:MODIFIES]->(ms:Microservice)
              -[impl:IMPLEMENTS]->(f:Function)
              -[cov:COVERS]-(wi:WorkItem {id: $wid})
        RETURN DISTINCT
            c.commit_id as commit_id,
            c.auteur as author,
            c.remail as email,
            c.message as commit_message,
            c.date as commit_date,
            ms.name as microservice,
            f.id as function_id,
            wi.id as workitem_id,
            wi.titre as workitem_title
        """
        
        with self.driver.session() as session:
            results = session.run(query, wid=int(workitem_id))
            records = [dict(record) for record in results]
            
            # Optional: Limit commits for performance, but keep all microservices
            if limit and len(records) > limit:
                # Sort by date and keep the most recent ones
                records = sorted(records, key=lambda x: x.get('commit_date', ''), reverse=True)[:limit]
            
        return self._process_dependency_chain(records, workitem_id)
    
    def _process_dependency_chain(self, records, workitem_id):
        """
        Process dependency chain records and generate structured NRT scope
        """
        if not records:
            return {
                "workitem_id": workitem_id,
                "status": "ERROR",
                "message": "No dependency chain found for this WorkItem",
                "scope": []
            }
        
        # Aggregate data
        commits = set()
        microservices = set()
        functions = set()
        authors = set()
        commit_details = {}
        
        for record in records:
            if record['commit_id']:
                commits.add(record['commit_id'])
                commit_details[record['commit_id']] = {
                    "id": record['commit_id'],
                    "author": record['author'],
                    "email": record['email'],
                    "message": record['commit_message'],
                    "date": record['commit_date']
                }
            if record['microservice']:
                microservices.add(record['microservice'])
            if record['function_id']:
                functions.add(record['function_id'])
            if record['author']:
                authors.add(record['author'])
        
        # Build NRT Scope
        scope = {
            "workitem_id": workitem_id,
            "workitem_title": records[0]['workitem_title'] if records else "Unknown",
            "generated_at": datetime.now().isoformat(),
            "impact_analysis": {
                "total_commits": len(commits),
                "total_microservices": len(microservices),
                "total_functions": len(functions),
                "authors_involved": list(authors),
                "commits": list(commit_details.values()),
                "microservices": sorted(list(microservices)),
                "functions": sorted(list(functions))
            },
            "nrt_scope_details": {
                "affected_areas": sorted(list(microservices)),
                "dependencies": {
                    "microservices": sorted(list(microservices)),
                    "functions": sorted(list(functions))
                },
                "related_commits": list(commit_details.values()),
                "scope_summary": f"WorkItem {workitem_id} ({records[0]['workitem_title']}) is affected by {len(commits)} commits across {len(microservices)} microservices"
            }
        }
        
        return scope
    
    def export_scope_json(self, workitem_id, output_file=None):
        """
        Export NRT scope to JSON file
        """
        scope = self.get_nrt_scope(workitem_id)
        
        if output_file is None:
            output_file = f"scope_nrt_{workitem_id}.json"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(scope, f, indent=2, ensure_ascii=False)
        
        print(f"✅ NRT Scope exported to: {output_file}")
        return output_file
    
    def analyze_all_workitems(self):
        """
        Generate NRT scope for all WorkItems that have COVERS relationships
        """
        query = """
        MATCH (wi:WorkItem)<-[cov:COVERS]-(f:Function)
        RETURN DISTINCT wi.id as workitem_id
        ORDER BY wi.id
        """
        
        with self.driver.session() as session:
            results = session.run(query)
            workitem_ids = [record['workitem_id'] for record in results]
        
        print(f"Found {len(workitem_ids)} WorkItems with dependencies")
        return workitem_ids
    
    def close(self):
        """Close database connection"""
        self.driver.close()


def main():
    """Main execution"""
    print("="*70)
    print("SYMBOLIC AI ENGINE - NRT SCOPE GENERATION")
    print("="*70)
    
    # Initialize engine
    engine = SymbolicAIEngine()
    
    try:
        # Test with WorkItem 127835 (Page Builder)
        workitem_id = 127835
        print(f"\n📊 Analyzing WorkItem {workitem_id}...")
        print("-"*70)
        
        # Generate scope
        scope = engine.get_nrt_scope(workitem_id)
        
        # Pretty print results
        print(json.dumps(scope, indent=2, ensure_ascii=False))
        print("\n" + "="*70)
        
        # Export to file
        engine.export_scope_json(workitem_id)
        
        print(f"\n✅ NRT Scope generation completed successfully!")
        print(f"   WorkItem: {scope['workitem_title']}")
        print(f"   Affected Microservices: {scope['impact_analysis']['total_microservices']}")
        print(f"   Related Commits: {scope['impact_analysis']['total_commits']}")
        print(f"   Functions Involved: {scope['impact_analysis']['total_functions']}")
        
    except Exception as e:
        print(f"❌ Error during NRT scope generation: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        engine.close()


if __name__ == "__main__":
    main()
