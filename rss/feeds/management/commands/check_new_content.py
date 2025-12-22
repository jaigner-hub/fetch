"""
Management command to check for new content in feeds.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from feeds.models import Feed, Article, Website


class Command(BaseCommand):
    help = 'Check and display statistics about new content in feeds'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=24,
            help='Check for content from the last N hours (default: 24)'
        )
        parser.add_argument(
            '--website',
            type=str,
            help='Filter by website name or URL'
        )
        
    def handle(self, *args, **options):
        hours = options['hours']
        website_filter = options.get('website')
        
        since = timezone.now() - timedelta(hours=hours)
        
        # Build query
        articles_query = Article.objects.filter(fetched_at__gte=since)
        
        if website_filter:
            # Filter by website
            websites = Website.objects.filter(
                name__icontains=website_filter
            ) | Website.objects.filter(
                url__icontains=website_filter
            )
            
            if not websites.exists():
                self.stdout.write(
                    self.style.WARNING(
                        f'No websites matching "{website_filter}" found'
                    )
                )
                return
            
            feeds = Feed.objects.filter(website__in=websites)
            articles_query = articles_query.filter(feed__in=feeds)
        
        # Get statistics
        new_articles = articles_query.order_by('-fetched_at')
        total_count = new_articles.count()
        
        if total_count == 0:
            self.stdout.write(
                self.style.WARNING(
                    f'No new content found in the last {hours} hours'
                )
            )
            return
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\nFound {total_count} new articles in the last {hours} hours:'
            )
        )
        
        # Group by feed
        feeds_with_content = {}
        for article in new_articles:
            feed_key = f"{article.feed.website.name} - {article.feed.feed_type}"
            if feed_key not in feeds_with_content:
                feeds_with_content[feed_key] = []
            feeds_with_content[feed_key].append(article)
        
        # Display results
        for feed_name, articles in feeds_with_content.items():
            self.stdout.write(f'\n{feed_name} ({len(articles)} articles):')
            
            # Show first 5 articles from each feed
            for article in articles[:5]:
                published = article.published_date or article.fetched_at
                self.stdout.write(
                    f'  - [{published.strftime("%Y-%m-%d %H:%M")}] {article.title[:80]}'
                )
            
            if len(articles) > 5:
                self.stdout.write(f'  ... and {len(articles) - 5} more')
        
        # Summary statistics
        self.stdout.write('\n' + '=' * 50)
        self.stdout.write('Summary:')
        self.stdout.write(f'  Total new articles: {total_count}')
        self.stdout.write(f'  Feeds with new content: {len(feeds_with_content)}')
        
        # Check feeds that haven't been updated
        all_feeds = Feed.objects.filter(active=True)
        stale_feeds = all_feeds.filter(
            last_checked__lt=since
        ) | all_feeds.filter(last_checked__isnull=True)
        
        if stale_feeds.exists():
            self.stdout.write(
                self.style.WARNING(
                    f'\n{stale_feeds.count()} feeds haven\'t been checked recently:'
                )
            )
            for feed in stale_feeds[:10]:
                last_check = 'Never' if not feed.last_checked else feed.last_checked.strftime("%Y-%m-%d %H:%M")
                self.stdout.write(
                    f'  - {feed.website.name}: Last checked {last_check}'
                )