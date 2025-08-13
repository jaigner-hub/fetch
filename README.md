# RSS Feed Crawler

A Django-based RSS feed crawler that automatically discovers and fetches content from websites.

## Features

- **Automatic Feed Discovery**: Discovers RSS feeds, Atom feeds, and sitemaps from websites
- **Content Fetching**: Fetches and stores articles from discovered feeds
- **Background Processing**: Uses Celery for asynchronous task processing
- **Django Admin Interface**: Manage websites, feeds, and articles through Django admin
- **Deduplication**: Prevents duplicate content using content hashing
- **Error Handling**: Tracks and manages feed errors automatically

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run migrations:
```bash
python manage.py migrate
```

3. Create a superuser:
```bash
python manage.py createsuperuser
```

4. Start Redis (required for Celery):
```bash
redis-server
```

5. Start Celery worker (in a separate terminal):
```bash
celery -A rss worker -l info
```

6. Start Celery Beat for periodic tasks (in another terminal):
```bash
celery -A rss beat -l info
```

7. Run the Django development server:
```bash
python manage.py runserver
```

## Usage

### Management Commands

#### Discover Feeds
Discover RSS feeds and sitemaps for a website:
```bash
python manage.py discover_feeds https://example.com --name "Example Site"
```

Options:
- `--async`: Run discovery asynchronously using Celery

#### Fetch Content
Fetch content from feeds:
```bash
# Fetch from specific feed
python manage.py fetch_content --feed-id 1

# Fetch from all feeds of a website
python manage.py fetch_content --website "Example Site"

# Fetch from all active feeds
python manage.py fetch_content --all
```

Options:
- `--async`: Run fetch asynchronously using Celery

#### Check New Content
Check for recently fetched content:
```bash
python manage.py check_new_content --hours 24
```

Options:
- `--website`: Filter by website name or URL

### Django Admin

Access the Django admin at `/admin/` to:
- Add and manage websites
- View and manage discovered feeds
- Browse fetched articles
- Monitor fetch logs
- Trigger feed discovery and content fetching

### Periodic Tasks

The system automatically runs:
- **Hourly**: Check all active feeds for new content
- **Daily (2 AM)**: Discover new feeds for all active websites

## Database Models

- **Website**: Stores websites to crawl
- **Feed**: Stores discovered RSS feeds and sitemaps
- **Article**: Stores fetched content from feeds
- **FetchLog**: Logs feed fetching activities

## Configuration

Edit `rss/settings.py` to configure:
- Database settings (MySQL by default)
- Celery/Redis settings
- Periodic task schedules

## API Integration

The system provides Python modules for programmatic access:

```python
from feeds.feed_discovery import FeedDiscoverer
from feeds.content_fetcher import ContentFetcher

# Discover feeds
discoverer = FeedDiscoverer('https://example.com')
results = discoverer.discover_all()

# Fetch content
fetcher = ContentFetcher()
content = fetcher.fetch_feed_content('https://example.com/rss')
```

## Troubleshooting

- **No feeds discovered**: Check if the website has RSS feeds in HTML meta tags or common paths
- **Content not fetching**: Ensure Redis is running and Celery workers are active
- **Database errors**: Check MySQL connection settings in settings.py