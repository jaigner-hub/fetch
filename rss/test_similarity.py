#!/usr/bin/env python
"""Test the improved similarity detection"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rss.settings')
django.setup()

from feeds.models import Article
from feeds.similarity_detector import SimilarityDetector
from django.utils import timezone
from datetime import timedelta

def test_similarity():
    detector = SimilarityDetector()
    
    # Get a recent article to test with
    recent_date = timezone.now() - timedelta(days=7)
    articles = Article.objects.filter(
        published_date__gte=recent_date,
        content__isnull=False
    ).exclude(content='')[:10]
    
    if not articles:
        print("No recent articles with content found!")
        return
    
    test_article = articles[0]
    print(f"Testing with article: {test_article.title[:80]}")
    print(f"Website: {test_article.feed.website.name}")
    print(f"Published: {test_article.published_date}")
    print("-" * 80)
    
    # Test title similarity
    print("\n=== Testing Title Similarity ===")
    for other in articles[1:4]:
        similarity = detector.title_similarity(test_article.title, other.title)
        print(f"{similarity:.2%} - {other.title[:60]}")
    
    # Test content similarity
    print("\n=== Testing Content Similarity ===")
    if test_article.content:
        for other in articles[1:4]:
            if other.content:
                similarity = detector.content_similarity(test_article.content, other.content)
                print(f"{similarity:.2%} - {other.title[:60]}")
    
    # Test SimHash
    print("\n=== Testing SimHash (Near-Duplicate Detection) ===")
    source_text = test_article.title + " " + (test_article.content or "")
    source_hash = detector.calculate_simhash(source_text)
    print(f"Source SimHash: {source_hash:064b}")
    
    for other in articles[1:4]:
        other_text = other.title + " " + (other.content or "")
        other_hash = detector.calculate_simhash(other_text)
        distance = detector.hamming_distance(source_hash, other_hash)
        print(f"Distance: {distance:2d} - {other.title[:60]}")
    
    # Test finding similar articles
    print("\n=== Finding Similar Articles ===")
    similar = detector.find_similar_articles(
        test_article,
        threshold=0.5,  # Lower threshold to see more results
        max_results=10,
        days_back=30
    )
    
    if similar:
        print(f"Found {len(similar)} similar articles:")
        for article, scores in similar[:5]:
            print(f"\nOverall: {scores['overall']:.2%}")
            print(f"  Title: {article.title[:60]}")
            print(f"  Website: {article.feed.website.name}")
            print(f"  Scores: Title={scores['title']:.2%}, Content={scores['content']:.2%}, SimHash={scores['simhash']:.2%}")
    else:
        print("No similar articles found")
    
    # Test exact duplicate detection
    print("\n=== Testing Exact Duplicate Detection ===")
    exact_dupes = detector.find_exact_duplicates(test_article)
    if exact_dupes:
        print(f"Found {len(exact_dupes)} exact duplicates!")
        for dupe in exact_dupes:
            print(f"  [{dupe.id}] {dupe.title[:60]}")
    else:
        print("No exact duplicates found")
    
    # Test near-duplicate detection
    print("\n=== Testing Near-Duplicate Detection ===")
    near_dupes = detector.find_near_duplicates(test_article, max_hamming_distance=10)
    if near_dupes:
        print(f"Found {len(near_dupes)} near duplicates:")
        for dupe, distance in near_dupes[:5]:
            print(f"  Distance {distance}: [{dupe.id}] {dupe.title[:60]}")
    else:
        print("No near duplicates found")
    
    print("\n" + "=" * 80)
    print("✅ Similarity detection tests complete!")

if __name__ == "__main__":
    test_similarity()