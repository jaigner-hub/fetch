from django.core.management.base import BaseCommand
from django.db.models import Q
from feeds.models import Article
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Clean up low-content articles that are likely index/category pages'

    def add_arguments(self, parser):
        parser.add_argument(
            '--min-content-length',
            type=int,
            default=300,
            help='Minimum text content length to keep (default: 300 characters)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Don't actually delete, just show what would be deleted"
        )
        parser.add_argument(
            '--website',
            type=str,
            help='Filter by website name (optional)'
        )
        parser.add_argument(
            '--show-samples',
            action='store_true',
            help='Show sample titles of articles to be deleted'
        )
        parser.add_argument(
            '--exclude-analyzed',
            action='store_true',
            help='Skip articles that have been analyzed'
        )

    def handle(self, *args, **options):
        min_content_length = options['min_content_length']
        dry_run = options['dry_run']
        website_filter = options['website']
        show_samples = options['show_samples']
        exclude_analyzed = options['exclude_analyzed']
        
        # Get articles to check
        queryset = Article.objects.all()
        
        # Apply website filter if specified
        if website_filter:
            queryset = queryset.filter(feed__website__name__icontains=website_filter)
        
        # Exclude analyzed articles if requested
        if exclude_analyzed:
            queryset = queryset.filter(analysis__isnull=True)
        
        total_articles = queryset.count()
        self.stdout.write(f"Checking {total_articles} articles for low content...")
        
        low_content_articles = []
        no_content_articles = []
        checked = 0
        
        # Check each article
        for article in queryset.iterator(chunk_size=100):
            checked += 1
            
            # Show progress every 100 articles
            if checked % 100 == 0:
                self.stdout.write(f"Progress: {checked}/{total_articles} articles checked...")
            
            # Check if article has no content at all
            if not article.content or article.content.strip() == '':
                no_content_articles.append(article)
                continue
            
            # Parse content to get text length
            try:
                soup = BeautifulSoup(article.content, 'html.parser')
                text_content = soup.get_text(strip=True)
                
                # Check if text content is too short
                if len(text_content) < min_content_length:
                    low_content_articles.append((article, len(text_content)))
            except Exception as e:
                logger.error(f"Error parsing article {article.id}: {e}")
                continue
        
        # Summary
        self.stdout.write(
            f"\nResults:\n"
            f"  - Total articles checked: {total_articles}\n"
            f"  - Articles with no content: {len(no_content_articles)}\n"
            f"  - Articles with low content (<{min_content_length} chars): {len(low_content_articles)}\n"
            f"  - Total to be deleted: {len(no_content_articles) + len(low_content_articles)}"
        )
        
        # Show samples if requested
        if show_samples:
            if no_content_articles:
                self.stdout.write("\nSample articles with NO content:")
                for article in no_content_articles[:10]:
                    self.stdout.write(
                        f"  - [{article.feed.website.name}] {article.title[:80]}"
                    )
            
            if low_content_articles:
                self.stdout.write(f"\nSample articles with LOW content (<{min_content_length} chars):")
                for article, text_len in low_content_articles[:10]:
                    self.stdout.write(
                        f"  - [{article.feed.website.name}] {article.title[:60]} ({text_len} chars)"
                    )
        
        # Group by website for better visibility
        website_stats = {}
        for article in no_content_articles:
            website_name = article.feed.website.name
            if website_name not in website_stats:
                website_stats[website_name] = {'no_content': 0, 'low_content': 0}
            website_stats[website_name]['no_content'] += 1
        
        for article, _ in low_content_articles:
            website_name = article.feed.website.name
            if website_name not in website_stats:
                website_stats[website_name] = {'no_content': 0, 'low_content': 0}
            website_stats[website_name]['low_content'] += 1
        
        if website_stats:
            self.stdout.write("\nBreakdown by website:")
            for website, stats in sorted(website_stats.items(), 
                                        key=lambda x: x[1]['no_content'] + x[1]['low_content'], 
                                        reverse=True)[:10]:
                total = stats['no_content'] + stats['low_content']
                self.stdout.write(
                    f"  - {website}: {total} articles "
                    f"(no content: {stats['no_content']}, low: {stats['low_content']})"
                )
        
        # Delete if not dry run
        if not dry_run and (no_content_articles or low_content_articles):
            confirm = input(
                f"\nAre you sure you want to delete {len(no_content_articles) + len(low_content_articles)} articles? "
                f"This cannot be undone! (yes/no): "
            )
            
            if confirm.lower() == 'yes':
                # Delete no content articles
                if no_content_articles:
                    no_content_ids = [a.id for a in no_content_articles]
                    deleted_count = Article.objects.filter(id__in=no_content_ids).delete()[0]
                    self.stdout.write(
                        self.style.SUCCESS(f"Deleted {deleted_count} articles with no content")
                    )
                
                # Delete low content articles
                if low_content_articles:
                    low_content_ids = [a.id for a, _ in low_content_articles]
                    deleted_count = Article.objects.filter(id__in=low_content_ids).delete()[0]
                    self.stdout.write(
                        self.style.SUCCESS(f"Deleted {deleted_count} articles with low content")
                    )
            else:
                self.stdout.write("Deletion cancelled")
        elif dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "\nDRY RUN - No articles were deleted. "
                    "Run without --dry-run to actually delete."
                )
            )