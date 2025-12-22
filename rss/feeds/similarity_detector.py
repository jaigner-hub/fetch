"""
Advanced Similarity Detection for Articles

This module provides improved algorithms for finding similar and duplicate articles
using multiple similarity metrics and efficient database queries.
"""

import hashlib
import re
from typing import List, Tuple, Dict, Optional
from difflib import SequenceMatcher
from collections import Counter
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


class SimilarityDetector:
    """Advanced similarity detection for articles."""
    
    def __init__(self):
        """Initialize the similarity detector with optimized settings."""
        # TF-IDF vectorizer with better parameters
        self.tfidf_vectorizer = TfidfVectorizer(
            max_features=2000,  # Increased for better coverage
            stop_words='english',
            ngram_range=(1, 3),  # Include trigrams for better phrase matching
            min_df=1,
            max_df=0.95,
            use_idf=True,
            smooth_idf=True,
            sublinear_tf=True  # Use log normalization
        )
        
        # Title vectorizer (separate for title comparison)
        self.title_vectorizer = TfidfVectorizer(
            max_features=100,
            stop_words='english',
            ngram_range=(1, 2),
            min_df=1
        )
    
    def normalize_text(self, text: str) -> str:
        """Normalize text for comparison."""
        if not text:
            return ""
        
        # Convert to lowercase
        text = text.lower()
        
        # Remove URLs
        text = re.sub(r'https?://\S+|www\.\S+', '', text)
        
        # Remove email addresses
        text = re.sub(r'\S+@\S+', '', text)
        
        # Remove special characters but keep spaces
        text = re.sub(r'[^\w\s]', ' ', text)
        
        # Remove multiple spaces
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()
    
    def calculate_content_hash(self, title: str, content: str) -> str:
        """Calculate a hash for content deduplication."""
        # Normalize and combine title and content
        normalized = self.normalize_text(f"{title} {content}")
        
        # Remove all whitespace for hash
        normalized = ''.join(normalized.split())
        
        # Calculate SHA256 hash
        return hashlib.sha256(normalized.encode()).hexdigest()
    
    def calculate_simhash(self, text: str, hashbits: int = 64) -> int:
        """
        Calculate SimHash for near-duplicate detection.
        SimHash is locality-sensitive, meaning similar texts produce similar hashes.
        """
        if not text:
            return 0
        
        # Tokenize
        tokens = text.lower().split()
        
        # Initialize hash vector
        v = [0] * hashbits
        
        for token in tokens:
            # Hash each token
            token_hash = int(hashlib.md5(token.encode()).hexdigest(), 16)
            
            # Update vector
            for i in range(hashbits):
                bit = (token_hash >> i) & 1
                if bit:
                    v[i] += 1
                else:
                    v[i] -= 1
        
        # Generate final hash
        simhash = 0
        for i in range(hashbits):
            if v[i] > 0:
                simhash |= (1 << i)
        
        return simhash
    
    def hamming_distance(self, hash1: int, hash2: int) -> int:
        """Calculate Hamming distance between two hashes."""
        xor = hash1 ^ hash2
        distance = 0
        while xor:
            distance += xor & 1
            xor >>= 1
        return distance
    
    def title_similarity(self, title1: str, title2: str) -> float:
        """
        Calculate similarity between two titles using multiple methods.
        """
        if not title1 or not title2:
            return 0.0
        
        # Normalize titles
        t1 = self.normalize_text(title1)
        t2 = self.normalize_text(title2)
        
        # Method 1: Exact match (after normalization)
        if t1 == t2:
            return 1.0
        
        # Method 2: Sequence matching (good for slight variations)
        seq_similarity = SequenceMatcher(None, t1, t2).ratio()
        
        # Method 3: Word overlap (Jaccard similarity)
        words1 = set(t1.split())
        words2 = set(t2.split())
        if words1 or words2:
            jaccard = len(words1.intersection(words2)) / len(words1.union(words2))
        else:
            jaccard = 0.0
        
        # Weighted average
        return (seq_similarity * 0.6) + (jaccard * 0.4)
    
    def content_similarity(self, content1: str, content2: str) -> float:
        """
        Calculate content similarity using TF-IDF and cosine similarity.
        """
        if not content1 or not content2:
            return 0.0
        
        try:
            # Normalize content
            c1 = self.normalize_text(content1)
            c2 = self.normalize_text(content2)
            
            # Quick check for very short content
            if len(c1) < 50 or len(c2) < 50:
                return SequenceMatcher(None, c1, c2).ratio()
            
            # Use TF-IDF for longer content
            tfidf_matrix = self.tfidf_vectorizer.fit_transform([c1, c2])
            similarity = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
            
            return float(similarity)
            
        except Exception as e:
            logger.warning(f"Error calculating content similarity: {e}")
            return 0.0
    
    def find_similar_articles(
        self,
        article,
        threshold: float = 0.7,
        max_results: int = 20,
        days_back: int = 30,
        check_all_time: bool = False
    ) -> List[Tuple['Article', Dict[str, float]]]:
        """
        Find similar articles using multiple similarity metrics.
        
        Args:
            article: Article model instance to compare
            threshold: Overall similarity threshold (0-1)
            max_results: Maximum number of results to return
            days_back: How many days back to search
            check_all_time: If True, search all articles (slower)
            
        Returns:
            List of tuples (similar_article, scores_dict)
        """
        from .models import Article
        
        # Prepare source article data
        source_title = article.title
        source_content = article.content or article.summary or ""
        source_simhash = self.calculate_simhash(source_title + " " + source_content)
        
        # Build query for candidate articles
        query = Article.objects.exclude(id=article.id)
        
        if not check_all_time:
            # Limit to recent articles for performance
            cutoff_date = timezone.now() - timedelta(days=days_back)
            query = query.filter(published_date__gte=cutoff_date)
        
        # First pass: Get candidates with similar titles or from same website
        candidates = []
        
        # Check articles from the same website first (likely to have similar content)
        same_site = query.filter(feed__website=article.feed.website)[:100]
        candidates.extend(same_site)
        
        # Check articles with similar titles
        if source_title:
            # Split title into significant words
            title_words = set(self.normalize_text(source_title).split())
            title_words = {w for w in title_words if len(w) > 3}  # Skip short words
            
            if title_words:
                # Build Q objects for title matching
                title_q = Q()
                for word in list(title_words)[:5]:  # Limit to 5 most important words
                    title_q |= Q(title__icontains=word)
                
                similar_titles = query.filter(title_q)[:100]
                candidates.extend(similar_titles)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_candidates = []
        for article in candidates:
            if article.id not in seen:
                seen.add(article.id)
                unique_candidates.append(article)
        
        # Calculate similarities for all candidates
        results = []
        
        for candidate in unique_candidates:
            scores = {}
            
            # Title similarity
            scores['title'] = self.title_similarity(source_title, candidate.title)
            
            # Content similarity
            candidate_content = candidate.content or candidate.summary or ""
            if source_content and candidate_content:
                scores['content'] = self.content_similarity(source_content, candidate_content)
            else:
                scores['content'] = 0.0
            
            # SimHash similarity (for near-duplicates)
            candidate_simhash = self.calculate_simhash(candidate.title + " " + candidate_content)
            hamming_dist = self.hamming_distance(source_simhash, candidate_simhash)
            # Convert Hamming distance to similarity (64 bits max)
            scores['simhash'] = 1.0 - (hamming_dist / 64.0)
            
            # Check if same website
            scores['same_site'] = 1.0 if candidate.feed.website_id == article.feed.website_id else 0.0
            
            # Calculate weighted overall score
            # Adjust weights based on your needs
            if scores['content'] > 0:
                overall = (
                    scores['title'] * 0.3 +
                    scores['content'] * 0.5 +
                    scores['simhash'] * 0.15 +
                    scores['same_site'] * 0.05
                )
            else:
                # If no content, rely more on title
                overall = (
                    scores['title'] * 0.7 +
                    scores['simhash'] * 0.2 +
                    scores['same_site'] * 0.1
                )
            
            scores['overall'] = overall
            
            # Only include if above threshold
            if overall >= threshold:
                results.append((candidate, scores))
        
        # Sort by overall score
        results.sort(key=lambda x: x[1]['overall'], reverse=True)
        
        return results[:max_results]
    
    def find_exact_duplicates(self, article) -> List['Article']:
        """
        Find exact duplicates based on content hash.
        """
        from .models import Article
        
        # Calculate hash for this article
        content_hash = self.calculate_content_hash(article.title, article.content)
        
        # Find articles with the same hash
        duplicates = Article.objects.exclude(
            id=article.id
        ).filter(
            content_hash=content_hash
        )
        
        return list(duplicates)
    
    def find_near_duplicates(
        self,
        article,
        max_hamming_distance: int = 5
    ) -> List[Tuple['Article', int]]:
        """
        Find near-duplicates using SimHash.
        
        Args:
            article: Article to check
            max_hamming_distance: Maximum Hamming distance for near-duplicates
            
        Returns:
            List of (article, hamming_distance) tuples
        """
        from .models import Article
        
        source_text = article.title + " " + (article.content or article.summary or "")
        source_simhash = self.calculate_simhash(source_text)
        
        # Get recent articles
        cutoff_date = timezone.now() - timedelta(days=7)
        candidates = Article.objects.exclude(
            id=article.id
        ).filter(
            published_date__gte=cutoff_date
        )[:500]  # Limit for performance
        
        near_duplicates = []
        
        for candidate in candidates:
            candidate_text = candidate.title + " " + (candidate.content or candidate.summary or "")
            candidate_simhash = self.calculate_simhash(candidate_text)
            
            distance = self.hamming_distance(source_simhash, candidate_simhash)
            
            if distance <= max_hamming_distance:
                near_duplicates.append((candidate, distance))
        
        # Sort by distance (closest first)
        near_duplicates.sort(key=lambda x: x[1])
        
        return near_duplicates
    
    def bulk_find_duplicates(
        self,
        days_back: int = 7,
        similarity_threshold: float = 0.85
    ) -> Dict[int, List[int]]:
        """
        Find all duplicate groups in recent articles.
        
        Returns:
            Dictionary mapping article IDs to lists of duplicate article IDs
        """
        from .models import Article
        
        cutoff_date = timezone.now() - timedelta(days=days_back)
        articles = Article.objects.filter(
            published_date__gte=cutoff_date
        ).order_by('-published_date')[:1000]
        
        # Build similarity matrix
        duplicate_groups = {}
        processed = set()
        
        for article in articles:
            if article.id in processed:
                continue
            
            similar = self.find_similar_articles(
                article,
                threshold=similarity_threshold,
                days_back=days_back
            )
            
            if similar:
                duplicate_ids = [s[0].id for s in similar]
                duplicate_groups[article.id] = duplicate_ids
                processed.add(article.id)
                processed.update(duplicate_ids)
        
        return duplicate_groups