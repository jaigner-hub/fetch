#!/usr/bin/env python
"""Fix GeneratedContent entries where content is stored as JSON string"""
import os
import sys
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rss.settings')
django.setup()

from feeds.models import GeneratedContent

def fix_json_content():
    """Fix content fields that contain JSON strings instead of HTML."""
    
    fixed_count = 0
    error_count = 0
    
    # Get all GeneratedContent entries
    contents = GeneratedContent.objects.all()
    
    for content_obj in contents:
        if not content_obj.content:
            continue
            
        # Check if content starts with JSON
        if content_obj.content.strip().startswith('{'):
            try:
                # Clean the JSON string first (remove control characters)
                import re
                cleaned_json = re.sub(r'[\x00-\x1f\x7f]', ' ', content_obj.content)
                
                # Parse the JSON
                parsed = json.loads(cleaned_json)
                
                # Extract the actual content
                if isinstance(parsed, dict):
                    # Update fields from the parsed JSON
                    if 'title' in parsed and parsed['title']:
                        content_obj.title = parsed['title']
                    
                    if 'subtitle' in parsed and parsed['subtitle']:
                        content_obj.subtitle = parsed['subtitle']
                    
                    if 'content' in parsed and parsed['content']:
                        content_obj.content = parsed['content']
                    
                    if 'summary' in parsed and parsed['summary']:
                        content_obj.summary = parsed['summary']
                    
                    # Save the fixed content
                    content_obj.save()
                    fixed_count += 1
                    print(f"Fixed: {content_obj.title[:50]}")
                    
            except json.JSONDecodeError as e:
                error_count += 1
                print(f"Error parsing JSON for content {content_obj.id}: {e}")
            except Exception as e:
                error_count += 1
                print(f"Error fixing content {content_obj.id}: {e}")
    
    print(f"\nSummary:")
    print(f"Fixed {fixed_count} content entries")
    print(f"Errors: {error_count}")

if __name__ == "__main__":
    fix_json_content()