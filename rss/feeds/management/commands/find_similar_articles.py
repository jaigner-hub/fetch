from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from feeds.models import Article, ArticleAnalysis
from feeds.similarity_detector import SimilarityDetector
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Find and update similar articles for existing content'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Number of days back to look for articles (default: 7)'
        )
        parser.add_argument(
            '--threshold',
            type=float,
            default=0.5,
            help='Similarity threshold (0.0-1.0, default: 0.5)'
        )
        parser.add_argument(
            '--max-similar',
            type=int,
            default=10,
            help='Maximum number of similar articles to find per article (default: 10)'
        )
        parser.add_argument(
            '--website',
            type=str,
            help='Filter by website name (optional)'
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Limit number of articles to process (optional)'
        )
        parser.add_argument(
            '--update-existing',
            action='store_true',
            help='Update existing similarity relationships'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output'
        )

    def handle(self, *args, **options):
        days = options['days']
        threshold = options['threshold']
        max_similar = options['max_similar']
        website_filter = options['website']
        limit = options['limit']
        update_existing = options['update_existing']
        verbose = options['verbose']
        
        # Get articles to process
        since_date = timezone.now() - timedelta(days=days)
        queryset = Article.objects.filter(
            published_date__gte=since_date
        ).select_related('feed__website')
        
        # Apply website filter if specified
        if website_filter:
            queryset = queryset.filter(feed__website__name__icontains=website_filter)
        
        # Filter out articles that already have similarity analysis (unless updating)
        if not update_existing:
            queryset = queryset.filter(analysis__similar_articles__isnull=True)
        
        # Apply limit if specified
        if limit:
            queryset = queryset[:limit]
        
        total_articles = queryset.count()
        self.stdout.write(f"Processing {total_articles} articles from the last {days} days...")
        
        # Initialize similarity detector
        detector = SimilarityDetector()
        
        processed = 0
        relationships_created = 0
        
        for article in queryset:
            processed += 1
            
            if verbose:
                self.stdout.write(f"\n[{processed}/{total_articles}] Processing: {article.title[:80]}...")
            
            try:
                # Find similar articles
                similar_articles = detector.find_similar_articles(
                    article,
                    threshold=threshold,
                    max_results=max_similar,
                    days_back=days * 2  # Look back further for similar content
                )
                
                if similar_articles:
                    # Get or create analysis for this article
                    analysis, created = ArticleAnalysis.objects.get_or_create(
                        article=article,
                        defaults={
                            'ai_summary': article.summary or '',
                            'topics': [],
                            'entities': {},
                            'keywords': []
                        }
                    )
                    
                    # Clear existing similar articles if updating
                    if update_existing and not created:
                        analysis.similar_articles.clear()
                    
                    # Add similar articles
                    similar_article_objects = []
                    for similar_article, scores in similar_articles:
                        if similar_article.id != article.id:  # Don't add self
                            similar_article_objects.append(similar_article)
                            
                            if verbose:
                                self.stdout.write(
                                    f"  - Similar: {similar_article.title[:60]} "
                                    f"(Title: {scores.get('title_similarity', 0):.2f}, "
                                    f"Content: {scores.get('content_similarity', 0):.2f})"
                                )
                    
                    if similar_article_objects:
                        analysis.similar_articles.add(*similar_article_objects)
                        relationships_created += len(similar_article_objects)
                        
                        if not verbose:
                            self.stdout.write(
                                f"  Found {len(similar_article_objects)} similar articles "
                                f"for: {article.title[:60]}"
                            )
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"  Error processing article {article.id}: {str(e)}")
                )
                continue
            
            # Show progress
            if processed % 10 == 0 and not verbose:
                self.stdout.write(f"Progress: {processed}/{total_articles} articles processed...")
        
        # Summary
        self.stdout.write(
            self.style.SUCCESS(
                f"\nCompleted! Processed {processed} articles, "
                f"created {relationships_created} similarity relationships."
            )
        )
        
        # Show statistics
        articles_with_similar = ArticleAnalysis.objects.filter(
            similar_articles__isnull=False
        ).distinct().count()
        
        self.stdout.write(
            f"\nStatistics:\n"
            f"  - Total articles with similar content: {articles_with_similar}\n"
            f"  - Average similar articles per article: "
            f"{relationships_created / processed if processed > 0 else 0:.1f}"
        )