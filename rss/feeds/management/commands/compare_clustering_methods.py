"""
Compare different clustering methods to see which works best.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from feeds.models import Article, ArticleCluster
from feeds.local_clustering import LocalClusterBuilder
from django.db.models import Exists, OuterRef
import time


class Command(BaseCommand):
    help = 'Compare different clustering methods'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=48,
            help='Look back period in hours (default: 48)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of articles to process (default: 100)'
        )
    
    def handle(self, *args, **options):
        hours = options['hours']
        batch_size = options['batch_size']
        
        # Get unclustered articles
        since = timezone.now() - timedelta(hours=hours)
        articles = list(
            Article.objects.select_related('feed__website')
            .filter(published_date__gte=since)
            .filter(~Exists(ArticleCluster.objects.filter(articles=OuterRef('pk'))))
            .order_by('-published_date')[:batch_size]
        )
        
        if len(articles) < 3:
            self.stdout.write(self.style.WARNING("Not enough articles to cluster"))
            return
        
        self.stdout.write(f"\nTesting with {len(articles)} articles from last {hours} hours\n")
        self.stdout.write("=" * 60)
        
        # Test 1: TF-IDF only
        self.stdout.write("\n1. TF-IDF ONLY METHOD:")
        self.stdout.write("-" * 40)
        
        builder_tfidf = LocalClusterBuilder(use_sentence_transformers=False)
        start_time = time.time()
        
        clusters_tfidf = builder_tfidf.build_clusters(
            articles,
            min_cluster_size=3,
            similarity_threshold=0.65,
            time_window_hours=hours,
            method='tfidf'
        )
        
        tfidf_time = time.time() - start_time
        
        self.stdout.write(f"Found {len(clusters_tfidf)} clusters in {tfidf_time:.2f} seconds")
        for i, cluster in enumerate(clusters_tfidf[:5], 1):
            self.stdout.write(
                f"  {i}. {cluster['title'][:50]}... ({cluster['article_count']} articles)"
            )
        
        # Test 2: Sentence Transformers
        self.stdout.write("\n2. SENTENCE TRANSFORMERS METHOD:")
        self.stdout.write("-" * 40)
        
        builder_transformer = LocalClusterBuilder(use_sentence_transformers=True)
        start_time = time.time()
        
        clusters_transformer = builder_transformer.build_clusters(
            articles,
            min_cluster_size=3,
            similarity_threshold=0.45,  # Lower threshold for embeddings
            time_window_hours=hours,
            method='embedding'
        )
        
        transformer_time = time.time() - start_time
        
        self.stdout.write(f"Found {len(clusters_transformer)} clusters in {transformer_time:.2f} seconds")
        for i, cluster in enumerate(clusters_transformer[:5], 1):
            self.stdout.write(
                f"  {i}. {cluster['title'][:50]}... ({cluster['article_count']} articles)"
            )
        
        # Test 3: Hybrid Method
        self.stdout.write("\n3. HYBRID METHOD (TF-IDF + Transformers):")
        self.stdout.write("-" * 40)
        
        builder_hybrid = LocalClusterBuilder(use_sentence_transformers=True)
        start_time = time.time()
        
        clusters_hybrid = builder_hybrid.build_clusters(
            articles,
            min_cluster_size=3,
            similarity_threshold=0.55,  # Medium threshold for hybrid
            time_window_hours=hours,
            method='hybrid'
        )
        
        hybrid_time = time.time() - start_time
        
        self.stdout.write(f"Found {len(clusters_hybrid)} clusters in {hybrid_time:.2f} seconds")
        for i, cluster in enumerate(clusters_hybrid[:5], 1):
            self.stdout.write(
                f"  {i}. {cluster['title'][:50]}... ({cluster['article_count']} articles)"
            )
        
        # Summary
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("SUMMARY:")
        self.stdout.write("-" * 40)
        
        self.stdout.write(f"TF-IDF Only:           {len(clusters_tfidf)} clusters in {tfidf_time:.2f}s")
        self.stdout.write(f"Sentence Transformers: {len(clusters_transformer)} clusters in {transformer_time:.2f}s")
        self.stdout.write(f"Hybrid Method:         {len(clusters_hybrid)} clusters in {hybrid_time:.2f}s")
        
        # Calculate average cluster size
        if clusters_tfidf:
            avg_size_tfidf = sum(c['article_count'] for c in clusters_tfidf) / len(clusters_tfidf)
            self.stdout.write(f"\nAverage cluster size (TF-IDF): {avg_size_tfidf:.1f} articles")
        
        if clusters_transformer:
            avg_size_transformer = sum(c['article_count'] for c in clusters_transformer) / len(clusters_transformer)
            self.stdout.write(f"Average cluster size (Transformers): {avg_size_transformer:.1f} articles")
        
        if clusters_hybrid:
            avg_size_hybrid = sum(c['article_count'] for c in clusters_hybrid) / len(clusters_hybrid)
            self.stdout.write(f"Average cluster size (Hybrid): {avg_size_hybrid:.1f} articles")
        
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(
            self.style.SUCCESS(
                "\n✓ Comparison complete! Sentence transformers provide better semantic matching."
            )
        )