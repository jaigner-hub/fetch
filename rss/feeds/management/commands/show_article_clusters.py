from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from collections import defaultdict
from feeds.models import Article, ArticleAnalysis
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Show clusters of similar articles (articles covering the same topic/event)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Number of days back to look for clusters (default: 7)'
        )
        parser.add_argument(
            '--min-cluster-size',
            type=int,
            default=3,
            help='Minimum number of articles to form a cluster (default: 3)'
        )
        parser.add_argument(
            '--website',
            type=str,
            help='Filter by website name (optional)'
        )
        parser.add_argument(
            '--show-all',
            action='store_true',
            help='Show all articles in each cluster (default: show first 5)'
        )

    def handle(self, *args, **options):
        days = options['days']
        min_cluster_size = options['min_cluster_size']
        website_filter = options['website']
        show_all = options['show_all']
        
        # Get articles with similarity analysis from the specified time period
        since_date = timezone.now() - timedelta(days=days)
        queryset = ArticleAnalysis.objects.filter(
            article__published_date__gte=since_date,
            similar_articles__isnull=False
        ).select_related('article', 'article__feed__website').prefetch_related('similar_articles')
        
        # Apply website filter if specified
        if website_filter:
            queryset = queryset.filter(
                article__feed__website__name__icontains=website_filter
            )
        
        self.stdout.write(f"Finding article clusters from the last {days} days...\n")
        
        # Build clusters using Union-Find approach
        article_clusters = {}  # article_id -> cluster_id
        cluster_articles = defaultdict(set)  # cluster_id -> set of article_ids
        cluster_id_counter = 0
        
        for analysis in queryset:
            main_article = analysis.article
            similar_articles = analysis.similar_articles.all()
            
            if not similar_articles:
                continue
            
            # Get or create cluster for main article
            if main_article.id not in article_clusters:
                article_clusters[main_article.id] = cluster_id_counter
                cluster_articles[cluster_id_counter].add(main_article.id)
                cluster_id_counter += 1
            
            main_cluster_id = article_clusters[main_article.id]
            
            # Add similar articles to the same cluster
            for similar_article in similar_articles:
                if similar_article.id in article_clusters:
                    # Merge clusters if article already belongs to another cluster
                    existing_cluster_id = article_clusters[similar_article.id]
                    if existing_cluster_id != main_cluster_id:
                        # Merge smaller cluster into larger one
                        if len(cluster_articles[existing_cluster_id]) <= len(cluster_articles[main_cluster_id]):
                            articles_to_move = cluster_articles[existing_cluster_id]
                            for article_id in articles_to_move:
                                article_clusters[article_id] = main_cluster_id
                                cluster_articles[main_cluster_id].add(article_id)
                            del cluster_articles[existing_cluster_id]
                        else:
                            articles_to_move = cluster_articles[main_cluster_id]
                            for article_id in articles_to_move:
                                article_clusters[article_id] = existing_cluster_id
                                cluster_articles[existing_cluster_id].add(article_id)
                            del cluster_articles[main_cluster_id]
                            main_cluster_id = existing_cluster_id
                else:
                    article_clusters[similar_article.id] = main_cluster_id
                    cluster_articles[main_cluster_id].add(similar_article.id)
        
        # Filter clusters by minimum size
        valid_clusters = {
            cluster_id: article_ids 
            for cluster_id, article_ids in cluster_articles.items()
            if len(article_ids) >= min_cluster_size
        }
        
        if not valid_clusters:
            self.stdout.write(
                self.style.WARNING(
                    f"No clusters found with at least {min_cluster_size} articles."
                )
            )
            return
        
        # Sort clusters by size (largest first)
        sorted_clusters = sorted(
            valid_clusters.items(),
            key=lambda x: len(x[1]),
            reverse=True
        )
        
        # Display clusters
        self.stdout.write(
            self.style.SUCCESS(
                f"Found {len(sorted_clusters)} clusters with {min_cluster_size}+ articles:\n"
            )
        )
        
        for idx, (cluster_id, article_ids) in enumerate(sorted_clusters, 1):
            # Get articles in this cluster
            articles = Article.objects.filter(
                id__in=article_ids
            ).select_related(
                'feed__website'
            ).order_by('-published_date')
            
            if not articles:
                continue
            
            # Get cluster timespan
            earliest = articles.last().published_date
            latest = articles.first().published_date
            
            # Try to determine cluster topic from the most common title words
            title_words = defaultdict(int)
            for article in articles:
                # Extract significant words from title
                words = article.title.lower().split()
                for word in words:
                    if len(word) > 4:  # Skip short words
                        title_words[word] += 1
            
            # Get most common words for cluster topic
            common_words = sorted(title_words.items(), key=lambda x: x[1], reverse=True)[:3]
            cluster_topic = ' '.join([word for word, _ in common_words])
            
            # Display cluster header
            self.stdout.write(
                self.style.WARNING(f"\n{'='*80}")
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"CLUSTER #{idx}: {len(article_ids)} articles "
                    f"({earliest.strftime('%Y-%m-%d')} to {latest.strftime('%Y-%m-%d')})"
                )
            )
            if cluster_topic:
                self.stdout.write(f"Common keywords: {cluster_topic}")
            self.stdout.write("")
            
            # Display articles in cluster
            articles_to_show = articles if show_all else articles[:5]
            
            for article in articles_to_show:
                self.stdout.write(
                    f"  • [{article.published_date.strftime('%m/%d %H:%M')}] "
                    f"{article.feed.website.name}: {article.title[:80]}"
                )
            
            if not show_all and len(articles) > 5:
                self.stdout.write(f"  ... and {len(articles) - 5} more articles")
            
            # Show websites involved
            websites = set(article.feed.website.name for article in articles)
            self.stdout.write(f"\nSources ({len(websites)}): {', '.join(sorted(websites))}")
        
        # Summary statistics
        total_articles_in_clusters = sum(len(ids) for ids in valid_clusters.values())
        avg_cluster_size = total_articles_in_clusters / len(valid_clusters) if valid_clusters else 0
        
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'='*80}\n"
                f"Summary:\n"
                f"  - Total clusters: {len(valid_clusters)}\n"
                f"  - Articles in clusters: {total_articles_in_clusters}\n"
                f"  - Average cluster size: {avg_cluster_size:.1f}\n"
                f"  - Largest cluster: {len(sorted_clusters[0][1]) if sorted_clusters else 0} articles"
            )
        )