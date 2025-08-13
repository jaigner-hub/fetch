"""
Management command to discover feeds for a website.
"""
from django.core.management.base import BaseCommand, CommandError
from feeds.models import Website
from feeds.tasks import discover_feeds_for_website


class Command(BaseCommand):
    help = 'Discover RSS feeds and sitemaps for a website'
    
    def add_arguments(self, parser):
        parser.add_argument(
            'url',
            type=str,
            help='URL of the website to discover feeds from'
        )
        parser.add_argument(
            '--name',
            type=str,
            default='',
            help='Name for the website'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            help='Run discovery asynchronously using Celery'
        )
        
    def handle(self, *args, **options):
        url = options['url']
        name = options['name'] or url
        use_async = options['async']
        
        # Ensure URL has protocol
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        try:
            # Get or create website
            website, created = Website.objects.get_or_create(
                url=url,
                defaults={'name': name}
            )
            
            if created:
                self.stdout.write(
                    self.style.SUCCESS(f'Created new website: {website.name}')
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f'Website already exists: {website.name}')
                )
            
            # Discover feeds
            if use_async:
                result = discover_feeds_for_website.delay(website.id)
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Feed discovery task queued with ID: {result.id}'
                    )
                )
            else:
                # Run synchronously
                result = discover_feeds_for_website(website.id)
                self.stdout.write(
                    self.style.SUCCESS(result)
                )
                
                # Display discovered feeds
                feeds = website.feeds.all()
                if feeds:
                    self.stdout.write('\nDiscovered feeds:')
                    for feed in feeds:
                        self.stdout.write(
                            f'  - {feed.feed_type}: {feed.feed_url}'
                        )
                else:
                    self.stdout.write('No feeds found.')
                    
        except Exception as e:
            raise CommandError(f'Error discovering feeds: {str(e)}')