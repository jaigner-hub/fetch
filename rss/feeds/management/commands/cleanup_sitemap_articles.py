"""
Management command to clean up articles that were fetched from sitemaps.
"""
from django.core.management.base import BaseCommand
from django.db.models import Q, Count
from feeds.models import Article, Feed, FetchLog
from django.utils import timezone


class Command(BaseCommand):
    help = 'Clean up articles that were fetched from sitemaps'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )
        parser.add_argument(
            '--clean-logs',
            action='store_true',
            help='Also clean up old fetch logs',
        )
    
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        clean_logs = options['clean_logs']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes will be made'))
        
        # 1. Check for any remaining SITEMAP feeds (shouldn't be any)
        sitemap_feeds = Feed.objects.filter(feed_type='SITEMAP')
        if sitemap_feeds.exists():
            self.stdout.write(self.style.ERROR(f'Found {sitemap_feeds.count()} SITEMAP feeds still in database!'))
            if not dry_run:
                sitemap_feeds.delete()
                self.stdout.write(self.style.SUCCESS('Deleted SITEMAP feeds'))
        else:
            self.stdout.write(self.style.SUCCESS('No SITEMAP feeds found in database'))
        
        # 2. Find articles with suspicious characteristics that suggest sitemap origin
        # These are articles with no tags, no author, and minimal metadata
        suspicious_articles = Article.objects.filter(
            Q(tags__isnull=True) | Q(tags=[]),
            author='',
            raw_data__isnull=False
        )
        
        # Further filter by checking if raw_data suggests sitemap origin
        sitemap_articles = []
        for article in suspicious_articles:
            raw_data = article.raw_data
            if isinstance(raw_data, dict):
                # Sitemap articles often lack certain metadata
                if not raw_data.get('categories') and not raw_data.get('tags'):
                    # Check if URL pattern suggests it came from a sitemap
                    if '/p/' in article.url or article.url.count('/') > 5:
                        sitemap_articles.append(article.id)
        
        if sitemap_articles:
            self.stdout.write(self.style.WARNING(f'Found {len(sitemap_articles)} potential sitemap articles'))
            if not dry_run:
                deleted = Article.objects.filter(id__in=sitemap_articles).delete()
                self.stdout.write(self.style.SUCCESS(f'Deleted {deleted[0]} sitemap articles'))
        else:
            self.stdout.write(self.style.SUCCESS('No suspicious sitemap articles found'))
        
        # 3. Clean up orphaned articles (no valid feed)
        # First check if there are any
        orphaned_articles = []
        all_articles = Article.objects.select_related('feed')
        for article in all_articles:
            try:
                if not article.feed:
                    orphaned_articles.append(article.id)
            except Feed.DoesNotExist:
                orphaned_articles.append(article.id)
        
        if orphaned_articles:
            self.stdout.write(self.style.WARNING(f'Found {len(orphaned_articles)} orphaned articles'))
            if not dry_run:
                deleted = Article.objects.filter(id__in=orphaned_articles).delete()
                self.stdout.write(self.style.SUCCESS(f'Deleted {deleted[0]} orphaned articles'))
        else:
            self.stdout.write(self.style.SUCCESS('No orphaned articles found'))
        
        # 4. Clean up fetch logs for non-existent feeds
        valid_feed_ids = set(Feed.objects.values_list('id', flat=True))
        all_log_feed_ids = set(FetchLog.objects.values_list('feed_id', flat=True).distinct())
        orphaned_feed_ids = all_log_feed_ids - valid_feed_ids
        
        if orphaned_feed_ids:
            orphaned_logs = FetchLog.objects.filter(feed_id__in=orphaned_feed_ids)
            self.stdout.write(self.style.WARNING(f'Found {orphaned_logs.count()} orphaned fetch logs'))
            if not dry_run:
                deleted = orphaned_logs.delete()
                self.stdout.write(self.style.SUCCESS(f'Deleted {deleted[0]} orphaned fetch logs'))
        else:
            self.stdout.write(self.style.SUCCESS('No orphaned fetch logs found'))
        
        # 5. Optional: Clean old fetch logs
        if clean_logs:
            from datetime import timedelta
            old_date = timezone.now() - timedelta(days=30)
            old_logs = FetchLog.objects.filter(started_at__lt=old_date)
            
            if old_logs.exists():
                self.stdout.write(self.style.WARNING(f'Found {old_logs.count()} fetch logs older than 30 days'))
                if not dry_run:
                    deleted = old_logs.delete()
                    self.stdout.write(self.style.SUCCESS(f'Deleted {deleted[0]} old fetch logs'))
            else:
                self.stdout.write(self.style.SUCCESS('No old fetch logs found'))
        
        # 6. Show final statistics
        self.stdout.write('\n' + self.style.SUCCESS('=== Final Statistics ==='))
        self.stdout.write(f'Total feeds: {Feed.objects.count()}')
        self.stdout.write(f'  RSS feeds: {Feed.objects.filter(feed_type="RSS").count()}')
        self.stdout.write(f'  ATOM feeds: {Feed.objects.filter(feed_type="ATOM").count()}')
        self.stdout.write(f'Total articles: {Article.objects.count()}')
        self.stdout.write(f'Total fetch logs: {FetchLog.objects.count()}')
        
        # Check article distribution
        feed_article_counts = Article.objects.values('feed__feed_type').annotate(count=Count('id'))
        for item in feed_article_counts:
            self.stdout.write(f'  Articles from {item["feed__feed_type"]} feeds: {item["count"]}')