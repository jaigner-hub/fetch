"""
Build article clusters using local similarity detection (no API calls).
Uses TF-IDF, SimHash, and optionally sentence transformers for clustering.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from feeds.models import Article, ArticleCluster
from django.db.models import Q, Exists, OuterRef
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.cluster import DBSCAN, AgglomerativeClustering
import logging
from typing import List, Dict, Set, Tuple
from collections import defaultdict
import re

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Build article clusters using local similarity detection (no API calls)'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=48,
            help='Look back period in hours (default: 48)'
        )
        parser.add_argument(
            '--min-articles',
            type=int,
            default=3,
            help='Minimum articles per cluster (default: 3)'
        )
        parser.add_argument(
            '--similarity-threshold',
            type=float,
            default=0.65,
            help='Similarity threshold for clustering (0-1, default: 0.65)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=200,
            help='Number of articles to process per batch (default: 200)'
        )
        parser.add_argument(
            '--use-transformers',
            action='store_true',
            help='Use sentence transformers for semantic similarity (requires sentence-transformers package)'
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing clusters before building new ones'
        )
        parser.add_argument(
            '--skip-clustered',
            action='store_true',
            default=True,
            help='Skip articles already in clusters (default: True)'
        )
        parser.add_argument(
            '--clustering-method',
            type=str,
            choices=['dbscan', 'agglomerative', 'custom'],
            default='custom',
            help='Clustering algorithm to use (default: custom)'
        )
    
    def __init__(self):
        super().__init__()
        self.tfidf_vectorizer = None
        self.sentence_model = None
        
    def handle(self, *args, **options):
        hours = options['hours']
        min_articles = options['min_articles']
        similarity_threshold = options['similarity_threshold']
        batch_size = options['batch_size']
        use_transformers = options['use_transformers']
        skip_clustered = options['skip_clustered']
        clustering_method = options['clustering_method']
        
        if options['clear']:
            self.stdout.write("Clearing existing clusters...")
            ArticleCluster.objects.all().delete()
        
        # Initialize sentence transformer if requested
        if use_transformers:
            try:
                from sentence_transformers import SentenceTransformer
                self.stdout.write("Loading sentence transformer model...")
                self.sentence_model = SentenceTransformer('all-MiniLM-L6-v2')
                self.stdout.write(self.style.SUCCESS("✓ Sentence transformer loaded"))
            except ImportError:
                self.stdout.write(
                    self.style.WARNING(
                        "sentence-transformers not installed. Install with: pip install sentence-transformers"
                    )
                )
                use_transformers = False
        
        # Initialize TF-IDF vectorizer
        self.tfidf_vectorizer = TfidfVectorizer(
            max_features=3000,
            stop_words='english',
            ngram_range=(1, 3),
            min_df=2,
            max_df=0.95,
            use_idf=True,
            smooth_idf=True,
            sublinear_tf=True
        )
        
        # Build base query
        since = timezone.now() - timedelta(hours=hours)
        base_query = Article.objects.select_related('feed__website').filter(
            published_date__gte=since
        )
        
        if skip_clustered:
            base_query = base_query.filter(
                ~Exists(ArticleCluster.objects.filter(articles=OuterRef('pk')))
            )
        
        # Get articles
        articles = list(base_query.order_by('-published_date')[:batch_size])
        
        if len(articles) < min_articles:
            self.stdout.write(
                self.style.WARNING(f"Only {len(articles)} unclustered articles found. Need at least {min_articles}.")
            )
            return
        
        self.stdout.write(f"Processing {len(articles)} articles...")
        
        # Build clusters based on method
        if clustering_method == 'custom':
            clusters = self.build_custom_clusters(
                articles, 
                similarity_threshold, 
                min_articles,
                use_transformers
            )
        elif clustering_method == 'dbscan':
            clusters = self.build_dbscan_clusters(
                articles,
                similarity_threshold,
                min_articles,
                use_transformers
            )
        else:  # agglomerative
            clusters = self.build_agglomerative_clusters(
                articles,
                similarity_threshold,
                min_articles,
                use_transformers
            )
        
        # Create cluster objects
        created_count = 0
        for cluster_articles in clusters:
            if len(cluster_articles) >= min_articles:
                cluster = self.create_cluster_from_articles(cluster_articles)
                if cluster:
                    created_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Created: {cluster.title[:60]}... "
                            f"({cluster.articles.count()} articles, "
                            f"{cluster.source_count} sources)"
                        )
                    )
        
        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Completed! Created {created_count} clusters from {len(articles)} articles"
            )
        )
    
    def build_custom_clusters(
        self, 
        articles: List[Article], 
        threshold: float,
        min_articles: int,
        use_transformers: bool
    ) -> List[List[Article]]:
        """Build clusters using custom similarity-based approach."""
        
        # Calculate similarity matrix
        similarity_matrix = self.calculate_similarity_matrix(articles, use_transformers)
        
        # Build clusters greedily
        clusters = []
        used_indices = set()
        
        for i in range(len(articles)):
            if i in used_indices:
                continue
            
            # Find all articles similar to this one
            cluster_indices = {i}
            
            for j in range(len(articles)):
                if j != i and j not in used_indices:
                    if similarity_matrix[i][j] >= threshold:
                        cluster_indices.add(j)
            
            # If cluster is large enough, keep it
            if len(cluster_indices) >= min_articles:
                cluster = [articles[idx] for idx in cluster_indices]
                clusters.append(cluster)
                used_indices.update(cluster_indices)
        
        return clusters
    
    def build_dbscan_clusters(
        self,
        articles: List[Article],
        threshold: float,
        min_articles: int,
        use_transformers: bool
    ) -> List[List[Article]]:
        """Build clusters using DBSCAN algorithm."""
        
        # Get feature vectors
        if use_transformers and self.sentence_model:
            texts = [self.get_article_text(a) for a in articles]
            X = self.sentence_model.encode(texts)
        else:
            texts = [self.get_article_text(a) for a in articles]
            X = self.tfidf_vectorizer.fit_transform(texts).toarray()
        
        # Run DBSCAN
        clustering = DBSCAN(
            eps=1-threshold,  # Convert similarity to distance
            min_samples=min_articles,
            metric='cosine'
        ).fit(X)
        
        # Group articles by cluster
        clusters = defaultdict(list)
        for idx, label in enumerate(clustering.labels_):
            if label != -1:  # -1 means noise/unclustered
                clusters[label].append(articles[idx])
        
        return list(clusters.values())
    
    def build_agglomerative_clusters(
        self,
        articles: List[Article],
        threshold: float,
        min_articles: int,
        use_transformers: bool
    ) -> List[List[Article]]:
        """Build clusters using Agglomerative Clustering."""
        
        # Get feature vectors
        if use_transformers and self.sentence_model:
            texts = [self.get_article_text(a) for a in articles]
            X = self.sentence_model.encode(texts)
        else:
            texts = [self.get_article_text(a) for a in articles]
            X = self.tfidf_vectorizer.fit_transform(texts).toarray()
        
        # Run Agglomerative Clustering
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=1-threshold,
            linkage='average'
        ).fit(X)
        
        # Group articles by cluster
        clusters = defaultdict(list)
        for idx, label in enumerate(clustering.labels_):
            clusters[label].append(articles[idx])
        
        # Filter out small clusters
        return [c for c in clusters.values() if len(c) >= min_articles]
    
    def calculate_similarity_matrix(
        self,
        articles: List[Article],
        use_transformers: bool
    ) -> np.ndarray:
        """Calculate pairwise similarity matrix for articles."""
        
        # Prepare texts
        texts = [self.get_article_text(article) for article in articles]
        
        if use_transformers and self.sentence_model:
            # Use sentence transformers for semantic similarity
            self.stdout.write("Encoding articles with sentence transformer...")
            embeddings = self.sentence_model.encode(texts, show_progress_bar=True)
            similarity_matrix = cosine_similarity(embeddings)
        else:
            # Use TF-IDF
            self.stdout.write("Calculating TF-IDF vectors...")
            tfidf_matrix = self.tfidf_vectorizer.fit_transform(texts)
            similarity_matrix = cosine_similarity(tfidf_matrix)
        
        # Boost similarity for articles from same source or with similar titles
        for i in range(len(articles)):
            for j in range(i+1, len(articles)):
                # Title similarity boost
                title_sim = self.calculate_title_similarity(
                    articles[i].title,
                    articles[j].title
                )
                
                # Weighted combination
                base_sim = similarity_matrix[i][j]
                combined_sim = base_sim * 0.7 + title_sim * 0.3
                
                # Same source penalty (to avoid clustering same-source articles)
                if articles[i].feed.website_id == articles[j].feed.website_id:
                    combined_sim *= 0.8
                
                similarity_matrix[i][j] = combined_sim
                similarity_matrix[j][i] = combined_sim
        
        return similarity_matrix
    
    def get_article_text(self, article: Article) -> str:
        """Get combined text representation of an article."""
        parts = []
        
        # Title is most important
        if article.title:
            parts.append(article.title)
            parts.append(article.title)  # Double weight for title
        
        # Add summary or content
        if article.summary:
            parts.append(article.summary)
        elif article.content:
            # Use first 1000 chars of content if no summary
            parts.append(article.content[:1000])
        
        return ' '.join(parts)
    
    def calculate_title_similarity(self, title1: str, title2: str) -> float:
        """Calculate similarity between two titles."""
        if not title1 or not title2:
            return 0.0
        
        # Normalize
        t1 = self.normalize_text(title1)
        t2 = self.normalize_text(title2)
        
        # Exact match
        if t1 == t2:
            return 1.0
        
        # Word overlap (Jaccard similarity)
        words1 = set(t1.split())
        words2 = set(t2.split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1.intersection(words2))
        union = len(words1.union(words2))
        
        return intersection / union if union > 0 else 0.0
    
    def normalize_text(self, text: str) -> str:
        """Normalize text for comparison."""
        if not text:
            return ""
        
        # Lowercase
        text = text.lower()
        
        # Remove URLs
        text = re.sub(r'https?://\S+|www\.\S+', '', text)
        
        # Remove special characters
        text = re.sub(r'[^\w\s]', ' ', text)
        
        # Remove extra spaces
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()
    
    def create_cluster_from_articles(self, articles: List[Article]) -> ArticleCluster:
        """Create a cluster from a list of articles."""
        
        if len(articles) < 2:
            return None
        
        # Check if any articles are already clustered
        existing = ArticleCluster.objects.filter(
            articles__in=[a.id for a in articles]
        ).first()
        
        if existing:
            return None
        
        # Extract common themes from titles
        all_titles = ' '.join([a.title for a in articles if a.title])
        
        # Find most common meaningful words
        words = self.normalize_text(all_titles).split()
        word_counts = defaultdict(int)
        
        stopwords = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
            'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
            'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need',
            'that', 'this', 'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we',
            'they', 'what', 'which', 'who', 'when', 'where', 'why', 'how', 'not',
            'no', 'nor', 'so', 'than', 'too', 'very', 's', 't', 'just', 'don', 'now'
        }
        
        for word in words:
            if len(word) > 3 and word not in stopwords:
                word_counts[word] += 1
        
        # Get top words for cluster title
        top_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Generate cluster title
        if top_words:
            cluster_title = ' '.join([w[0].title() for w in top_words[:3]])
            cluster_title = f"Cluster: {cluster_title}"
        else:
            cluster_title = f"Article Cluster ({len(articles)} articles)"
        
        # Generate description
        sources = set()
        for article in articles:
            if article.feed and article.feed.website:
                sources.add(article.feed.website.name)
        
        description = f"Related articles from {len(sources)} sources covering similar topics"
        
        # Detect event type based on content patterns
        event_type = 'ongoing_story'
        title_lower = all_titles.lower()
        
        if any(word in title_lower for word in ['breaking', 'alert', 'urgent', 'just in']):
            event_type = 'breaking_news'
        elif any(word in title_lower for word in ['announce', 'reveal', 'launch', 'introduce']):
            event_type = 'announcement'
        elif any(word in title_lower for word in ['crash', 'accident', 'attack', 'incident']):
            event_type = 'incident'
        
        # Extract entities (simple approach)
        entities = {
            'people': [],
            'organizations': [],
            'locations': []
        }
        
        # Simple entity extraction based on capitalization patterns
        for article in articles[:5]:  # Sample first 5 articles
            if article.title:
                # Find capitalized words (potential entities)
                words = article.title.split()
                for i, word in enumerate(words):
                    if word[0].isupper() and len(word) > 2:
                        # Skip common title words
                        if word.lower() not in stopwords:
                            # Simple heuristic: consecutive capitalized words might be names
                            if i > 0 and words[i-1][0].isupper():
                                potential_name = f"{words[i-1]} {word}"
                                if potential_name not in entities['people']:
                                    entities['people'].append(potential_name)
        
        # Limit entities
        for key in entities:
            entities[key] = entities[key][:5]
        
        try:
            # Create the cluster
            cluster = ArticleCluster.objects.create(
                title=cluster_title[:200],
                description=description,
                event_type=event_type,
                main_topics=list(top_words[:3]) if top_words else [],
                key_entities=entities,
                confidence_score=0.75  # Default confidence for local clustering
            )
            
            # Add articles
            cluster.articles.set(articles)
            
            # Update metadata
            cluster.update_metadata()
            
            return cluster
            
        except Exception as e:
            logger.error(f"Error creating cluster: {e}")
            return None