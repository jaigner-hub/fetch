#!/usr/bin/env python
"""
Test script to demonstrate the article analysis and content generation features.
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rss.settings')
django.setup()

from feeds.models import Article, ArticleAnalysis, GeneratedContent
from feeds.article_analyzer import ArticleAnalyzer
from datetime import timedelta
from django.utils import timezone

def test_similarity_detection():
    """Test finding similar articles without needing Claude API."""
    print("Testing Article Similarity Detection")
    print("=" * 50)
    
    # Get recent articles
    recent_date = timezone.now() - timedelta(days=7)
    articles = Article.objects.filter(
        fetched_at__gte=recent_date
    ).order_by('-published_date')[:5]
    
    if not articles:
        print("No recent articles found. Please fetch some content first.")
        return
    
    analyzer = ArticleAnalyzer()
    
    for article in articles[:2]:
        print(f"\nAnalyzing: {article.title[:60]}...")
        print(f"URL: {article.url}")
        
        # Find similar articles
        similar = analyzer.find_similar_articles(article, threshold=0.5)
        
        if similar:
            print(f"Found {len(similar)} similar articles:")
            for similar_article, score in similar[:3]:
                print(f"  - {similar_article.title[:50]} (similarity: {score:.2%})")
        else:
            print("  No similar articles found")
        
        # Check for duplicates
        duplicate = analyzer.check_duplicate_content(article)
        if duplicate:
            print(f"  ⚠ Potential duplicate of: {duplicate.title[:50]}")

def test_api_endpoints():
    """Show available API endpoints."""
    print("\n\nAvailable API Endpoints")
    print("=" * 50)
    
    endpoints = [
        {
            "method": "POST",
            "url": "/feeds/api/articles/{article_id}/analyze/",
            "description": "Analyze a single article for summary and topics"
        },
        {
            "method": "GET",
            "url": "/feeds/api/articles/{article_id}/analysis/",
            "description": "Get analysis results for an article"
        },
        {
            "method": "POST",
            "url": "/feeds/api/articles/find-similar/",
            "description": "Find articles similar to given text or article"
        },
        {
            "method": "POST",
            "url": "/feeds/api/articles/batch-analyze/",
            "description": "Analyze multiple articles in batch"
        },
        {
            "method": "POST",
            "url": "/feeds/api/content/generate/",
            "description": "Generate new content from source articles"
        },
        {
            "method": "GET",
            "url": "/feeds/api/content/{content_id}/",
            "description": "Get a generated content piece"
        },
        {
            "method": "GET",
            "url": "/feeds/api/content/",
            "description": "List all generated content"
        }
    ]
    
    for endpoint in endpoints:
        print(f"\n{endpoint['method']} {endpoint['url']}")
        print(f"  {endpoint['description']}")

def test_management_command():
    """Show how to use the management command."""
    print("\n\nManagement Command Usage")
    print("=" * 50)
    
    commands = [
        {
            "command": "python manage.py analyze_articles --days 1 --limit 10",
            "description": "Analyze articles from the last day"
        },
        {
            "command": "python manage.py analyze_articles --website 'Hollywood Reporter' --find-duplicates",
            "description": "Analyze articles from a specific website and find duplicates"
        },
        {
            "command": "python manage.py analyze_articles --days 7 --generate --style news",
            "description": "Analyze recent articles and generate news content"
        },
        {
            "command": "python manage.py analyze_articles --article-id 123",
            "description": "Analyze a specific article by ID"
        }
    ]
    
    for cmd in commands:
        print(f"\n{cmd['command']}")
        print(f"  {cmd['description']}")

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("RSS AGGREGATOR - ARTICLE ANALYSIS & CONTENT GENERATION")
    print("=" * 60)
    
    test_similarity_detection()
    test_api_endpoints()
    test_management_command()
    
    print("\n" + "=" * 60)
    print("Note: Full AI-powered analysis requires setting ANTHROPIC_API_KEY")
    print("in your environment or Django settings.")
    print("=" * 60)