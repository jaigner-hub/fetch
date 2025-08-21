# Article Aggregator System - Complete Project Guidelines

## Project Overview
This is an advanced article aggregation system that monitors news and content-rich websites to automatically discover and harvest new articles. The system intelligently identifies RSS feeds AND XML sitemaps from any domain, continuously monitors them for new content articles (not just any pages), and ingests the full article content with rich media for future processing and content generation.

## Core Mission
**Phase 1: Article Discovery & Ingestion**
- Discover RSS feeds and XML sitemaps from any given domain
- Continuously monitor for NEW article content (not general pages)
- Fetch and store complete article content with all rich media
- Structure and clean data for downstream processing

**Phase 2: Article Clustering & Analysis**
- Identify articles covering the same topic/event within 48-hour windows
- Group related articles from multiple sources
- Extract common themes, entities, and narratives

**Phase 3: Content Generation**
- Generate new articles using multiple related sources as input
- Combine perspectives from different outlets on the same story
- Create unique, synthesized content from aggregated sources

## System Architecture

### Key Components

#### 1. Feed & Sitemap Discovery
- **RSS/Atom Discovery**: Find all RSS and Atom feeds from a website
- **Sitemap Discovery**: Locate and parse XML sitemaps (including nested sitemap indexes)
- **Article URL Detection**: Identify which URLs in sitemaps are actual articles vs other pages
- **Claude AI Integration**: Use AI to intelligently discover feeds when traditional methods fail

#### 2. Content Monitoring & Filtering
- **Smart Polling**: Adaptive intervals based on site update frequency
- **Article Detection**: Filter sitemap URLs to only fetch actual news/content articles
- **Deduplication**: Handle articles appearing in multiple feeds/sitemaps
- **New Content Detection**: Track what's already been fetched to avoid duplicates

#### 3. Article Ingestion Pipeline
- **Full HTML Fetch**: Download complete article HTML
- **Content Extraction**: Clean extraction of main article text (remove ads, navigation, etc.)
- **Metadata Parsing**: Extract all available metadata:
  - Title, author, publish date, update date
  - Categories, tags, keywords
  - OpenGraph, JSON-LD, meta tags
- **Rich Media Handling**:
  - Download and store hero/featured images
  - Extract and store all embedded images
  - Capture video URLs and thumbnails
  - Prepare media for S3 bucket upload

#### 4. Similarity Detection & Clustering
- **48-Hour Windows**: Group articles published within same time period
- **Multi-Metric Similarity**:
  - Title similarity
  - Content overlap
  - Entity matching (people, companies, locations)
  - Topic/category alignment
- **Event Detection**: Identify when multiple outlets cover the same event/story

#### 5. Content Generation Pipeline
- **Source Selection**: Choose related articles for synthesis
- **Multi-Source Analysis**: Extract key facts from each source
- **Content Creation**: Generate new article combining multiple perspectives
- **Media Selection**: Choose best images/videos from source articles
- **Attribution**: Track and credit all source articles

### Database Schema

#### Core Models
- **Website**: Base domains to monitor
  - URL, name, active status
  - Auto-fetch settings and intervals
  - Last fetch timestamps

- **Feed**: RSS/Atom feeds and sitemaps
  - Feed URL and type (RSS/ATOM/SITEMAP)
  - Title, description
  - Error tracking and retry logic
  - Last successful fetch

- **Article**: Ingested content
  - URL (unique), title, full content
  - Cleaned text, summary
  - Author, published date
  - Content hash for duplicate detection
  - Media assets (images, videos)
  - Cross-feed tracking (M2M)

- **ArticleAnalysis**: AI analysis results
  - AI-generated summary
  - Topics, entities, sentiment
  - SEO keywords
  - Similar articles (M2M)
  - Duplicate detection

- **GeneratedContent**: New synthesized articles
  - Generated title, content, summary
  - Source articles used (M2M)
  - Media items selected
  - Generation parameters
  - Publishing status

## Critical Implementation Details

### Article Detection from Sitemaps
Sitemaps contain ALL pages, not just articles. We must filter for actual content:

#### URL Pattern Indicators (Articles):
- Contains date patterns: `/2024/08/`, `/2024-08-20/`
- News/article paths: `/news/`, `/article/`, `/story/`, `/post/`
- Has article ID: `/p/12345`, `/-12345`, `/12345-title`
- Category paths: `/movies/`, `/tv/`, `/entertainment/`

#### URL Pattern Exclusions (Not Articles):
- Static pages: `/about`, `/contact`, `/privacy`, `/terms`
- Category listings: `/category/`, `/tag/`, `/author/`
- Archives: `/archive/`, `/page/2`
- Media galleries: `/gallery/`, `/photos/`

#### Content-Type Verification:
Before full fetch, check:
- HEAD request for content-type
- Check meta tags for article indicators
- Verify OpenGraph type (article, news)

### Media Storage Strategy

#### Image Handling:
1. Extract all image URLs from article
2. Download images locally to `/media/images/{domain}/{article_id}/`
3. Store metadata: original URL, alt text, caption
4. Generate thumbnails for hero images
5. Prepare manifest for S3 batch upload

#### Video Handling:
1. Extract video URLs and embed codes
2. Download video thumbnails
3. Store video metadata and duration
4. Prepare for CDN/streaming integration

### Scheduling & Performance

#### Fetch Intervals by Website Type:
- **High-frequency news**: Every 15-30 minutes
- **Daily publishers**: Every 2-4 hours
- **Weekly content**: Every 6-12 hours
- **Low-activity sites**: Daily

#### Parallel Processing:
- Use Celery workers for concurrent fetching
- Batch process similar operations
- Rate limit per domain to avoid blocking

