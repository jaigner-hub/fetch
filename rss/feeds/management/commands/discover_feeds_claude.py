"""
Management command to discover feeds using Claude AI.
"""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from feeds.claude_feed_discovery import ClaudeFeedDiscoverer
from feeds.models import Website, Feed
import json


class Command(BaseCommand):
    help = 'Discover RSS feeds for a website using Claude AI'
    
    def add_arguments(self, parser):
        parser.add_argument(
            'url',
            type=str,
            help='URL of the website to discover feeds from'
        )
        parser.add_argument(
            '--save',
            action='store_true',
            help='Save discovered feeds to database'
        )
        parser.add_argument(
            '--website-name',
            type=str,
            help='Name for the website (required if --save is used)'
        )
        
    def handle(self, *args, **options):
        url = options['url']
        save = options['save']
        website_name = options['website_name']
        
        # Ensure URL has protocol
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        # Check for API key
        if not settings.ANTHROPIC_API_KEY:
            raise CommandError('ANTHROPIC_API_KEY not found in environment variables')
        
        # If saving, ensure website name is provided
        if save and not website_name:
            raise CommandError('--website-name is required when using --save')
        
        self.stdout.write(f"Discovering feeds for: {url}")
        self.stdout.write("Using Claude AI for intelligent discovery...")
        
        try:
            discoverer = ClaudeFeedDiscoverer(url)
            results = discoverer.discover_feeds_intelligently()
            
            # Display results
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(f"Found {len(results['feeds'])} feeds:"))
            
            for feed in results['feeds']:
                self.stdout.write(
                    f"  • {feed.get('title', 'Untitled')} ({feed.get('type', 'RSS')})"
                )
                self.stdout.write(f"    URL: {feed['url']}")
                if feed.get('description'):
                    self.stdout.write(f"    Description: {feed['description'][:100]}")
                self.stdout.write('')
            
            if results.get('sitemaps'):
                self.stdout.write(self.style.SUCCESS(f"Found {len(results['sitemaps'])} sitemaps:"))
                for sitemap in results['sitemaps']:
                    self.stdout.write(f"  • {sitemap['url']}")
                self.stdout.write('')
            
            # Save to database if requested
            if save:
                self.stdout.write("Saving feeds to database...")
                
                # Get or create website
                website, created = Website.objects.get_or_create(
                    url=url,
                    defaults={'name': website_name}
                )
                
                if created:
                    self.stdout.write(self.style.SUCCESS(f"Created website: {website.name}"))
                else:
                    self.stdout.write(f"Using existing website: {website.name}")
                
                # Save feeds
                feeds_created = 0
                feeds_updated = 0
                
                for feed_info in results['feeds']:
                    feed, created = Feed.objects.get_or_create(
                        feed_url=feed_info['url'],
                        defaults={
                            'website': website,
                            'feed_type': feed_info.get('type', 'RSS'),
                            'title': feed_info.get('title', ''),
                            'description': feed_info.get('description', ''),
                            'active': True
                        }
                    )
                    
                    if created:
                        feeds_created += 1
                        self.stdout.write(self.style.SUCCESS(f"  + Created: {feed.title or feed.feed_url}"))
                    else:
                        # Update if we have better information
                        updated = False
                        if feed_info.get('title') and not feed.title:
                            feed.title = feed_info['title']
                            updated = True
                        if feed_info.get('description') and not feed.description:
                            feed.description = feed_info['description']
                            updated = True
                        
                        if updated:
                            feed.save()
                            feeds_updated += 1
                            self.stdout.write(self.style.WARNING(f"  ~ Updated: {feed.title or feed.feed_url}"))
                        else:
                            self.stdout.write(f"  - Exists: {feed.title or feed.feed_url}")
                
                self.stdout.write('')
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Summary: {feeds_created} created, {feeds_updated} updated"
                    )
                )
            
        except Exception as e:
            raise CommandError(f"Error discovering feeds: {str(e)}")