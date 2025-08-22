"""
Local clustering utilities for article grouping without API calls.
Uses TF-IDF, word embeddings, and named entity recognition.
"""

import re
import hashlib
from typing import List, Dict, Set, Tuple, Optional
from collections import defaultdict, Counter
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.decomposition import TruncatedSVD
from datetime import timedelta
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


class LocalClusterBuilder:
    """Builds article clusters using local NLP techniques."""
    
    def __init__(self, 
                 use_sentence_transformers: bool = False,
                 use_transformers: bool = False,  # Alias for compatibility
                 similarity_threshold: float = 0.65,
                 min_cluster_size: int = 3,
                 verbose: bool = False):
        """
        Initialize the cluster builder.
        
        Args:
            use_sentence_transformers: Whether to use sentence transformers for embeddings
            use_transformers: Alias for use_sentence_transformers
            similarity_threshold: Default similarity threshold for clustering
            min_cluster_size: Default minimum cluster size
            verbose: Whether to print verbose output
        """
        # Handle both parameter names
        self.use_sentence_transformers = use_sentence_transformers or use_transformers
        self.similarity_threshold = similarity_threshold
        self.min_cluster_size = min_cluster_size
        self.verbose = verbose
        self.sentence_model = None
        
        # Initialize TF-IDF vectorizer with optimized settings for speed
        self.tfidf_vectorizer = TfidfVectorizer(
            max_features=1000,  # Reduced for speed
            stop_words='english',
            ngram_range=(1, 2),  # Reduced n-gram range
            min_df=2,  # Increased min_df
            max_df=0.9,
            use_idf=True,
            smooth_idf=True,
            sublinear_tf=True,
            strip_accents='unicode',
            token_pattern=r'\b[a-zA-Z][a-zA-Z]+\b'  # Only words with 2+ letters
        )
        
        # Title-specific vectorizer (more focused)
        self.title_vectorizer = TfidfVectorizer(
            max_features=500,
            stop_words='english',
            ngram_range=(1, 2),
            min_df=1,
            strip_accents='unicode'
        )
        
        # Load sentence transformer if requested
        if use_sentence_transformers:
            try:
                from sentence_transformers import SentenceTransformer
                # Use a small, fast model
                self.sentence_model = SentenceTransformer('all-MiniLM-L6-v2')
                logger.info("Loaded sentence transformer model")
            except (ImportError, OSError) as e:
                logger.warning(f"Could not load sentence-transformers: {e}")
                self.use_sentence_transformers = False
        
        # Entity patterns
        self.entity_patterns = {
            'person': re.compile(r'\b([A-Z][a-z]+ [A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b'),
            'organization': re.compile(r'\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*(?:\s+(?:Inc|Corp|Ltd|LLC|Co|Company|Group|Foundation|Association|Organization|Institute|University|College|School|Hospital|Bank|Agency|Department|Ministry|Commission|Committee|Council|Board|Authority|Office)\.?))\b'),
            'location': re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,?\s+(?:[A-Z][a-z]+\s+)?(?:USA|UK|US|Canada|Australia|Germany|France|Italy|Spain|China|Japan|India|Brazil|Mexico|Russia|South Korea|North Korea|Saudi Arabia|UAE|Israel|Iran|Iraq|Syria|Egypt|Turkey|Greece|Poland|Netherlands|Belgium|Switzerland|Austria|Sweden|Norway|Denmark|Finland))\b'),
            'date': re.compile(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b'),
            'money': re.compile(r'\$[\d,]+(?:\.\d{2})?(?:\s*(?:billion|million|thousand|hundred))?|\b\d+(?:\.\d+)?\s*(?:billion|million|thousand|hundred)?\s*(?:dollars|euros|pounds|yen|yuan)\b', re.IGNORECASE),
            'percentage': re.compile(r'\b\d+(?:\.\d+)?%\b')
        }
    
    def build_clusters(
        self,
        articles: List,
        min_cluster_size: int = 3,
        similarity_threshold: float = 0.65,
        time_window_hours: int = 48,
        method: str = 'hybrid'
    ) -> List[Dict]:
        """
        Build clusters from articles using local methods.
        
        Args:
            articles: List of Article objects
            min_cluster_size: Minimum articles per cluster
            similarity_threshold: Minimum similarity for clustering
            time_window_hours: Time window for clustering
            method: Clustering method ('tfidf', 'embedding', 'hybrid')
            
        Returns:
            List of cluster dictionaries
        """
        if len(articles) < min_cluster_size:
            return []
        
        # Calculate similarity matrix based on method
        if method == 'tfidf':
            similarity_matrix = self._calculate_tfidf_similarity(articles)
        elif method == 'embedding' and self.sentence_model:
            similarity_matrix = self._calculate_embedding_similarity(articles)
        else:  # hybrid
            similarity_matrix = self._calculate_hybrid_similarity(articles)
        
        # Build clusters using greedy approach
        clusters = self._greedy_clustering(
            articles,
            similarity_matrix,
            similarity_threshold,
            min_cluster_size
        )
        
        # Return just the clusters (lists of articles)
        # The enhancement will be done when creating the cluster object
        return clusters
    
    def _calculate_tfidf_similarity(self, articles: List) -> np.ndarray:
        """Calculate TF-IDF based similarity matrix."""
        # Prepare texts
        texts = []
        for article in articles:
            text = self._prepare_article_text(article)
            texts.append(text)
        
        # Calculate TF-IDF
        try:
            tfidf_matrix = self.tfidf_vectorizer.fit_transform(texts)
            
            # Apply LSA for dimensionality reduction (speeds up similarity calculation)
            if tfidf_matrix.shape[0] > 100:
                svd = TruncatedSVD(n_components=min(100, tfidf_matrix.shape[0] - 1))
                tfidf_matrix = svd.fit_transform(tfidf_matrix)
            
            # Calculate cosine similarity
            similarity = cosine_similarity(tfidf_matrix)
            
        except Exception as e:
            logger.error(f"Error calculating TF-IDF similarity: {e}")
            # Fallback to simple similarity
            similarity = np.zeros((len(articles), len(articles)))
            for i in range(len(articles)):
                for j in range(i+1, len(articles)):
                    sim = self._simple_similarity(articles[int(i)], articles[int(j)])
                    similarity[i][j] = sim
                    similarity[j][i] = sim
        
        return similarity
    
    def _calculate_embedding_similarity(self, articles: List) -> np.ndarray:
        """Calculate similarity using sentence embeddings."""
        if not self.sentence_model:
            return self._calculate_tfidf_similarity(articles)
        
        # Prepare texts
        texts = []
        for article in articles:
            # For embeddings, use title + first part of content
            text = article.title or ""
            if article.summary:
                text += " " + article.summary[:500]
            elif article.content:
                text += " " + article.content[:500]
            texts.append(text)
        
        # Get embeddings
        try:
            embeddings = self.sentence_model.encode(texts, batch_size=32, show_progress_bar=False)
            similarity = cosine_similarity(embeddings)
        except Exception as e:
            logger.error(f"Error calculating embedding similarity: {e}")
            return self._calculate_tfidf_similarity(articles)
        
        return similarity
    
    def _calculate_hybrid_similarity(self, articles: List) -> np.ndarray:
        """Calculate hybrid similarity combining multiple methods."""
        # Get TF-IDF similarity
        tfidf_sim = self._calculate_tfidf_similarity(articles)
        
        # Get embedding similarity if available
        if self.sentence_model:
            embedding_sim = self._calculate_embedding_similarity(articles)
            # Weighted average
            similarity = tfidf_sim * 0.5 + embedding_sim * 0.5
        else:
            similarity = tfidf_sim
        
        # Boost similarity for articles with similar titles
        for i in range(len(articles)):
            for j in range(i+1, len(articles)):
                title_sim = self._title_similarity(articles[int(i)].title, articles[int(j)].title)
                
                # Strong title match boosts overall similarity
                if title_sim > 0.8:
                    similarity[i][j] = min(1.0, similarity[i][j] * 1.2)
                    similarity[j][i] = similarity[i][j]
                
                # Penalize same-source articles to encourage diversity
                if articles[int(i)].feed.website_id == articles[int(j)].feed.website_id:
                    similarity[i][j] *= 0.7
                    similarity[j][i] = similarity[i][j]
        
        return similarity
    
    def _prepare_article_text(self, article) -> str:
        """Prepare article text for vectorization."""
        parts = []
        
        # Title (most important, add twice)
        if article.title:
            parts.append(article.title)
            parts.append(article.title)
        
        # Summary or content snippet
        if article.summary:
            parts.append(article.summary)
        elif article.content:
            # Use first 1500 chars
            content_snippet = article.content[:1500]
            parts.append(content_snippet)
        
        # Add source name for context
        if article.feed and article.feed.website:
            parts.append(article.feed.website.name)
        
        return ' '.join(parts)
    
    def _simple_similarity(self, article1, article2) -> float:
        """Calculate simple word-based similarity."""
        text1 = self._normalize_text(article1.title + " " + (article1.summary or ""))
        text2 = self._normalize_text(article2.title + " " + (article2.summary or ""))
        
        words1 = set(text1.split())
        words2 = set(text2.split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1.intersection(words2))
        union = len(words1.union(words2))
        
        return intersection / union if union > 0 else 0.0
    
    def _title_similarity(self, title1: str, title2: str) -> float:
        """Calculate title similarity."""
        if not title1 or not title2:
            return 0.0
        
        # Normalize
        t1 = self._normalize_text(title1)
        t2 = self._normalize_text(title2)
        
        # Exact match
        if t1 == t2:
            return 1.0
        
        # Calculate word overlap
        words1 = set(t1.split())
        words2 = set(t2.split())
        
        # Remove common words
        stopwords = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'as', 'is', 'was', 'are', 'were'}
        words1 = {w for w in words1 if w not in stopwords and len(w) > 2}
        words2 = {w for w in words2 if w not in stopwords and len(w) > 2}
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1.intersection(words2))
        smaller_set_size = min(len(words1), len(words2))
        
        # Use smaller set size for normalization (more forgiving)
        return intersection / smaller_set_size if smaller_set_size > 0 else 0.0
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison."""
        if not text:
            return ""
        
        # Lowercase
        text = text.lower()
        
        # Remove URLs
        text = re.sub(r'https?://\S+|www\.\S+', '', text)
        
        # Remove special characters but keep spaces
        text = re.sub(r'[^\w\s]', ' ', text)
        
        # Remove extra spaces
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()
    
    def _greedy_clustering(
        self,
        articles: List,
        similarity_matrix: np.ndarray,
        threshold: float,
        min_size: int
    ) -> List[List]:
        """Perform greedy clustering based on similarity matrix."""
        n = len(articles)
        clusters = []
        used = set()
        
        # Sort articles by average similarity to find best seeds
        avg_similarities = np.mean(similarity_matrix, axis=1)
        sorted_indices = np.argsort(avg_similarities)[::-1]
        
        for seed_idx in sorted_indices:
            if seed_idx in used:
                continue
            
            # Find all articles similar to seed
            cluster_indices = {seed_idx}
            
            # Get similarities for this seed
            similarities = similarity_matrix[seed_idx]
            
            # Add articles above threshold
            for idx in range(n):
                if idx != seed_idx and idx not in used:
                    if similarities[idx] >= threshold:
                        cluster_indices.add(idx)
            
            # Check if cluster is large enough
            if len(cluster_indices) >= min_size:
                cluster = [articles[int(idx)] for idx in cluster_indices]
                clusters.append(cluster)
                used.update(cluster_indices)
        
        return clusters
    
    def _enhance_cluster(self, articles: List) -> Dict:
        """Enhance cluster with metadata and extracted information."""
        # Extract entities from all articles
        all_entities = defaultdict(Counter)
        
        for article in articles:
            entities = self._extract_entities(article)
            for entity_type, entity_list in entities.items():
                for entity in entity_list:
                    all_entities[entity_type][entity] += 1
        
        # Get most common entities
        top_entities = {}
        for entity_type, counter in all_entities.items():
            top_entities[entity_type] = [
                entity for entity, _ in counter.most_common(5)
            ]
        
        # Generate cluster title
        cluster_title = self._generate_cluster_title(articles)
        
        # Determine event type
        event_type = self._detect_event_type(articles)
        
        # Calculate cluster statistics
        sources = set()
        for article in articles:
            if article.feed and article.feed.website:
                sources.add(article.feed.website.name)
        
        # Get time range
        dates = [a.published_date for a in articles if a.published_date]
        if dates:
            min_date = min(dates)
            max_date = max(dates)
            time_span = (max_date - min_date).total_seconds() / 3600  # in hours
        else:
            time_span = 0
        
        return {
            'title': cluster_title,
            'description': f"Cluster of {len(articles)} related articles from {len(sources)} sources",
            'articles': articles,
            'event_type': event_type,
            'entities': top_entities,
            'source_count': len(sources),
            'article_count': len(articles),
            'time_span_hours': time_span,
            'confidence': 0.75  # Default confidence for local clustering
        }
    
    def _extract_entities(self, article) -> Dict[str, List[str]]:
        """Extract named entities from article."""
        entities = defaultdict(list)
        
        # Combine title and content for extraction
        text = article.title or ""
        if article.summary:
            text += " " + article.summary
        elif article.content:
            text += " " + article.content[:1000]
        
        # Extract using patterns
        for entity_type, pattern in self.entity_patterns.items():
            matches = pattern.findall(text)
            entities[entity_type] = list(set(matches))[:10]  # Limit to 10 per type
        
        return dict(entities)
    
    def _generate_cluster_title(self, articles: List) -> str:
        """Generate a descriptive title for the cluster."""
        # Collect all titles
        titles = [a.title for a in articles if a.title]
        
        if not titles:
            return f"Article Cluster ({len(articles)} articles)"
        
        # Find common words across titles
        all_words = []
        for title in titles:
            words = self._normalize_text(title).split()
            all_words.extend(words)
        
        # Count word frequencies
        word_counts = Counter(all_words)
        
        # Remove stopwords
        stopwords = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
            'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
            'says', 'said', 'new', 'after', 'over', 'into', 'about', 'more'
        }
        
        # Get significant words
        significant_words = []
        for word, count in word_counts.most_common(20):
            if word not in stopwords and len(word) > 3 and count >= 2:
                significant_words.append(word)
            if len(significant_words) >= 3:
                break
        
        if significant_words:
            # Capitalize and create title
            title_words = [w.capitalize() for w in significant_words[:3]]
            return f"{' '.join(title_words)} Coverage"
        else:
            # Fallback to using first article's title snippet
            return titles[0][:50] + "..."
    
    def _detect_event_type(self, articles: List) -> str:
        """Detect the type of event based on article content."""
        # Combine all titles for analysis
        all_text = ' '.join([a.title for a in articles if a.title]).lower()
        
        # Check for patterns
        if any(word in all_text for word in ['breaking', 'alert', 'urgent', 'developing']):
            return 'breaking_news'
        elif any(word in all_text for word in ['announce', 'unveil', 'reveal', 'launch', 'introduce']):
            return 'announcement'
        elif any(word in all_text for word in ['crash', 'accident', 'attack', 'shooting', 'explosion']):
            return 'incident'
        elif any(word in all_text for word in ['die', 'death', 'pass away', 'obituary']):
            return 'obituary'
        elif any(word in all_text for word in ['win', 'victory', 'champion', 'defeat', 'score']):
            return 'sports'
        elif any(word in all_text for word in ['stock', 'market', 'trading', 'earnings', 'economy']):
            return 'financial'
        else:
            return 'ongoing_story'
    
    def build_clusters_from_articles(self, articles: List, min_cluster_size: int = None) -> List[List]:
        """
        Build clusters from a list of articles.
        
        Args:
            articles: List of Article objects
            min_cluster_size: Minimum cluster size (uses default if None)
            
        Returns:
            List of article clusters (each cluster is a list of articles)
        """
        if min_cluster_size is None:
            min_cluster_size = self.min_cluster_size
            
        if len(articles) < min_cluster_size:
            return []
        
        # Calculate similarity matrix
        similarity_matrix = self._calculate_hybrid_similarity(articles)
        
        # Build clusters
        clusters = self._greedy_clustering(
            articles,
            similarity_matrix,
            self.similarity_threshold,
            min_cluster_size
        )
        
        return clusters
    
    def create_cluster_from_articles(self, articles: List) -> Optional['ArticleCluster']:
        """
        Create an ArticleCluster object from a list of articles.
        
        Args:
            articles: List of Article objects
            
        Returns:
            ArticleCluster object or None if creation fails
        """
        from feeds.models import ArticleCluster
        
        if len(articles) < 2:
            return None
        
        # Check if any articles are already clustered
        existing = ArticleCluster.objects.filter(
            articles__in=[a.id for a in articles]
        ).first()
        
        if existing:
            if self.verbose:
                logger.info(f"Articles already in cluster: {existing.id}")
            return None
        
        # Get cluster metadata
        cluster_data = self._enhance_cluster(articles)
        
        try:
            # Create the cluster
            cluster = ArticleCluster.objects.create(
                title=cluster_data['title'][:200],
                description=cluster_data['description'],
                event_type=cluster_data['event_type'],
                main_topics=[],  # Will be updated by update_metadata
                key_entities=cluster_data.get('entities', {}),
                confidence_score=cluster_data.get('confidence', 0.75)
            )
            
            # Add articles
            cluster.articles.set(articles)
            
            # Update metadata (calculates source_count, etc.)
            cluster.update_metadata()
            
            return cluster
            
        except Exception as e:
            logger.error(f"Error creating cluster: {e}")
            return None