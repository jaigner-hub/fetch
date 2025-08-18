#!/usr/bin/env python
"""Fix ArticleAnalysis entries where ai_summary is stored as JSON string"""
import os
import sys
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rss.settings')
django.setup()

from feeds.models import ArticleAnalysis

def fix_json_summaries():
    """Fix ai_summary fields that contain JSON strings instead of plain text."""
    
    fixed_count = 0
    error_count = 0
    
    # Get all ArticleAnalysis entries
    analyses = ArticleAnalysis.objects.all()
    
    for analysis in analyses:
        if not analysis.ai_summary:
            continue
            
        # Check if summary starts with JSON
        if analysis.ai_summary.strip().startswith('{'):
            try:
                # Try to extract summary using regex for truncated JSON
                import re
                
                # Look for "summary": "..." pattern
                match = re.search(r'"summary"\s*:\s*"([^"]*)', analysis.ai_summary)
                if match:
                    summary = match.group(1)
                    # Unescape JSON string escapes
                    summary = summary.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                    
                    if summary:
                        analysis.ai_summary = summary
                        analysis.save()
                        fixed_count += 1
                        print(f"Fixed analysis for: {analysis.article.title[:50]}")
                        continue
                
                # If regex didn't work, try parsing as JSON
                parsed = json.loads(analysis.ai_summary)
                
                # Extract the actual summary
                if isinstance(parsed, dict):
                    # Try different possible keys
                    summary = parsed.get('summary', parsed.get('text', parsed.get('content', '')))
                    
                    if summary:
                        analysis.ai_summary = summary
                        analysis.save()
                        fixed_count += 1
                        print(f"Fixed analysis for: {analysis.article.title[:50]}")
                    
            except json.JSONDecodeError as e:
                error_count += 1
                print(f"Error parsing JSON for analysis {analysis.id}: {e}")
            except Exception as e:
                error_count += 1
                print(f"Error fixing analysis {analysis.id}: {e}")
    
    print(f"\nSummary:")
    print(f"Fixed {fixed_count} analysis entries")
    print(f"Errors: {error_count}")

if __name__ == "__main__":
    fix_json_summaries()