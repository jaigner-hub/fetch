"""
Management command to fetch content from all feeds for a specific website.
"""
from django.core.management.base import BaseCommand, CommandError
from feeds.models import Website, Feed
from feeds.tasks import fetch_feed_content
import time


class Command(BaseCommand):
    help = 'Fetch content from all feeds for a specific website'
    
    def add_arguments(self, parser):
        parser.add_argument(
            'website_name',
            type=str,
            help='Name or URL of the website'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            help='Run fetches asynchronously using Celery'
        )
        parser.add_argument(
            '--delay',
            type=float,
            default=1.0,
            help='Delay in seconds between feed fetches (for rate limiting)'
        )
        parser.add_argument(
            '--only-active',
            action='store_true',
            default=True,
            help='Only fetch active feeds'
        )
        
    def handle(self, *args, **options):
        website_name = options['website_name']
        use_async = options['async']
        delay = options['delay']
        only_active = options['only_active']
        
        try:
            # Try to find website by name or URL
            try:
                website = Website.objects.get(name__icontains=website_name)
            except Website.DoesNotExist:
                website = Website.objects.get(url__icontains=website_name)
            
            self.stdout.write(
                self.style.SUCCESS(f'Found website: {website.name} ({website.url})')
            )
            
            # Get feeds
            feeds = website.feeds.all()
            if only_active:
                feeds = feeds.filter(active=True)
            
            total_feeds = feeds.count()
            
            if total_feeds == 0:
                self.stdout.write(
                    self.style.WARNING('No feeds found for this website')
                )
                return
            
            self.stdout.write(
                f'Fetching content from {total_feeds} feeds...'
            )
            
            # Fetch content from each feed
            for i, feed in enumerate(feeds, 1):
                self.stdout.write(
                    f'[{i}/{total_feeds}] Fetching: {feed.title or feed.feed_url}'
                )
                
                if use_async:
                    result = fetch_feed_content.delay(feed.id)
                    self.stdout.write(
                        self.style.SUCCESS(f'  Queued task: {result.id}')
                    )
                else:
                    try:
                        result = fetch_feed_content(feed.id)
                        self.stdout.write(
                            self.style.SUCCESS(f'  {result}')
                        )
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f'  Error: {e}')
                        )
                
                # Add delay between feeds to avoid overwhelming the server
                if i < total_feeds and delay > 0:
                    time.sleep(delay)
            
            self.stdout.write(
                self.style.SUCCESS(f'\nCompleted fetching from {total_feeds} feeds')
            )
            
            # Show summary statistics
            from feeds.models import Article
            from django.utils import timezone
            from datetime import timedelta
            
            # Get articles from the last 24 hours
            recent_cutoff = timezone.now() - timedelta(hours=24)
            recent_articles = Article.objects.filter(
                feed__website=website,
                fetched_at__gte=recent_cutoff
            ).count()
            
            total_articles = Article.objects.filter(
                feed__website=website
            ).count()
            
            self.stdout.write(
                f'\nStatistics:'
            )
            self.stdout.write(
                f'  Total articles: {total_articles}'
            )
            self.stdout.write(
                f'  Articles fetched in last 24h: {recent_articles}'
            )
            
        except Website.DoesNotExist:
            raise CommandError(f'Website not found: {website_name}')
        except Website.MultipleObjectsReturned:
            self.stdout.write(
                self.style.ERROR('Multiple websites match that name. Please be more specific.')
            )
            websites = Website.objects.filter(name__icontains=website_name)
            for w in websites:
                self.stdout.write(f'  - {w.name} ({w.url})')
        except Exception as e:
            raise CommandError(f'Error fetching feeds: {str(e)}')