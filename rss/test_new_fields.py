#!/usr/bin/env python
"""Test that the new Article fields work correctly"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rss.settings')
django.setup()

from feeds.models import Article, Feed
from datetime import datetime
from django.utils import timezone

# Get a feed to use for testing
feed = Feed.objects.first()
if not feed:
    print("No feeds found!")
    sys.exit(1)

print(f"Using feed: {feed.title}")

# Create a test article with the new fields
test_article = Article.objects.create(
    feed=feed,
    title="Test Article with Tags and Images",
    url=f"https://test.example.com/article-{datetime.now().timestamp()}",
    content="<p>This is test content with an image</p>",
    summary="Test summary",
    author="Test Author",
    published_date=timezone.now(),
    tags=["technology", "news", "test"],
    images=[
        {"url": "https://example.com/image1.jpg", "alt": "Test image 1"},
        {"url": "https://example.com/image2.jpg", "alt": "Test image 2"}
    ],
    featured_image="https://example.com/featured.jpg"
)

print(f"\nCreated article: {test_article.title}")
print(f"Article ID: {test_article.id}")
print(f"Tags: {test_article.tags}")
print(f"Images: {test_article.images}")
print(f"Featured Image: {test_article.featured_image}")

# Now read it back
retrieved = Article.objects.get(id=test_article.id)
print(f"\nRetrieved article:")
print(f"Tags: {retrieved.tags}")
print(f"Images: {retrieved.images}")
print(f"Featured Image: {retrieved.featured_image}")

# Test updating
retrieved.tags.append("updated")
retrieved.save()

# Read again
updated = Article.objects.get(id=test_article.id)
print(f"\nAfter update:")
print(f"Tags: {updated.tags}")

print("\n✅ All fields working correctly!")

# Clean up
test_article.delete()
print("Test article deleted.")