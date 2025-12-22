"""
Continuously process all articles in the database for clustering.
Processes articles in batches until the entire database is clustered.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q, Exists, OuterRef, Count
from datetime import timedelta
from feeds.models import Article, ArticleCluster
from feeds.local_clustering import LocalClusterBuilder
import logging
import time
from tqdm import tqdm

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Continuously cluster all articles in the database until complete'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Number of articles to process per batch (default: 500)'
        )
        parser.add_argument(
            '--similarity-threshold',
            type=float,
            default=0.65,
            help='Similarity threshold for clustering (0-1, default: 0.65)'
        )
        parser.add_argument(
            '--min-articles',
            type=int,
            default=3,
            help='Minimum articles per cluster (default: 3)'
        )
        parser.add_argument(
            '--window-hours',
            type=int,
            default=72,
            help='Time window in hours to look for similar articles (default: 72)'
        )
        parser.add_argument(
            '--sleep-between-batches',
            type=int,
            default=2,
            help='Seconds to sleep between batches (default: 2)'
        )
        parser.add_argument(
            '--max-batches',
            type=int,
            default=0,
            help='Maximum number of batches to process (0 for unlimited)'
        )
        parser.add_argument(
            '--reprocess',
            action='store_true',
            help='Reprocess already clustered articles'
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear all existing clusters before starting'
        )
        parser.add_argument(
            '--use-transformers',
            action='store_true',
            help='Use sentence transformers for better semantic similarity'
        )
        parser.add_argument(
            '--days-back',
            type=int,
            default=0,
            help='Only process articles from the last N days (0 for all articles)'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed progress information'
        )
    
    def handle(self, *args, **options):
        batch_size = options['batch_size']
        similarity_threshold = options['similarity_threshold']
        min_articles = options['min_articles']
        window_hours = options['window_hours']
        sleep_seconds = options['sleep_between_batches']
        max_batches = options['max_batches']
        reprocess = options['reprocess']
        use_transformers = options['use_transformers']
        days_back = options['days_back']
        verbose = options['verbose']
        
        # Clear existing clusters if requested
        if options['clear']:
            self.stdout.write(self.style.WARNING("Clearing all existing clusters..."))
            deleted_count = ArticleCluster.objects.all().delete()[0]
            self.stdout.write(self.style.SUCCESS(f"✓ Deleted {deleted_count} clusters"))
        
        # Initialize the cluster builder
        self.stdout.write("Initializing cluster builder...")
        cluster_builder = LocalClusterBuilder(
            similarity_threshold=similarity_threshold,
            min_cluster_size=min_articles,
            use_transformers=use_transformers,
            verbose=verbose
        )
        
        # Build base query
        base_query = Article.objects.select_related('feed__website')
        
        # Filter by date if specified
        if days_back > 0:
            since_date = timezone.now() - timedelta(days=days_back)
            base_query = base_query.filter(published_date__gte=since_date)
            self.stdout.write(f"Processing articles from the last {days_back} days")
        
        # Exclude already clustered articles unless reprocessing
        if not reprocess:
            base_query = base_query.filter(
                ~Exists(ArticleCluster.objects.filter(articles=OuterRef('pk')))
            )
        
        # Get total count
        total_articles = base_query.count()
        
        if total_articles == 0:
            self.stdout.write(self.style.WARNING("No unclustered articles found!"))
            return
        
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'='*60}\n"
                f"Starting continuous clustering process\n"
                f"Total articles to process: {total_articles:,}\n"
                f"Batch size: {batch_size}\n"
                f"Similarity threshold: {similarity_threshold}\n"
                f"Minimum articles per cluster: {min_articles}\n"
                f"Time window: {window_hours} hours\n"
                f"{'='*60}\n"
            )
        )
        
        # Process statistics
        processed_count = 0
        clusters_created = 0
        articles_clustered = 0
        batch_num = 0
        start_time = time.time()
        
        # Create progress bar
        with tqdm(total=total_articles, desc="Processing articles", unit="articles") as pbar:
            while True:
                batch_num += 1
                
                # Check max batches limit
                if max_batches > 0 and batch_num > max_batches:
                    self.stdout.write(
                        self.style.WARNING(f"\nReached maximum batch limit ({max_batches})")
                    )
                    break
                
                # Get next batch of unclustered articles
                if not reprocess:
                    # Re-query to exclude newly clustered articles
                    batch_query = Article.objects.select_related('feed__website').filter(
                        ~Exists(ArticleCluster.objects.filter(articles=OuterRef('pk')))
                    )
                    if days_back > 0:
                        batch_query = batch_query.filter(published_date__gte=since_date)
                else:
                    batch_query = base_query
                
                # Get batch ordered by date
                batch_articles = list(
                    batch_query.order_by('-published_date')[:batch_size]
                )
                
                if not batch_articles:
                    self.stdout.write(
                        self.style.SUCCESS("\n✓ All articles have been processed!")
                    )
                    break
                
                # Process this batch
                if verbose:
                    self.stdout.write(f"\nBatch {batch_num}: Processing {len(batch_articles)} articles")
                
                # For each article in the batch, find similar articles within the time window
                batch_clusters_created = 0
                batch_articles_clustered = 0
                
                for article in batch_articles:
                    # Skip if already clustered (in case of race conditions)
                    if not reprocess and ArticleCluster.objects.filter(articles=article).exists():
                        continue
                    
                    # Define time window
                    window_start = article.published_date - timedelta(hours=window_hours/2)
                    window_end = article.published_date + timedelta(hours=window_hours/2)
                    
                    # Find potential similar articles in the time window
                    candidate_articles = Article.objects.select_related('feed__website').filter(
                        published_date__gte=window_start,
                        published_date__lte=window_end
                    ).exclude(
                        id=article.id
                    )
                    
                    # Exclude already clustered articles unless reprocessing
                    if not reprocess:
                        candidate_articles = candidate_articles.filter(
                            ~Exists(ArticleCluster.objects.filter(articles=OuterRef('pk')))
                        )
                    
                    # Include the current article
                    candidates = [article] + list(candidate_articles[:200])  # Limit candidates for performance
                    
                    if len(candidates) >= min_articles:
                        # Try to build clusters from these candidates
                        clusters = cluster_builder.build_clusters_from_articles(
                            candidates,
                            min_cluster_size=min_articles
                        )
                        
                        # Create cluster objects
                        for cluster_articles in clusters:
                            if article in cluster_articles and len(cluster_articles) >= min_articles:
                                # Check if any of these articles are already clustered
                                if not reprocess:
                                    already_clustered = ArticleCluster.objects.filter(
                                        articles__in=[a.id for a in cluster_articles]
                                    ).exists()
                                    
                                    if already_clustered:
                                        continue
                                
                                # Create the cluster
                                cluster = cluster_builder.create_cluster_from_articles(cluster_articles)
                                if cluster:
                                    batch_clusters_created += 1
                                    batch_articles_clustered += len(cluster_articles)
                                    
                                    if verbose:
                                        self.stdout.write(
                                            self.style.SUCCESS(
                                                f"  ✓ Created cluster: {cluster.title[:50]}... "
                                                f"({cluster.articles.count()} articles)"
                                            )
                                        )
                                    break  # Article is now clustered, move to next
                
                # Update statistics
                processed_count += len(batch_articles)
                clusters_created += batch_clusters_created
                articles_clustered += batch_articles_clustered
                
                # Update progress bar
                pbar.update(len(batch_articles))
                pbar.set_postfix({
                    'Clusters': clusters_created,
                    'Clustered': articles_clustered,
                    'Batch': batch_num
                })
                
                # Show batch summary
                if batch_clusters_created > 0 or verbose:
                    elapsed = time.time() - start_time
                    rate = processed_count / elapsed if elapsed > 0 else 0
                    
                    self.stdout.write(
                        f"\nBatch {batch_num} complete: "
                        f"Created {batch_clusters_created} clusters from {batch_articles_clustered} articles "
                        f"(Rate: {rate:.1f} articles/sec)"
                    )
                
                # Sleep between batches to avoid overloading
                if sleep_seconds > 0 and batch_num < total_articles / batch_size:
                    time.sleep(sleep_seconds)
        
        # Final summary
        elapsed_time = time.time() - start_time
        hours = int(elapsed_time // 3600)
        minutes = int((elapsed_time % 3600) // 60)
        seconds = int(elapsed_time % 60)
        
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'='*60}\n"
                f"CLUSTERING COMPLETE!\n"
                f"{'='*60}\n"
                f"Total articles processed: {processed_count:,}\n"
                f"Total clusters created: {clusters_created:,}\n"
                f"Total articles clustered: {articles_clustered:,}\n"
                f"Clustering rate: {articles_clustered/processed_count*100:.1f}%\n"
                f"Time elapsed: {hours}h {minutes}m {seconds}s\n"
                f"Processing rate: {processed_count/elapsed_time:.1f} articles/sec\n"
                f"{'='*60}\n"
            )
        )
        
        # Show unclustered article stats
        remaining_unclustered = Article.objects.filter(
            ~Exists(ArticleCluster.objects.filter(articles=OuterRef('pk')))
        ).count()
        
        if remaining_unclustered > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"\nNote: {remaining_unclustered:,} articles remain unclustered "
                    f"(likely not enough similar articles within time windows)"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("\n✓ All articles have been successfully clustered!")
            )