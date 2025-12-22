#!/usr/bin/env python
"""
Discover feeds for all active websites in the database.
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rss.settings')
django.setup()

from feeds.models import Website, Feed
from feeds.tasks import discover_feeds_for_website
from django.db.models import Count

def main():
    # Get statistics before
    total_feeds_before = Feed.objects.count()
    rss_before = Feed.objects.filter(feed_type='RSS').count()
    atom_before = Feed.objects.filter(feed_type='ATOM').count()
    sitemap_before = Feed.objects.filter(feed_type='SITEMAP').count()
    
    print("=== FEED DISCOVERY FOR ALL WEBSITES ===")
    print(f"Feeds before discovery: {total_feeds_before}")
    print(f"  RSS: {rss_before}, ATOM: {atom_before}, SITEMAP: {sitemap_before}\n")
    
    # Get all active websites
    websites = Website.objects.filter(active=True).order_by('name')
    total_websites = websites.count()
    
    print(f"Processing {total_websites} active websites...\n")
    
    success_count = 0
    error_count = 0
    new_feeds_total = 0
    new_sitemaps_total = 0
    
    for i, website in enumerate(websites, 1):
        print(f"[{i}/{total_websites}] {website.name}")
        print(f"  URL: {website.url}")
        
        try:
            # Count feeds before
            feeds_before = website.feeds.count()
            
            # Run discovery
            result = discover_feeds_for_website(website.id)
            
            # Count feeds after
            feeds_after = website.feeds.count()
            new_feeds = feeds_after - feeds_before
            
            # Count new sitemaps
            new_sitemaps = website.feeds.filter(feed_type='SITEMAP').count()
            
            print(f"  ✓ {result}")
            print(f"  Total feeds: {feeds_after} (+{new_feeds})")
            
            success_count += 1
            new_feeds_total += new_feeds
            if new_sitemaps > 0:
                new_sitemaps_total += new_sitemaps
                
        except Exception as e:
            print(f"  ✗ Error: {e}")
            error_count += 1
        
        print()
    
    # Get statistics after
    total_feeds_after = Feed.objects.count()
    rss_after = Feed.objects.filter(feed_type='RSS').count()
    atom_after = Feed.objects.filter(feed_type='ATOM').count()
    sitemap_after = Feed.objects.filter(feed_type='SITEMAP').count()
    
    print("=== DISCOVERY COMPLETE ===")
    print(f"Processed: {success_count} successful, {error_count} errors")
    print(f"New feeds discovered: {new_feeds_total}")
    print(f"New selective sitemaps: {new_sitemaps_total}")
    print(f"\nTotal feeds after discovery: {total_feeds_after}")
    print(f"  RSS: {rss_after} (+{rss_after - rss_before})")
    print(f"  ATOM: {atom_after} (+{atom_after - atom_before})")
    print(f"  SITEMAP: {sitemap_after} (+{sitemap_after - sitemap_before})")
    
    # Show websites with most feeds
    print("\nTop websites by feed count:")
    top_websites = Website.objects.annotate(
        feed_count=Count('feeds')
    ).order_by('-feed_count')[:10]
    
    for website in top_websites:
        rss = website.feeds.filter(feed_type='RSS').count()
        atom = website.feeds.filter(feed_type='ATOM').count()
        sitemap = website.feeds.filter(feed_type='SITEMAP').count()
        print(f"  {website.name}: {website.feed_count} feeds (RSS:{rss}, ATOM:{atom}, SITEMAP:{sitemap})")

if __name__ == '__main__':
    main()