#!/usr/bin/env python
"""
Fetch content from newly discovered feeds.
"""
import os
import sys
import django
import time
from datetime import timedelta

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rss.settings')
django.setup()

from feeds.models import Feed, Article
from feeds.tasks import fetch_feed_content, fetch_selective_sitemap_content
from django.utils import timezone

def main():
    print("=== FETCHING CONTENT FROM NEW FEEDS ===\n")
    
    # Get feeds created in the last hour that have no articles
    recent_feeds = Feed.objects.filter(
        created_at__gte=timezone.now() - timedelta(hours=2)
    ).exclude(
        articles__isnull=False
    ).distinct().order_by('website__name', 'feed_type')
    
    total_feeds = recent_feeds.count()
    print(f"Found {total_feeds} new feeds without content\n")
    
    if total_feeds == 0:
        print("No new feeds to fetch content from.")
        return
    
    success_count = 0
    error_count = 0
    articles_fetched = 0
    
    # Process feeds by type
    rss_feeds = recent_feeds.filter(feed_type__in=['RSS', 'ATOM'])
    sitemap_feeds = recent_feeds.filter(feed_type='SITEMAP')
    
    print(f"RSS/ATOM feeds: {rss_feeds.count()}")
    print(f"Sitemap feeds: {sitemap_feeds.count()}\n")
    
    # Process RSS/ATOM feeds
    if rss_feeds.exists():
        print("--- Fetching RSS/ATOM Feeds ---")
        for i, feed in enumerate(rss_feeds[:20], 1):  # Limit to 20 for now
            print(f"\n[{i}/{min(20, rss_feeds.count())}] {feed.website.name}: {feed.title or 'No title'}")
            print(f"  URL: {feed.feed_url[:80]}..." if len(feed.feed_url) > 80 else f"  URL: {feed.feed_url}")
            
            try:
                # Fetch content
                result = fetch_feed_content(feed.id)
                
                # Count new articles
                new_articles = feed.articles.count()
                articles_fetched += new_articles
                
                print(f"  ✓ Fetched {new_articles} articles")
                success_count += 1
                
            except Exception as e:
                print(f"  ✗ Error: {e}")
                error_count += 1
            
            # Rate limiting
            time.sleep(1)
    
    # Process Sitemap feeds (with 48-hour filter)
    if sitemap_feeds.exists():
        print("\n--- Fetching Selective Sitemap Content (48-hour window) ---")
        for i, feed in enumerate(sitemap_feeds[:10], 1):  # Limit to 10 sitemaps
            print(f"\n[{i}/{min(10, sitemap_feeds.count())}] {feed.website.name}: {feed.title or 'Sitemap'}")
            print(f"  URL: {feed.feed_url[:80]}..." if len(feed.feed_url) > 80 else f"  URL: {feed.feed_url}")
            
            try:
                # Fetch selective content
                result = fetch_selective_sitemap_content(feed.id)
                
                # Count new articles
                new_articles = feed.articles.count()
                articles_fetched += new_articles
                
                print(f"  ✓ Fetched {new_articles} recent articles (48-hour window)")
                success_count += 1
                
            except Exception as e:
                print(f"  ✗ Error: {e}")
                error_count += 1
            
            # Rate limiting for sitemaps
            time.sleep(2)
    
    # Final statistics
    print("\n" + "="*50)
    print("=== FETCH COMPLETE ===")
    print(f"Feeds processed: {success_count} successful, {error_count} errors")
    print(f"Total articles fetched: {articles_fetched}")
    
    # Show overall statistics
    total_articles = Article.objects.count()
    recent_articles = Article.objects.filter(
        fetched_at__gte=timezone.now() - timedelta(hours=24)
    ).count()
    
    print(f"\nDatabase statistics:")
    print(f"  Total articles: {total_articles}")
    print(f"  Articles in last 24 hours: {recent_articles}")

if __name__ == '__main__':
    main()