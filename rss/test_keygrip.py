#!/usr/bin/env python
"""
Test script for Keygrip API integration
"""
import os
import sys
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rss.settings')
django.setup()

from feeds.models import Article
from feeds.article_analyzer import ContentGenerator

def test_keygrip_api():
    """Test direct Keygrip API call"""
    print("Testing Keygrip API integration...")
    
    # Test direct API call first
    import requests
    api_key = "zrag_4DCDDfwBj4wAMY1-kRGXk7_g2Vcpyz068JEt2iRqsVc"
    api_url = "https://stage.keygrip.ai/api/v1/query/"
    
    print("\n--- Testing Direct API Call ---")
    headers = {
        'X-API-Key': api_key,
        'Content-Type': 'application/json'
    }
    
    test_payload = {
        'query': 'What is FAL.ai?',
        'voice_prompt_id': 'product_writer',
        'use_writing_samples': False,
        'use_web_search': True
    }
    
    print(f"Sending test request to {api_url}")
    print(f"Payload: {test_payload}")
    
    try:
        response = requests.post(api_url, headers=headers, json=test_payload, timeout=30)
        print(f"Response status: {response.status_code}")
        print(f"Response: {response.text[:500] if response.text else 'No response body'}")
    except Exception as e:
        print(f"Direct API test failed: {e}")
    
    # Get some recent articles to test with
    articles = Article.objects.filter(
        content__isnull=False
    ).order_by('-published_date')[:2]
    
    if not articles:
        print("No articles found in database. Please fetch some articles first.")
        return
    
    print(f"\nFound {len(articles)} articles to use as sources:")
    for article in articles:
        print(f"  - {article.title[:80]}")
    
    # Initialize the content generator
    generator = ContentGenerator()
    
    try:
        print("\n--- Testing Keygrip Generation ---")
        result = generator.generate_with_keygrip(
            source_articles=list(articles),
            voice_prompt_id="product_writer",
            use_writing_samples=False,
            use_web_search=True
        )
        
        print("\nGeneration successful!")
        print(f"Title: {result.get('title', 'No title')}")
        print(f"Content length: {len(result.get('content', ''))} characters")
        print(f"Generation method: {result.get('generation_method', 'unknown')}")
        print(f"Voice used: {result.get('voice_prompt_id', 'unknown')}")
        
        if result.get('web_sources'):
            print(f"Web sources found: {len(result['web_sources'])}")
        
        # Show a preview of the content
        content = result.get('content', '')
        if content:
            print(f"\nContent preview (first 500 chars):")
            print("-" * 50)
            print(content[:500])
            print("-" * 50)
        
    except Exception as e:
        print(f"\nError during Keygrip generation: {e}")
        import traceback
        traceback.print_exc()
        
        print("\nFalling back to Claude generation for comparison...")
        try:
            result = generator.generate_article(
                source_articles=list(articles),
                style="news",
                target_length=800
            )
            print("Claude generation successful!")
            print(f"Title: {result.get('title', 'No title')}")
        except Exception as e2:
            print(f"Claude generation also failed: {e2}")

if __name__ == "__main__":
    test_keygrip_api()