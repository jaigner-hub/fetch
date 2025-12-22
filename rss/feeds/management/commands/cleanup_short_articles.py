"""
Clean up short articles that don't meet minimum content requirements.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from feeds.models import Article
from bs4 import BeautifulSoup
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Remove articles with insufficient content'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--min-words',
            type=int,
            default=200,
            help='Minimum word count required (default: 200)'
        )
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Only check articles from last N days (default: 7)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )
        parser.add_argument(
            '--domain',
            type=str,
            help='Only check articles from specific domain'
        )
    
    def handle(self, *args, **options):
        min_words = options['min_words']
        days = options['days']
        dry_run = options['dry_run']
        domain_filter = options.get('domain')
        
        self.stdout.write(f"Checking articles for short content (min {min_words} words)...")
        
        # Get recent articles
        since = timezone.now() - timedelta(days=days)
        articles = Article.objects.filter(fetched_at__gte=since)
        
        if domain_filter:
            articles = articles.filter(url__icontains=domain_filter)
            self.stdout.write(f"Filtering for domain: {domain_filter}")
        
        total_checked = 0
        short_articles = []
        
        for article in articles:
            total_checked += 1
            
            # Extract text content and count words
            word_count = 0
            if article.content:
                soup = BeautifulSoup(article.content, 'html.parser')
                text = soup.get_text(strip=True)
                word_count = len(text.split())
            
            if word_count < min_words:
                domain = article.url.split('/')[2] if '/' in article.url else 'unknown'
                short_articles.append({
                    'article': article,
                    'word_count': word_count,
                    'domain': domain
                })
                
                if total_checked % 100 == 0:
                    self.stdout.write(f"Checked {total_checked} articles...")
        
        self.stdout.write(f"\nFound {len(short_articles)} short articles out of {total_checked} checked")
        
        # Group by domain for reporting
        by_domain = {}
        for item in short_articles:
            domain = item['domain']
            if domain not in by_domain:
                by_domain[domain] = []
            by_domain[domain].append(item)
        
        # Report findings
        self.stdout.write("\nShort articles by domain:")
        for domain, items in sorted(by_domain.items(), key=lambda x: -len(x[1])):
            self.stdout.write(f"  {domain}: {len(items)} articles")
            # Show a few examples
            for item in items[:3]:
                article = item['article']
                self.stdout.write(f"    - {item['word_count']} words: {article.title[:60]}...")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\nDRY RUN - No articles deleted"))
        else:
            # Delete short articles
            deleted_count = 0
            for item in short_articles:
                article = item['article']
                self.stdout.write(f"Deleting: {article.title[:60]}... ({item['word_count']} words)")
                article.delete()
                deleted_count += 1
            
            self.stdout.write(self.style.SUCCESS(f"\nDeleted {deleted_count} short articles"))