#### Resource Management:
- Cache feed metadata to reduce requests
- Store robots.txt rules per domain
- Implement exponential backoff for failures
- Respect 429 rate limit responses

## Operational Workflows

### Adding a New Website
```python
# 1. Create website entry
website = Website.objects.create(
    url="https://example.com",
    name="Example News",
    auto_fetch_enabled=True,
    fetch_interval_minutes=60
)

# 2. Discover feeds and sitemaps
discover_feeds_for_website.delay(website.id)

# 3. Initial content fetch
fetch_all_website_content.delay(website.id)

# 4. Schedule recurring fetches
# (handled by Celery beat schedule)
```

### Processing Sitemap URLs
```python
# For each URL in sitemap:
1. Check if URL matches article patterns
2. Verify not in exclusion patterns
3. Check if already in database
4. Fetch page metadata (HEAD request)
5. If article detected:
   - Fetch full content
   - Extract and clean text
   - Download media assets
   - Store in database
```

### Finding Related Articles (48-Hour Window)
```python
# For new article:
1. Get publish date
2. Query articles within ±24 hours
3. Calculate similarity scores:
   - Title similarity (TF-IDF)
   - Content overlap (cosine similarity)
   - Entity matching (people, orgs, locations)
   - Category/topic alignment
4. Group articles with similarity > threshold
5. Mark as related for content generation
```

### Generating New Content
```python
# For article cluster:
1. Select 3-5 most relevant sources
2. Extract key facts from each
3. Identify unique angles/perspectives
4. Generate unified narrative
5. Select best media from sources
6. Create attribution links
7. Store as GeneratedContent
```

## Management Commands

### Essential Commands
- `discover_feeds <url>` - Find all feeds/sitemaps for a website
- `discover_feeds_claude <url>` - Use Claude AI for intelligent discovery
- `check_new_content` - Check all active feeds for new articles
- `fetch_all_website_feeds <website_name>` - Fetch all content for a website
- `find_similar_articles --hours 48` - Find related articles in time window
- `generate_content --cluster-id <id>` - Generate new article from cluster
- `download_media --article-id <id>` - Download all media for an article
- `check_scheduled` - Manually trigger scheduled fetch checks

### Monitoring Commands
- `feed_status` - Show health of all feeds
- `article_stats --days 7` - Article ingestion statistics
- `similarity_report` - Show article clusters found
- `media_storage_stats` - Media storage usage

## Error Handling & Recovery

### Feed Failures
- After 5 consecutive failures, mark feed as inactive
- Log detailed error messages for debugging
- Implement exponential backoff between retries
- Send alerts for critical feed failures

### Content Extraction Failures
- Store raw HTML for manual review
- Flag articles with extraction issues
- Implement fallback extraction methods
- Use AI for difficult content extraction

### Media Download Failures
- Retry with different user agents
- Store media URL for later retry
- Mark media as unavailable after max retries
- Use placeholder images when needed

## Security & Compliance

### Rate Limiting
- Respect robots.txt directives
- Implement per-domain rate limits
- Use polite crawling intervals
- Handle 429 responses appropriately

### Content Storage
- Sanitize all HTML before storage
- Validate URLs to prevent SSRF
- Implement request timeouts
- Store content hashes for integrity

### Attribution
- Always store source URLs
- Maintain publication timestamps
- Credit original authors
- Respect copyright notices

## Performance Optimizations

### Database
- Index on published_date for time windows
- Index on content_hash for deduplication
- Composite index on (feed_id, published_date)
- Full-text search indexes on content

### Caching
- Cache feed parsing results
- Cache similarity calculations
- Cache extracted metadata
- Redis for temporary data

### Parallel Processing
- Concurrent feed fetching
- Parallel content extraction
- Batch media downloads
- Distributed similarity computation

## Future Enhancements

### Phase 1 Improvements
- WebSocket monitoring for real-time updates
- Browser automation for JavaScript-heavy sites
- API integration for major publishers
- Smart feed discovery from navigation

### Phase 2 Expansions
- Natural language understanding
- Event timeline construction
- Fact extraction and verification
- Sentiment trajectory tracking

### Phase 3 Evolution
- Multi-language content generation
- Video content synthesis
- Podcast generation from articles
- Interactive content formats

## Success Metrics

### Ingestion Metrics
- Articles per day per website
- Successful fetch percentage
- Average content extraction quality
- Media download success rate

### Clustering Metrics
- Related articles found per day
- Average cluster size
- Topic coverage breadth
- Time to cluster detection

### Generation Metrics
- Articles generated per day
- Source diversity per article
- Content uniqueness score
- Reader engagement metrics

## Server Management

### Restarting the Application
The application runs under Apache web server. To apply changes:
```bash
sudo systemctl restart apache2
```

**Note:** Do NOT use `python manage.py runserver` - the production server is Apache with mod_wsgi.

## Debugging Guide

### No New Content Issues
1. Check Celery workers are running
2. Verify Celery beat is active
3. Check website fetch intervals
4. Review feed error logs
5. Verify article detection patterns
6. Check for rate limiting

### Sitemap Processing Issues
1. Verify sitemap is valid XML
2. Check for nested sitemap indexes
3. Verify article URL patterns
4. Review exclusion patterns
5. Check content-type detection

### Similarity Not Working
1. Verify time window queries
2. Check TF-IDF vectorization
3. Review similarity thresholds
4. Verify article content quality
5. Check for empty content fields

This system is designed to be a robust, scalable article aggregation platform that not only collects content but intelligently processes and synthesizes it for creating new, valuable content from multiple sources.