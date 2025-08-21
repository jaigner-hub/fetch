"""
Fast clustering command that processes articles in bulk for better performance.
Uses batch processing, pre-computed vectors, and efficient similarity calculations.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q, Exists, OuterRef, Count, Prefetch
from datetime import datetime, timedelta
from feeds.models import Article, ArticleCluster
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.decomposition import TruncatedSVD
import logging
import time
from tqdm import tqdm
from collections import defaultdict
import pickle
import hashlib
import os

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Fast clustering of articles using batch processing and caching'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--window-hours',
            type=int,
            default=48,
            help='Time window in hours for clustering (default: 48)'
        )
        parser.add_argument(
            '--similarity-threshold',
            type=float,
            default=0.55,
            help='Similarity threshold for clustering (0-1, default: 0.55)'
        )
        parser.add_argument(
            '--min-articles',
            type=int,
            default=3,
            help='Minimum articles per cluster (default: 3)'
        )
        parser.add_argument(
            '--days-back',
            type=int,
            default=7,
            help='Process articles from the last N days (default: 7)'
        )
        parser.add_argument(
            '--batch-hours',
            type=int,
            default=6,
            help='Process articles in time batches of N hours (default: 6)'
        )
        parser.add_argument(
            '--cache-vectors',
            action='store_true',
            help='Cache TF-IDF vectors to disk for reuse'
        )
        parser.add_argument(
            '--clear-cache',
            action='store_true',
            help='Clear vector cache before starting'
        )
        parser.add_argument(
            '--max-features',
            type=int,
            default=2000,
            help='Maximum TF-IDF features (default: 2000)'
        )
        parser.add_argument(
            '--use-lsa',
            action='store_true',
            help='Use LSA dimensionality reduction for speed'
        )
        parser.add_argument(
            '--lsa-components',
            type=int,
            default=100,
            help='Number of LSA components (default: 100)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without creating clusters (for testing)'
        )
    
    def handle(self, *args, **options):
        window_hours = options['window_hours']
        similarity_threshold = options['similarity_threshold']
        min_articles = options['min_articles']
        days_back = options['days_back']
        batch_hours = options['batch_hours']
        cache_vectors = options['cache_vectors']
        max_features = options['max_features']
        use_lsa = options['use_lsa']
        lsa_components = options['lsa_components']
        dry_run = options['dry_run']
        
        # Cache directory
        cache_dir = '/tmp/article_clustering_cache'
        if cache_vectors:
            os.makedirs(cache_dir, exist_ok=True)
            if options['clear_cache']:
                self.stdout.write("Clearing cache...")
                for f in os.listdir(cache_dir):
                    os.remove(os.path.join(cache_dir, f))
        
        # Calculate time range
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days_back)
        
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'='*60}\n"
                f"Fast Article Clustering\n"
                f"{'='*60}\n"
                f"Processing articles from: {start_date.strftime('%Y-%m-%d %H:%M')}\n"
                f"Processing articles to: {end_date.strftime('%Y-%m-%d %H:%M')}\n"
                f"Time window: {window_hours} hours\n"
                f"Batch size: {batch_hours} hours\n"
                f"Similarity threshold: {similarity_threshold}\n"
                f"Min articles per cluster: {min_articles}\n"
                f"Max TF-IDF features: {max_features}\n"
                f"Use LSA: {use_lsa} ({lsa_components} components)\n"
                f"Cache vectors: {cache_vectors}\n"
                f"{'='*60}\n"
            )
        )
        
        # Get all unclustered articles in date range
        self.stdout.write("Loading articles...")
        unclustered_articles = Article.objects.select_related(
            'feed__website'
        ).prefetch_related(
            'analysis'
        ).filter(
            published_date__gte=start_date,
            published_date__lte=end_date
        ).exclude(
            Exists(ArticleCluster.objects.filter(articles=OuterRef('pk')))
        ).order_by('published_date')
        
        total_articles = unclustered_articles.count()
        
        if total_articles == 0:
            self.stdout.write(self.style.WARNING("No unclustered articles found!"))
            return
        
        self.stdout.write(f"Found {total_articles:,} unclustered articles")
        
        # Initialize TF-IDF vectorizer
        vectorizer = TfidfVectorizer(
            max_features=max_features,
            stop_words='english',
            ngram_range=(1, 2),  # Reduced from (1,3) for speed
            min_df=2,
            max_df=0.8,
            use_idf=True,
            smooth_idf=True,
            sublinear_tf=True,
            strip_accents='unicode'
        )
        
        # Process in time batches
        current_date = start_date
        total_clusters = 0
        total_clustered = 0
        start_time = time.time()
        
        with tqdm(total=total_articles, desc="Processing articles") as pbar:
            while current_date < end_date:
                batch_end = min(current_date + timedelta(hours=batch_hours), end_date)
                
                # Get articles in this time batch
                batch_articles = list(unclustered_articles.filter(
                    published_date__gte=current_date,
                    published_date__lt=batch_end
                ))
                
                if not batch_articles:
                    current_date = batch_end
                    continue
                
                self.stdout.write(
                    f"\nProcessing batch: {current_date.strftime('%Y-%m-%d %H:%M')} to "
                    f"{batch_end.strftime('%Y-%m-%d %H:%M')} ({len(batch_articles)} articles)"
                )
                
                # Get potential candidates from wider window
                window_start = current_date - timedelta(hours=window_hours/2)
                window_end = batch_end + timedelta(hours=window_hours/2)
                
                candidates = list(Article.objects.select_related(
                    'feed__website'
                ).filter(
                    published_date__gte=window_start,
                    published_date__lte=window_end
                ).exclude(
                    Exists(ArticleCluster.objects.filter(articles=OuterRef('pk')))
                ))
                
                if len(candidates) < min_articles:
                    self.stdout.write(f"  Skip: Not enough candidates ({len(candidates)})")
                    pbar.update(len(batch_articles))
                    current_date = batch_end
                    continue
                
                # Prepare texts and compute vectors
                cache_key = None
                if cache_vectors:
                    # Create cache key from article IDs
                    ids_str = ','.join(str(a.id) for a in candidates)
                    cache_key = hashlib.md5(ids_str.encode()).hexdigest()
                    cache_file = os.path.join(cache_dir, f"{cache_key}.pkl")
                    
                    # Try to load from cache
                    if os.path.exists(cache_file):
                        with open(cache_file, 'rb') as f:
                            vectors = pickle.load(f)
                        self.stdout.write(f"  Loaded vectors from cache")
                    else:
                        vectors = self._compute_vectors(candidates, vectorizer, use_lsa, lsa_components)
                        with open(cache_file, 'wb') as f:
                            pickle.dump(vectors, f)
                else:
                    vectors = self._compute_vectors(candidates, vectorizer, use_lsa, lsa_components)
                
                # Create article ID to index mapping
                id_to_idx = {a.id: i for i, a in enumerate(candidates)}
                
                # Process each article in batch
                batch_clusters = []
                used_articles = set()
                
                for article in batch_articles:
                    if article.id in used_articles:
                        continue
                    
                    # Get article's vector
                    if article.id not in id_to_idx:
                        continue
                    
                    seed_idx = id_to_idx[article.id]
                    
                    # Find similar articles using vectorized operations
                    similarities = cosine_similarity(vectors[seed_idx:seed_idx+1], vectors)[0]
                    
                    # Get indices above threshold
                    similar_indices = np.where(similarities >= similarity_threshold)[0]
                    
                    # Filter by time window and unused articles
                    cluster_articles = []
                    for idx in similar_indices:
                        candidate = candidates[idx]
                        if candidate.id not in used_articles:
                            time_diff = abs((candidate.published_date - article.published_date).total_seconds() / 3600)
                            if time_diff <= window_hours:
                                cluster_articles.append(candidate)
                    
                    # Create cluster if large enough
                    if len(cluster_articles) >= min_articles:
                        batch_clusters.append(cluster_articles)
                        used_articles.update(a.id for a in cluster_articles)
                
                # Create cluster objects
                if not dry_run:
                    for cluster_articles in batch_clusters:
                        cluster = self._create_cluster(cluster_articles)
                        if cluster:
                            total_clusters += 1
                            total_clustered += len(cluster_articles)
                            self.stdout.write(
                                f"  ✓ Cluster: {cluster.title[:50]}... ({len(cluster_articles)} articles)"
                            )
                else:
                    total_clusters += len(batch_clusters)
                    total_clustered += sum(len(c) for c in batch_clusters)
                    self.stdout.write(f"  [DRY RUN] Would create {len(batch_clusters)} clusters")
                
                pbar.update(len(batch_articles))
                current_date = batch_end
        
        # Final summary
        elapsed = time.time() - start_time
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'='*60}\n"
                f"CLUSTERING COMPLETE!\n"
                f"{'='*60}\n"
                f"Total articles processed: {total_articles:,}\n"
                f"Total clusters created: {total_clusters:,}\n"
                f"Total articles clustered: {total_clustered:,}\n"
                f"Clustering rate: {total_clustered/total_articles*100:.1f}%\n"
                f"Time elapsed: {elapsed:.1f} seconds\n"
                f"Processing rate: {total_articles/elapsed:.1f} articles/sec\n"
                f"{'='*60}\n"
            )
        )
    
    def _compute_vectors(self, articles, vectorizer, use_lsa, lsa_components):
        """Compute TF-IDF vectors for articles."""
        # Prepare texts
        texts = []
        for article in articles:
            text = self._prepare_text(article)
            texts.append(text)
        
        # Compute TF-IDF
        try:
            tfidf_matrix = vectorizer.fit_transform(texts)
            
            # Apply LSA if requested and beneficial
            if use_lsa and tfidf_matrix.shape[0] > lsa_components + 1:
                svd = TruncatedSVD(n_components=lsa_components, random_state=42)
                vectors = svd.fit_transform(tfidf_matrix)
            else:
                vectors = tfidf_matrix.toarray()
            
            return vectors
            
        except Exception as e:
            logger.error(f"Error computing vectors: {e}")
            # Return identity matrix as fallback
            return np.eye(len(articles))
    
    def _prepare_text(self, article):
        """Prepare article text for vectorization."""
        parts = []
        
        # Title is most important
        if article.title:
            # Add title multiple times for emphasis
            parts.extend([article.title] * 3)
        
        # Add summary or content snippet
        if hasattr(article, 'analysis') and article.analysis:
            if article.analysis.ai_summary:
                parts.append(article.analysis.ai_summary)
        
        if article.summary:
            parts.append(article.summary[:500])
        elif article.content:
            # Clean content
            content = article.content[:1000]
            # Remove HTML if present
            import re
            content = re.sub(r'<[^>]+>', ' ', content)
            parts.append(content)
        
        # Add source for context
        if article.feed and article.feed.website:
            parts.append(article.feed.website.name)
        
        return ' '.join(parts)
    
    def _create_cluster(self, articles):
        """Create a cluster from articles."""
        if len(articles) < 2:
            return None
        
        # Generate cluster title
        title = self._generate_cluster_title(articles)
        
        # Get source diversity
        sources = set()
        for article in articles:
            if article.feed and article.feed.website:
                sources.add(article.feed.website.name)
        
        try:
            # Create cluster
            cluster = ArticleCluster.objects.create(
                title=title[:200],
                description=f"Cluster of {len(articles)} articles from {len(sources)} sources",
                event_type='ongoing_story',
                confidence_score=0.7
            )
            
            # Add articles
            cluster.articles.set(articles)
            
            # Update metadata
            cluster.update_metadata()
            
            return cluster
            
        except Exception as e:
            logger.error(f"Error creating cluster: {e}")
            return None
    
    def _generate_cluster_title(self, articles):
        """Generate a title for the cluster."""
        # Get most common significant words from titles
        from collections import Counter
        import re
        
        all_words = []
        for article in articles[:10]:  # Sample first 10
            if article.title:
                # Extract significant words
                words = re.findall(r'\b[A-Z][a-z]+\b', article.title)
                all_words.extend(words)
        
        if not all_words:
            return f"News Cluster ({len(articles)} articles)"
        
        # Get most common words
        word_counts = Counter(all_words)
        top_words = [word for word, _ in word_counts.most_common(3)]
        
        if top_words:
            return f"{' '.join(top_words)} - Related Coverage"
        else:
            return articles[0].title[:50] + "..."