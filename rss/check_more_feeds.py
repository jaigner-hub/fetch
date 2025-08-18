import requests
from urllib.parse import urljoin

base_url = "https://www.hollywoodreporter.com"

# Test more subcategories and sections
subcategories = [
    # Movies subcategories
    "movies/movie-news", "movies/movie-reviews", "movies/movie-features",
    # TV subcategories  
    "tv/tv-news", "tv/tv-reviews", "tv/tv-features",
    # Business subcategories
    "business/business-news", "business/digital",
    # Lifestyle subcategories
    "lifestyle/lifestyle-news", "lifestyle/style", "lifestyle/arts",
    # Other sections
    "politics", "international", "asia", "video", "lists",
    # Special sections
    "t/netflix", "t/disney", "t/warner-bros", "t/amazon", "t/paramount"
]

valid_feeds = []

for subcategory in subcategories:
    for suffix in ["/feed", "/rss"]:
        url = urljoin(base_url, subcategory + suffix)
        try:
            response = requests.head(url, timeout=5, allow_redirects=True)
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '').lower()
                if 'xml' in content_type or 'rss' in content_type or 'atom' in content_type:
                    valid_feeds.append(url)
                    print(f"✓ Valid feed: {url}")
        except:
            pass

print(f"\nFound {len(valid_feeds)} additional valid feeds")
