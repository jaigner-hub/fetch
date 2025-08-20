"""
Management command to manually trigger content fetching for websites.
"""
from django.core.management.base import BaseCommand
from feeds.models import Website
from feeds.tasks import fetch_all_website_content, fetch_feed_content


class Command(BaseCommand):
    help = 'Manually trigger content fetching for websites'

    def add_arguments(self, parser):
        parser.add_argument(
            '--website',
            type=str,
            help='Name of the website to fetch (partial match supported)'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Fetch from all active websites'
        )
        parser.add_argument(
            '--list',
            action='store_true',
            help='List all available websites'
        )

    def handle(self, *args, **options):
        if options['list']:
            self.list_websites()
            return

        if options['all']:
            self.fetch_all_websites()
        elif options['website']:
            self.fetch_website(options['website'])
        else:
            self.stdout.write(self.style.ERROR('Please specify --website NAME or --all'))
            self.stdout.write('\nUsage examples:')
            self.stdout.write('  python manage.py manual_fetch --website "World of Reel"')
            self.stdout.write('  python manage.py manual_fetch --website hollywood')
            self.stdout.write('  python manage.py manual_fetch --all')
            self.stdout.write('  python manage.py manual_fetch --list')

    def list_websites(self):
        """List all available websites."""
        websites = Website.objects.filter(active=True).order_by('name')
        
        self.stdout.write(self.style.SUCCESS(f'\nActive Websites ({websites.count()}):'))
        self.stdout.write('-' * 50)
        
        for website in websites:
            feed_count = website.feeds.filter(active=True).count()
            self.stdout.write(f'{website.name:30} ({feed_count} active feeds)')

    def fetch_website(self, name_partial):
        """Fetch content from a specific website."""
        websites = Website.objects.filter(
            name__icontains=name_partial,
            active=True
        )
        
        if not websites.exists():
            self.stdout.write(self.style.ERROR(f'No active website found matching "{name_partial}"'))
            self.stdout.write('Use --list to see available websites')
            return
        
        if websites.count() > 1:
            self.stdout.write(self.style.WARNING(f'Found {websites.count()} websites matching "{name_partial}":'))
            for w in websites:
                self.stdout.write(f'  - {w.name}')
            self.stdout.write('Please be more specific')
            return
        
        website = websites.first()
        self.stdout.write(self.style.SUCCESS(f'Fetching content from: {website.name}'))
        
        # Get RSS/Atom feeds
        rss_feeds = website.feeds.filter(active=True, feed_type__in=['RSS', 'ATOM'])
        
        if rss_feeds.exists():
            self.stdout.write(f'Processing {rss_feeds.count()} RSS/Atom feeds...')
            
            for i, feed in enumerate(rss_feeds, 1):
                self.stdout.write(f'  [{i}/{rss_feeds.count()}] {feed.title or feed.feed_url[:50]}')
                
                # Call the task synchronously for immediate feedback
                try:
                    from feeds.tasks import fetch_feed_content
                    result = fetch_feed_content(feed.id)
                    self.stdout.write(f'    ✓ {result}')
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'    ✗ Error: {e}'))
        else:
            self.stdout.write('No active RSS/Atom feeds found')
        
        # Note about sitemaps
        sitemap_count = website.feeds.filter(active=True, feed_type='SITEMAP').count()
        if sitemap_count > 0:
            self.stdout.write(f'\nNote: {sitemap_count} sitemaps available but not fetched (use scheduled fetch for sitemaps)')

    def fetch_all_websites(self):
        """Fetch content from all active websites."""
        websites = Website.objects.filter(active=True, auto_fetch_enabled=True)
        
        if not websites.exists():
            self.stdout.write(self.style.ERROR('No active websites with auto-fetch enabled'))
            return
        
        self.stdout.write(self.style.SUCCESS(f'Fetching from {websites.count()} websites...'))
        
        for website in websites:
            self.stdout.write(f'\n{website.name}:')
            
            try:
                result = fetch_all_website_content(website.id)
                self.stdout.write(self.style.SUCCESS(f'  ✓ {result}'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Error: {e}'))