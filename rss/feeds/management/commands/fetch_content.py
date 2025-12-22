"""
Management command to fetch content from feeds.
"""
from django.core.management.base import BaseCommand, CommandError
from feeds.models import Feed, Website
from feeds.tasks import fetch_feed_content, check_all_feeds


class Command(BaseCommand):
    help = 'Fetch content from RSS feeds'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--feed-id',
            type=int,
            help='Fetch content from a specific feed ID'
        )
        parser.add_argument(
            '--website',
            type=str,
            help='Fetch content from all feeds of a website (by name or URL)'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Fetch content from all active feeds'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            help='Run fetch asynchronously using Celery'
        )
        
    def handle(self, *args, **options):
        feed_id = options.get('feed_id')
        website_filter = options.get('website')
        fetch_all = options.get('all')
        use_async = options['async']
        
        if not any([feed_id, website_filter, fetch_all]):
            raise CommandError(
                'Please specify --feed-id, --website, or --all'
            )
        
        try:
            if feed_id:
                # Fetch specific feed
                try:
                    feed = Feed.objects.get(id=feed_id)
                except Feed.DoesNotExist:
                    raise CommandError(f'Feed with ID {feed_id} not found')
                
                self.stdout.write(f'Fetching content from: {feed.feed_url}')
                
                if use_async:
                    result = fetch_feed_content.delay(feed.id)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Fetch task queued with ID: {result.id}'
                        )
                    )
                else:
                    result = fetch_feed_content(feed.id)
                    self.stdout.write(self.style.SUCCESS(result))
                    
            elif website_filter:
                # Fetch from website's feeds
                try:
                    # Try to find by name first, then by URL
                    website = Website.objects.filter(
                        name__icontains=website_filter
                    ).first()
                    
                    if not website:
                        website = Website.objects.filter(
                            url__icontains=website_filter
                        ).first()
                    
                    if not website:
                        raise CommandError(
                            f'Website matching "{website_filter}" not found'
                        )
                        
                except Website.DoesNotExist:
                    raise CommandError(
                        f'Website matching "{website_filter}" not found'
                    )
                
                feeds = website.feeds.filter(active=True)
                feed_count = feeds.count()
                
                self.stdout.write(
                    f'Fetching content from {feed_count} feeds for {website.name}'
                )
                
                for feed in feeds:
                    if use_async:
                        fetch_feed_content.delay(feed.id)
                    else:
                        result = fetch_feed_content(feed.id)
                        self.stdout.write(f'  {feed.feed_url}: {result}')
                
                if use_async:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Queued {feed_count} fetch tasks'
                        )
                    )
                    
            elif fetch_all:
                # Fetch all active feeds
                if use_async:
                    result = check_all_feeds.delay()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'All feeds check task queued with ID: {result.id}'
                        )
                    )
                else:
                    result = check_all_feeds()
                    self.stdout.write(self.style.SUCCESS(result))
                    
        except Exception as e:
            raise CommandError(f'Error fetching content: {str(e)}')