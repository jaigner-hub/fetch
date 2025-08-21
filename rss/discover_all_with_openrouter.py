#!/usr/bin/env python
"""
Discover feeds for all active websites using OpenRouter AI.
"""
import os
import sys
import django
import time

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rss.settings')
django.setup()

from feeds.models import Website, Feed
from feeds.tasks import discover_feeds_for_website
from django.db.models import Count

def main():
    print("=== FEED DISCOVERY WITH OPENROUTER AI ===\n")
    
    # Get statistics before
    total_feeds_before = Feed.objects.count()
    rss_before = Feed.objects.filter(feed_type='RSS').count()
    atom_before = Feed.objects.filter(feed_type='ATOM').count()
    sitemap_before = Feed.objects.filter(feed_type='SITEMAP').count()
    
    print(f"Feeds before discovery:")
    print(f"  Total: {total_feeds_before}")
    print(f"  RSS: {rss_before}, ATOM: {atom_before}, SITEMAP: {sitemap_before}\n")
    
    # Get all active websites
    websites = Website.objects.filter(active=True).order_by('name')
    total_websites = websites.count()
    
    print(f"Processing {total_websites} active websites...\n")
    
    success_count = 0
    error_count = 0
    new_feeds_total = 0
    new_sitemaps_total = 0
    
    # Process in batches to avoid timeout
    batch_size = 5
    for batch_start in range(0, total_websites, batch_size):
        batch_end = min(batch_start + batch_size, total_websites)
        batch = websites[batch_start:batch_end]
        
        print(f"\n--- Batch {batch_start//batch_size + 1} ({batch_start+1}-{batch_end}) ---")
        
        for website in batch:
            print(f"\n[{batch_start + list(batch).index(website) + 1}/{total_websites}] {website.name}")
            print(f"  URL: {website.url}")
            
            try:
                # Count feeds before
                feeds_before = website.feeds.count()
                
                # Run discovery
                result = discover_feeds_for_website(website.id)
                
                # Count feeds after
                feeds_after = website.feeds.count()
                new_feeds = feeds_after - feeds_before
                
                # Count feed types
                rss = website.feeds.filter(feed_type='RSS').count()
                atom = website.feeds.filter(feed_type='ATOM').count()
                sitemaps = website.feeds.filter(feed_type='SITEMAP').count()
                
                print(f"  ✓ {result}")
                print(f"  Total feeds: {feeds_after} (+{new_feeds})")
                print(f"  RSS: {rss}, ATOM: {atom}, SITEMAP: {sitemaps}")
                
                success_count += 1
                new_feeds_total += new_feeds
                if sitemaps > 0:
                    new_sitemaps_total += sitemaps
                    
            except Exception as e:
                print(f"  ✗ Error: {e}")
                error_count += 1
            
            # Rate limiting - OpenRouter has rate limits
            time.sleep(2)  # 2 second delay between websites
        
        # Longer delay between batches
        if batch_end < total_websites:
            print(f"\nWaiting 5 seconds before next batch...")
            time.sleep(5)
    
    # Get statistics after
    print("\n" + "="*50)
    total_feeds_after = Feed.objects.count()
    rss_after = Feed.objects.filter(feed_type='RSS').count()
    atom_after = Feed.objects.filter(feed_type='ATOM').count()
    sitemap_after = Feed.objects.filter(feed_type='SITEMAP').count()
    
    print("=== DISCOVERY COMPLETE ===")
    print(f"Processed: {success_count} successful, {error_count} errors")
    print(f"New feeds discovered: {total_feeds_after - total_feeds_before}")
    print(f"\nTotal feeds after discovery: {total_feeds_after}")
    print(f"  RSS: {rss_after} (+{rss_after - rss_before})")
    print(f"  ATOM: {atom_after} (+{atom_after - atom_before})")
    print(f"  SITEMAP: {sitemap_after} (+{sitemap_after - sitemap_before})")
    
    # Show websites with most feeds
    print("\nTop 10 websites by feed count:")
    top_websites = Website.objects.annotate(
        feed_count=Count('feeds')
    ).order_by('-feed_count')[:10]
    
    for i, website in enumerate(top_websites, 1):
        rss = website.feeds.filter(feed_type='RSS').count()
        atom = website.feeds.filter(feed_type='ATOM').count()
        sitemap = website.feeds.filter(feed_type='SITEMAP').count()
        print(f"  {i}. {website.name}: {website.feed_count} feeds (RSS:{rss}, ATOM:{atom}, SITEMAP:{sitemap})")

if __name__ == '__main__':
    main()