"""
Management command to add multiple feeds for a website at once.
Useful for sites like Hollywood Reporter that have many category-specific RSS feeds.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from feeds.models import Website, Feed
import json


class Command(BaseCommand):
    help = 'Add multiple RSS feeds for a website from a structured list'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--website-url',
            type=str,
            required=True,
            help='Base URL of the website'
        )
        parser.add_argument(
            '--website-name',
            type=str,
            required=True,
            help='Name of the website'
        )
        parser.add_argument(
            '--feeds-json',
            type=str,
            help='JSON string containing feed definitions'
        )
        parser.add_argument(
            '--feeds-file',
            type=str,
            help='Path to JSON file containing feed definitions'
        )
        parser.add_argument(
            '--validate',
            action='store_true',
            help='Validate feeds after adding them'
        )
        
    def handle(self, *args, **options):
        website_url = options['website_url']
        website_name = options['website_name']
        
        # Ensure URL has protocol
        if not website_url.startswith(('http://', 'https://')):
            website_url = 'https://' + website_url
        
        # Load feeds data
        feeds_data = None
        if options['feeds_json']:
            try:
                feeds_data = json.loads(options['feeds_json'])
            except json.JSONDecodeError as e:
                raise CommandError(f'Invalid JSON: {e}')
        elif options['feeds_file']:
            try:
                with open(options['feeds_file'], 'r') as f:
                    feeds_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                raise CommandError(f'Error reading feeds file: {e}')
        else:
            raise CommandError('Either --feeds-json or --feeds-file must be provided')
        
        if not feeds_data:
            raise CommandError('No feeds data provided')
        
        try:
            with transaction.atomic():
                # Get or create website
                website, created = Website.objects.get_or_create(
                    url=website_url,
                    defaults={'name': website_name}
                )
                
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(f'Created new website: {website.name}')
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(f'Website already exists: {website.name}')
                    )
                
                # Add feeds
                feeds_created = 0
                feeds_updated = 0
                feeds_skipped = 0
                
                for feed_info in feeds_data:
                    feed_url = feed_info.get('url')
                    if not feed_url:
                        self.stdout.write(
                            self.style.ERROR(f'Feed missing URL: {feed_info}')
                        )
                        continue
                    
                    # Ensure feed URL has protocol
                    if not feed_url.startswith(('http://', 'https://')):
                        if website_url.startswith('https://'):
                            feed_url = 'https://' + feed_url
                        else:
                            feed_url = 'http://' + feed_url
                    
                    feed_name = feed_info.get('name', '')
                    feed_type = feed_info.get('type', 'RSS').upper()
                    
                    # Validate feed type
                    if feed_type not in ['RSS', 'ATOM', 'SITEMAP']:
                        feed_type = 'RSS'
                    
                    try:
                        feed, created = Feed.objects.get_or_create(
                            feed_url=feed_url,
                            defaults={
                                'website': website,
                                'feed_type': feed_type,
                                'title': feed_name,
                                'description': f'{website_name} - {feed_name}' if feed_name else '',
                                'active': True
                            }
                        )
                        
                        if created:
                            feeds_created += 1
                            self.stdout.write(
                                self.style.SUCCESS(f'  + Added: {feed_name or feed_url}')
                            )
                        else:
                            # Update existing feed if name is provided
                            if feed_name and not feed.title:
                                feed.title = feed_name
                                feed.save()
                                feeds_updated += 1
                                self.stdout.write(
                                    self.style.WARNING(f'  ~ Updated: {feed_name}')
                                )
                            else:
                                feeds_skipped += 1
                                self.stdout.write(
                                    self.style.WARNING(f'  - Skipped (exists): {feed.title or feed_url}')
                                )
                    
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f'  ! Error adding feed {feed_url}: {e}')
                        )
                
                # Summary
                self.stdout.write('')
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Summary: {feeds_created} created, {feeds_updated} updated, {feeds_skipped} skipped'
                    )
                )
                
                # Optionally validate feeds
                if options['validate']:
                    self.stdout.write('')
                    self.stdout.write('Validating feeds...')
                    from feeds.feed_discovery import FeedDiscoverer
                    
                    discoverer = FeedDiscoverer(website_url)
                    active_feeds = website.feeds.filter(active=True)
                    
                    for feed in active_feeds:
                        validated = discoverer.validate_feed(feed.feed_url)
                        if validated:
                            self.stdout.write(
                                self.style.SUCCESS(f'  ✓ Valid: {feed.title or feed.feed_url}')
                            )
                        else:
                            self.stdout.write(
                                self.style.ERROR(f'  ✗ Invalid: {feed.title or feed.feed_url}')
                            )
                
        except Exception as e:
            raise CommandError(f'Error adding feeds: {str(e)}')