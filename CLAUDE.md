# RSS/XML Feed Aggregator - Project Guidelines

## Project Overview
This is an RSS/XML feed aggregator designed to monitor news websites and automatically harvest new content. The system discovers RSS/XML feeds from any website, continuously monitors them for new articles, and ingests the full content into a database for future processing. The system supports complex news sites with multiple category-specific RSS feeds (like Hollywood Reporter) and handles deduplication when articles appear in multiple feeds.

## Core Functionality
1. **Feed Discovery**: Automatically find RSS/XML feeds from any given website URL
2. **Content Monitoring**: Continuously check feeds for new articles at regular intervals
3. **Article Ingestion**: When new content is detected:
   - Fetch the full HTML of the article
   - Clean and extract the main text content
   - Extract metadata (title, author, publish date, tags, etc.)
   - Store all information in the database

## Technical Architecture

### Key Components
- **Feed Scanner**: Discovers and validates RSS/XML feeds from websites
- **Content Monitor**: Polls feeds at configurable intervals for new content
- **HTML Processor**: Cleans HTML and extracts article text and metadata
- **Database Layer**: Stores feeds, articles, and metadata for future processing

### Database Schema Considerations
- **websites**: Store base website URLs and names
- **feeds**: Store discovered feed URLs, last check time, update frequency, linked to websites
- **articles**: Store article content, cleaned text, metadata, processing status
  - Includes `additional_feeds` M2M field for tracking articles appearing in multiple feeds
- **feed_items**: Track individual feed entries to detect new content
- **metadata**: Store extracted metadata in structured format
- **fetch_logs**: Track feed fetching history and statistics

## Development Guidelines

### When Adding Features
1. Maintain separation between feed discovery, monitoring, and content processing
2. Ensure all scraped content is properly cleaned and sanitized
3. Implement rate limiting to respect source websites
4. Handle errors gracefully (invalid feeds, unreachable sites, parsing failures)

### Testing Requirements
- Test with various RSS versions (RSS 2.0, Atom, RDF)
- Verify HTML cleaning works with different article structures
- Test handling of rate limits and network failures
- Ensure duplicate article detection works correctly

### Performance Considerations
- Implement efficient polling strategies (adaptive intervals based on feed update frequency)
- Use background jobs/workers for content processing
- Cache feed metadata to reduce unnecessary requests
- Batch database operations where possible

## Common Tasks

### Adding a New Feed Source
1. Validate the feed URL is accessible and valid XML
2. Parse feed to determine type and structure
3. Store feed configuration with appropriate polling interval
4. Initialize first content scan

### Adding Multiple Category Feeds for a Website
For sites with many category-specific RSS feeds (e.g., Hollywood Reporter):
1. Create a JSON file with all feed definitions
2. Use the `add_multi_feeds` management command:
   ```bash
   python manage.py add_multi_feeds --website-url "https://example.com" \
     --website-name "Example Site" --feeds-file feeds.json
   ```
3. The system automatically handles deduplication when articles appear in multiple feeds

### Processing New Articles
1. Compare feed items against database to identify new content
2. Fetch full article HTML from source URL
3. Extract and clean article text (remove ads, navigation, etc.)
4. Parse metadata (OpenGraph tags, JSON-LD, meta tags)
5. Store in database with appropriate timestamps and relationships

### Monitoring and Maintenance
- Log all feed check attempts and results
- Track success/failure rates per feed
- Implement alerting for consistently failing feeds
- Provide metrics on article ingestion rate and processing time

## Error Handling
- **Invalid Feeds**: Log and mark as inactive after repeated failures
- **Network Issues**: Implement exponential backoff for retries
- **Parsing Failures**: Store raw content for manual review/reprocessing
- **Rate Limiting**: Respect 429 responses and adjust polling frequency

## Security Considerations
- Sanitize all HTML content before storage
- Validate feed URLs to prevent SSRF attacks
- Implement request timeouts to prevent hanging connections
- Use User-Agent headers to identify the aggregator

## Management Commands

### Available Commands
- `discover_feeds <url>` - Discover feeds for a website
- `add_multi_feeds` - Add multiple feeds from JSON file
- `fetch_all_website_feeds <website_name>` - Fetch content from all feeds for a website
- `check_new_content` - Check all active feeds for new content
- `fetch_content <feed_id>` - Fetch content from a specific feed

### Example: Adding Hollywood Reporter
```bash
# Create feeds JSON file with all category feeds
python manage.py add_multi_feeds \
  --website-url "https://www.hollywoodreporter.com" \
  --website-name "Hollywood Reporter" \
  --feeds-file hollywood_reporter_feeds.json

# Fetch all feeds
python manage.py fetch_all_website_feeds "Hollywood Reporter"
```

## Future Enhancements to Consider
- Support for authenticated/private feeds
- Natural language processing for article categorization
- Full-text search capabilities
- Webhook notifications for new content
- RSS feed generation from aggregated content
- Automatic discovery of category feeds from site navigation