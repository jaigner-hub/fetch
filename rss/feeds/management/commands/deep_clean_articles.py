"""
Deep clean articles with quality issues - empty titles, minimal content, etc.
"""
from django.core.management.base import BaseCommand
from django.db.models import Q
from feeds.models import Article
from bs4 import BeautifulSoup
import re
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Deep clean articles with quality issues'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )
        parser.add_argument(
            '--min-words',
            type=int,
            default=100,
            help='Minimum word count (default: 100)'
        )
        parser.add_argument(
            '--min-title-length',
            type=int,
            default=10,
            help='Minimum title length in characters (default: 10)'
        )
    
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        min_words = options['min_words']
        min_title_length = options['min_title_length']
        
        self.stdout.write("Starting deep clean of articles...")
        
        articles_to_delete = []
        reasons = {
            'no_title': [],
            'bad_title': [],
            'no_content': [],
            'minimal_content': [],
            'listing_page': [],
            'error_page': []
        }
        
        # Get all articles
        all_articles = Article.objects.all()
        total_count = all_articles.count()
        
        self.stdout.write(f"Checking {total_count} articles...")
        
        batch_size = 1000
        for i in range(0, total_count, batch_size):
            batch = all_articles[i:i+batch_size]
            
            for article in batch:
                delete_reason = None
                
                # Check title issues
                if not article.title or article.title.strip() == '':
                    delete_reason = 'no_title'
                elif len(article.title.strip()) < min_title_length:
                    delete_reason = 'bad_title'
                elif article.title.strip() == '...':
                    delete_reason = 'bad_title'
                elif '|  ' in article.title:  # Double space often indicates parsing error
                    delete_reason = 'bad_title'
                elif article.title.count('|') > 3:  # Too many pipes often indicates bad parsing
                    delete_reason = 'bad_title'
                
                # Check content issues
                if not delete_reason:
                    if not article.content or article.content.strip() == '':
                        delete_reason = 'no_content'
                    else:
                        # Extract text and count words
                        try:
                            soup = BeautifulSoup(article.content, 'html.parser')
                            text = soup.get_text(strip=True)
                            word_count = len(text.split())
                            
                            if word_count < min_words:
                                delete_reason = 'minimal_content'
                            
                            # Check for common non-article patterns
                            text_lower = text.lower()
                            if not delete_reason:
                                # Listing page patterns
                                if ('where to stream' in text_lower and word_count < 50) or \
                                   ('where to watch' in text_lower and word_count < 50) or \
                                   (text_lower.strip().endswith('| where to stream and watch |') and word_count < 50):
                                    delete_reason = 'listing_page'
                                
                                # Error page patterns
                                elif '404' in text or 'page not found' in text_lower or \
                                     'error' in text_lower and word_count < 100:
                                    delete_reason = 'error_page'
                        except Exception as e:
                            logger.error(f"Error processing content for {article.url}: {e}")
                            delete_reason = 'no_content'
                
                # Check URL patterns that indicate non-articles
                if not delete_reason and article.url:
                    url_lower = article.url.lower()
                    # Common non-article URL patterns
                    if '/show/' in url_lower or '/movie/' in url_lower or \
                       '/watch/' in url_lower or '/stream/' in url_lower:
                        # Double-check with content length
                        try:
                            if article.content:
                                soup = BeautifulSoup(article.content, 'html.parser')
                                text = soup.get_text(strip=True)
                                if len(text.split()) < 100:
                                    delete_reason = 'listing_page'
                        except:
                            pass
                
                if delete_reason:
                    articles_to_delete.append(article)
                    reasons[delete_reason].append(article)
            
            if (i + batch_size) % 5000 == 0:
                self.stdout.write(f"Processed {min(i + batch_size, total_count)} articles...")
        
        # Report findings
        self.stdout.write("\n" + "="*50)
        self.stdout.write(f"DEEP CLEAN RESULTS")
        self.stdout.write("="*50)
        self.stdout.write(f"Total articles checked: {total_count}")
        self.stdout.write(f"Articles to delete: {len(articles_to_delete)}")
        self.stdout.write("\nBreakdown by issue:")
        
        for reason, articles in reasons.items():
            if articles:
                self.stdout.write(f"\n{reason.replace('_', ' ').title()}: {len(articles)} articles")
                # Show a few examples
                for article in articles[:3]:
                    domain = article.url.split('/')[2] if '/' in article.url else 'unknown'
                    title_preview = (article.title or '(NO TITLE)')[:50]
                    self.stdout.write(f"  - {domain}: {title_preview}...")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\nDRY RUN - No articles deleted"))
        else:
            # Delete the articles
            self.stdout.write("\nDeleting articles...")
            deleted_count = 0
            
            for article in articles_to_delete:
                try:
                    article.delete()
                    deleted_count += 1
                    
                    if deleted_count % 100 == 0:
                        self.stdout.write(f"Deleted {deleted_count} articles...")
                except Exception as e:
                    logger.error(f"Error deleting article {article.url}: {e}")
            
            self.stdout.write(self.style.SUCCESS(f"\nDeleted {deleted_count} low-quality articles"))