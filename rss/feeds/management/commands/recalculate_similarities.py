"""
Recalculate article similarities with improved thresholds.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from feeds.models import Article, ArticleAnalysis
from feeds.article_analyzer import ArticleAnalyzer
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Recalculate article similarities with better thresholds'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=72,
            help='Look back period in hours (default: 72)'
        )
        parser.add_argument(
            '--threshold',
            type=float,
            default=0.85,
            help='Similarity threshold (default: 0.85)'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Maximum number of analyses to process (default: 100)'
        )
    
    def handle(self, *args, **options):
        hours = options['hours']
        threshold = options['threshold']
        limit = options['limit']
        
        since = timezone.now() - timedelta(hours=hours)
        
        self.stdout.write(f"Recalculating similarities for articles from last {hours} hours...")
        self.stdout.write(f"Using similarity threshold: {threshold}")
        
        # Get analyses to update
        analyses = ArticleAnalysis.objects.filter(
            article__published_date__gte=since
        ).select_related('article')[:limit]
        
        analyzer = ArticleAnalyzer()
        
        updated_count = 0
        total_similarities = 0
        
        for analysis in analyses:
            try:
                # Find similar articles with new threshold
                similar = analyzer.find_similar_articles(
                    analysis.article, 
                    threshold=threshold
                )
                
                # Clear old similarities
                analysis.similar_articles.clear()
                
                # Add new similarities (limit to top 10)
                for similar_article, score in similar[:10]:
                    analysis.similar_articles.add(similar_article)
                    total_similarities += 1
                    self.stdout.write(
                        f"  {analysis.article.title[:50]}... → "
                        f"{similar_article.title[:50]}... (score: {score:.2f})"
                    )
                
                updated_count += 1
                
                if updated_count % 10 == 0:
                    self.stdout.write(f"Processed {updated_count} analyses...")
                    
            except Exception as e:
                logger.error(f"Error processing analysis {analysis.id}: {e}")
                self.stdout.write(
                    self.style.WARNING(f"Error processing {analysis.article.title[:50]}: {e}")
                )
        
        self.stdout.write(
            self.style.SUCCESS(
                f"\nRecalculation complete!\n"
                f"Updated {updated_count} analyses\n"
                f"Found {total_similarities} similarities with threshold {threshold}"
            )
        )