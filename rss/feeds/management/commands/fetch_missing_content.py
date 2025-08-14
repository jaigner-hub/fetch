"""
Management command to fetch missing content for articles from sitemaps.
"""
from django.core.management.base import BaseCommand
from django.db.models import Q
from feeds.models import Article, Feed
from feeds.content_fetcher import ContentFetcher
import time
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Fetch missing content for articles that were added from sitemaps'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Maximum number of articles to process'
        )
        parser.add_argument(
            '--delay',
            type=float,
            default=1.0,
            help='Delay between requests in seconds'
        )
        parser.add_argument(
            '--website',
            type=str,
            help='Only process articles from this website (by name)'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        delay = options['delay']
        website_filter = options.get('website')
        
        # Find articles without content (mostly from sitemaps)
        query = Article.objects.filter(
            Q(content__isnull=True) | Q(content='')
        )
        
        if website_filter:
            query = query.filter(feed__website__name__icontains=website_filter)
            self.stdout.write(f"Filtering for website: {website_filter}")
        
        # Focus on sitemap articles first
        sitemap_articles = query.filter(feed__feed_type='SITEMAP')[:limit]
        
        total = sitemap_articles.count()
        self.stdout.write(f"Found {total} articles without content from sitemaps")
        
        if total == 0:
            # If no sitemap articles, check RSS/ATOM articles
            other_articles = query.exclude(feed__feed_type='SITEMAP')[:limit]
            total = other_articles.count()
            if total > 0:
                sitemap_articles = other_articles
                self.stdout.write(f"Found {total} articles without content from RSS/ATOM feeds")
            else:
                self.stdout.write("No articles found without content")
                return
        
        fetcher = ContentFetcher(rate_limit_delay=delay)
        
        success_count = 0
        failed_count = 0
        
        for i, article in enumerate(sitemap_articles, 1):
            self.stdout.write(f"\n[{i}/{total}] Processing: {article.url}")
            
            try:
                # Fetch content
                content = fetcher.fetch_article_content(article.url)
                
                if content:
                    article.content = content
                    
                    # Try to improve the title if it's just from the URL
                    if article.title == article.url or '/' in article.title:
                        # Extract better title from URL
                        title_slug = article.url.rstrip('/').split('/')[-1]
                        if title_slug:
                            article.title = title_slug.replace('-', ' ').title()[:500]
                    
                    article.save()
                    success_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(f"  ✓ Fetched {len(content)} characters")
                    )
                else:
                    failed_count += 1
                    self.stdout.write(
                        self.style.WARNING(f"  ✗ Could not extract content")
                    )
                    
            except Exception as e:
                failed_count += 1
                self.stdout.write(
                    self.style.ERROR(f"  ✗ Error: {str(e)}")
                )
            
            # Show progress
            if i % 10 == 0:
                self.stdout.write(
                    f"\nProgress: {i}/{total} processed, "
                    f"{success_count} successful, {failed_count} failed"
                )
        
        # Final summary
        self.stdout.write("\n" + "="*60)
        self.stdout.write(
            self.style.SUCCESS(
                f"\nCompleted! Processed {total} articles:\n"
                f"  - Successful: {success_count}\n"
                f"  - Failed: {failed_count}"
            )
        )
        
        # Show remaining count
        remaining = Article.objects.filter(
            Q(content__isnull=True) | Q(content='')
        ).count()
        if remaining > 0:
            self.stdout.write(
                f"\nRemaining articles without content: {remaining}"
            )