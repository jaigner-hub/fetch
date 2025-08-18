import requests
from urllib.parse import urljoin

base_url = "https://www.hollywoodreporter.com"

# Test various category feed patterns
categories = [
    "movies", "tv", "business", "tech", "lifestyle", "awards", 
    "news", "features", "reviews", "heat-vision", "live-feed"
]

feed_patterns = [
    "{}/feed",
    "{}/rss", 
    "c/{}/feed",
    "c/{}/rss",
    "category/{}/feed",
    "section/{}/feed"
]

valid_feeds = []

for category in categories:
    for pattern in feed_patterns:
        url = urljoin(base_url, pattern.format(category))
        try:
            response = requests.head(url, timeout=5, allow_redirects=True)
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '').lower()
                if 'xml' in content_type or 'rss' in content_type or 'atom' in content_type:
                    valid_feeds.append(url)
                    print(f"✓ Valid feed: {url}")
        except:
            pass

# Also check the main feeds we know exist
main_feeds = [
    "https://www.hollywoodreporter.com/feed",
    "https://www.hollywoodreporter.com/rss",
    "https://www.hollywoodreporter.com/atom",
    "https://www.hollywoodreporter.com/news/feed",
    "https://www.hollywoodreporter.com/news/rss"
]

for url in main_feeds:
    try:
        response = requests.head(url, timeout=5, allow_redirects=True)
        if response.status_code == 200:
            content_type = response.headers.get('content-type', '').lower()
            if 'xml' in content_type or 'rss' in content_type or 'atom' in content_type:
                if url not in valid_feeds:
                    valid_feeds.append(url)
                    print(f"✓ Valid feed: {url}")
    except:
        pass

print(f"\nFound {len(valid_feeds)} valid feeds")
