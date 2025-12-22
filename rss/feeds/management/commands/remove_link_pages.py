"""
Remove articles that are primarily link/navigation pages rather than content.
"""
from django.core.management.base import BaseCommand
from feeds.models import Article
from bs4 import BeautifulSoup
import re
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Remove articles that are primarily navigation/link pages'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )
        parser.add_argument(
            '--max-link-ratio',
            type=float,
            default=0.5,
            help='Maximum ratio of links to paragraphs (default: 0.5)'
        )
        parser.add_argument(
            '--min-paragraph-words',
            type=int,
            default=20,
            help='Minimum average words per paragraph (default: 20)'
        )
    
    def is_link_page(self, article, max_link_ratio=0.5, min_paragraph_words=20):
        """
        Determine if an article is primarily a link/navigation page.
        
        Returns: (is_link_page, reason)
        """
        if not article.content:
            return True, "no_content"
        
        soup = BeautifulSoup(article.content, 'html.parser')
        
        # Get all text
        text = soup.get_text(strip=True)
        word_count = len(text.split())
        
        # Too short is always bad
        if word_count < 100:
            return True, f"too_short ({word_count} words)"
        
        # Count links and paragraphs
        links = soup.find_all('a')
        paragraphs = soup.find_all('p')
        
        # High link density is bad
        if word_count > 0:
            links_per_100_words = (len(links) / word_count) * 100
            if links_per_100_words > 10:  # More than 10 links per 100 words
                return True, f"high_link_density ({links_per_100_words:.1f} links/100 words)"
        
        # Check if paragraphs are too short (typical of navigation pages)
        if paragraphs:
            total_p_words = 0
            for p in paragraphs:
                p_text = p.get_text(strip=True)
                total_p_words += len(p_text.split())
            
            avg_words_per_p = total_p_words / len(paragraphs)
            if avg_words_per_p < min_paragraph_words:
                return True, f"short_paragraphs (avg {avg_words_per_p:.1f} words)"
        
        # Check for common navigation patterns
        text_lower = text.lower()
        nav_patterns = [
            'where to stream',
            'where to watch',
            'streaming on',
            'available on',
            'watch online',
            'stream online',
            'click here',
            'read more',
            'continue reading',
            'see all',
            'view all',
            'browse all'
        ]
        
        nav_count = sum(1 for pattern in nav_patterns if pattern in text_lower)
        if nav_count >= 3:
            return True, f"navigation_patterns ({nav_count} patterns found)"
        
        # Check for list-heavy content (common in navigation pages)
        lists = soup.find_all(['ul', 'ol'])
        if lists:
            list_items = soup.find_all('li')
            if len(list_items) > 20:  # Too many list items
                return True, f"list_heavy ({len(list_items)} list items)"
        
        # Check title patterns that indicate non-articles
        if article.title:
            title_lower = article.title.lower()
            bad_title_patterns = [
                'where to stream',
                'where to watch',
                'streaming guide',
                'watch guide',
                '| stream',
                '| watch',
                'page not found',
                '404',
                'error'
            ]
            
            for pattern in bad_title_patterns:
                if pattern in title_lower:
                    return True, f"bad_title_pattern ({pattern})"
        
        # Check URL patterns
        if article.url:
            url_lower = article.url.lower()
            bad_url_patterns = [
                '/page/',
                '/tag/',
                '/category/',
                '/archive/',
                '/search/',
                '/browse/',
                '/watch/',
                '/stream/',
                '/where-to-'
            ]
            
            for pattern in bad_url_patterns:
                if pattern in url_lower:
                    return True, f"bad_url_pattern ({pattern})"
        
        return False, "ok"
    
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        max_link_ratio = options['max_link_ratio']
        min_paragraph_words = options['min_paragraph_words']
        
        self.stdout.write("Scanning for link/navigation pages...")
        
        articles_to_delete = []
        reasons_count = {}
        
        # Process all articles
        all_articles = Article.objects.all()
        total_count = all_articles.count()
        
        self.stdout.write(f"Checking {total_count} articles...")
        
        batch_size = 500
        for i in range(0, total_count, batch_size):
            batch = all_articles[i:i+batch_size]
            
            for article in batch:
                is_link_page, reason = self.is_link_page(
                    article, 
                    max_link_ratio=max_link_ratio,
                    min_paragraph_words=min_paragraph_words
                )
                
                if is_link_page:
                    articles_to_delete.append(article)
                    reasons_count[reason] = reasons_count.get(reason, 0) + 1
            
            if (i + batch_size) % 2000 == 0:
                self.stdout.write(f"Processed {min(i + batch_size, total_count)} articles...")
        
        # Report findings
        self.stdout.write("\n" + "="*50)
        self.stdout.write("LINK PAGE DETECTION RESULTS")
        self.stdout.write("="*50)
        self.stdout.write(f"Total articles checked: {total_count}")
        self.stdout.write(f"Link/navigation pages found: {len(articles_to_delete)}")
        
        if reasons_count:
            self.stdout.write("\nReasons breakdown:")
            for reason, count in sorted(reasons_count.items(), key=lambda x: -x[1]):
                self.stdout.write(f"  {reason}: {count}")
        
        # Show examples
        if articles_to_delete:
            self.stdout.write("\nExamples:")
            for article in articles_to_delete[:5]:
                domain = article.url.split('/')[2] if '/' in article.url else 'unknown'
                self.stdout.write(f"  {domain}: {article.title[:60]}...")
                self.stdout.write(f"    URL: {article.url}")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\nDRY RUN - No articles deleted"))
        else:
            # Delete the articles
            self.stdout.write("\nDeleting link/navigation pages...")
            deleted_count = 0
            
            for article in articles_to_delete:
                try:
                    article.delete()
                    deleted_count += 1
                    
                    if deleted_count % 100 == 0:
                        self.stdout.write(f"Deleted {deleted_count} articles...")
                except Exception as e:
                    logger.error(f"Error deleting article {article.url}: {e}")
            
            self.stdout.write(self.style.SUCCESS(f"\nDeleted {deleted_count} link/navigation pages"))