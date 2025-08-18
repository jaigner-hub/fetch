"""
Celery tasks for RSS feed processing.
"""
from celery import shared_task
from django.utils import timezone
from django.db import transaction
import logging

from .models import Website, Feed, Article, FetchLog, ArticleAnalysis, GeneratedContent
from .feed_discovery import FeedDiscoverer
from .content_fetcher import ContentFetcher
from .article_analyzer import ArticleAnalyzer, ContentGenerator
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task
def discover_feeds_for_website(website_id):
    """
    Discover and save feeds for a specific website.
    Now also discovers RSS/Atom feeds from sitemaps.
    
    Args:
        website_id: ID of the Website model instance
    """
    try:
        website = Website.objects.get(id=website_id)
        logger.info(f"Starting feed discovery for {website.name} ({website.url})")
        
        # Try Claude-based discovery if API key is available
        if settings.ANTHROPIC_API_KEY:
            try:
                from .claude_feed_discovery import ClaudeFeedDiscoverer
                logger.info("Using Claude AI for intelligent feed discovery")
                discoverer = ClaudeFeedDiscoverer(website.url)
                results = discoverer.discover_feeds_intelligently()
            except Exception as e:
                logger.warning(f"Claude discovery failed, falling back to traditional: {e}")
                discoverer = FeedDiscoverer(website.url)
                results = discoverer.discover_all()
        else:
            logger.info("Using traditional feed discovery (no Claude API key)")
            discoverer = FeedDiscoverer(website.url)
            results = discoverer.discover_all()
        
        feeds_created = 0
        sitemaps_created = 0
        
        # Process discovered feeds
        for feed_info in results['feeds']:
            feed, created = Feed.objects.get_or_create(
                feed_url=feed_info['url'],
                defaults={
                    'website': website,
                    'feed_type': feed_info.get('type', 'RSS'),
                    'title': feed_info.get('title', ''),
                    'description': feed_info.get('description', ''),
                }
            )
            
            if created:
                feeds_created += 1
                logger.info(f"Created new feed: {feed.feed_url}")
            
            # Validate the feed
            if created or not feed.last_checked:
                validated = discoverer.validate_feed(feed.feed_url)
                if validated:
                    feed.title = validated.get('title', feed.title)
                    feed.description = validated.get('description', feed.description)
                    feed.save()
        
        # Process discovered sitemaps
        for sitemap_info in results.get('sitemaps', []):
            sitemap, created = Feed.objects.get_or_create(
                feed_url=sitemap_info['url'],
                defaults={
                    'website': website,
                    'feed_type': 'SITEMAP',
                    'title': sitemap_info.get('title', f"Sitemap: {sitemap_info['url']}"),
                    'description': sitemap_info.get('description', 'XML Sitemap'),
                }
            )
            
            if created:
                sitemaps_created += 1
                logger.info(f"Created new sitemap: {sitemap.feed_url}")
        
        logger.info(f"Feed discovery completed for {website.name}. Created {feeds_created} new feeds and {sitemaps_created} new sitemaps.")
        return f"Discovered {feeds_created} new feeds and {sitemaps_created} sitemaps for {website.name}"
        
    except Website.DoesNotExist:
        logger.error(f"Website with ID {website_id} not found")
        return f"Website with ID {website_id} not found"
    except Exception as e:
        logger.error(f"Error discovering feeds for website {website_id}: {e}")
        return f"Error: {str(e)}"


@shared_task
def fetch_feed_content(feed_id):
    """
    Fetch and save content from a specific feed.
    NOTE: Now only processes RSS/ATOM feeds, not sitemaps.
    
    Args:
        feed_id: ID of the Feed model instance
    """
    try:
        feed = Feed.objects.get(id=feed_id)
        
        # Skip sitemap feeds - they are only for discovering RSS/Atom feeds
        if feed.feed_type == 'SITEMAP':
            logger.info(f"Skipping sitemap feed (only for discovery): {feed.feed_url}")
            return "Sitemap feeds are not processed for content"
        
        logger.info(f"Starting content fetch for feed: {feed.feed_url}")
        
        # Get or create fetch log (may already exist if created by fetch_all_website_content)
        # Look for a recent incomplete fetch log
        from datetime import timedelta
        recent_cutoff = timezone.now() - timedelta(minutes=5)
        fetch_log = FetchLog.objects.filter(
            feed=feed,
            started_at__gte=recent_cutoff,
            completed_at__isnull=True
        ).first()
        
        if not fetch_log:
            # Create new fetch log if none exists
            fetch_log = FetchLog.objects.create(feed=feed)
        
        fetcher = ContentFetcher()
        
        if feed.feed_type in ['RSS', 'ATOM']:
            result = fetcher.fetch_feed_content(feed.feed_url)
            
            if result['success']:
                new_articles = 0
                updated_articles = 0
                
                with transaction.atomic():
                    for article_data in result['articles']:
                        # Check if article already exists
                        try:
                            article = Article.objects.get(url=article_data['url'])
                            
                            # Article exists - check if this feed should be added as additional feed
                            if feed != article.feed and feed not in article.additional_feeds.all():
                                article.additional_feeds.add(feed)
                                logger.info(f"Article '{article.title}' also found in feed {feed.title or feed.feed_url}")
                            
                            # Check if content has changed
                            if article.content_hash != article_data['content_hash']:
                                # Update article
                                article.title = article_data['title']
                                article.content = article_data['content']
                                article.summary = article_data['summary']
                                article.author = article_data['author']
                                article.published_date = article_data['published_date']
                                article.raw_data = article_data.get('raw_data', {})
                                article.save()
                                updated_articles += 1
                                logger.info(f"Updated article: {article.title}")
                                
                        except Article.DoesNotExist:
                            # Create new article
                            article = Article.objects.create(
                                feed=feed,
                                url=article_data['url'],
                                title=article_data['title'],
                                content=article_data['content'],
                                summary=article_data['summary'],
                                author=article_data['author'],
                                published_date=article_data['published_date'],
                                raw_data=article_data.get('raw_data', {}),
                                tags=article_data.get('tags', []),
                                images=article_data.get('images', []),
                                featured_image=article_data.get('featured_image', '')
                            )
                            new_articles += 1
                            logger.info(f"Created new article: {article.title}")
                    
                    # Update feed status
                    feed.mark_checked(success=True)
                    
                    # Update fetch log
                    fetch_log.completed_at = timezone.now()
                    fetch_log.success = True
                    fetch_log.new_articles = new_articles
                    fetch_log.updated_articles = updated_articles
                    fetch_log.save()
                
                logger.info(f"Feed fetch completed: {new_articles} new, {updated_articles} updated")
                return f"Fetched {new_articles} new and {updated_articles} updated articles"
                
            else:
                # Handle fetch error
                error_msg = result.get('error', 'Unknown error')
                feed.mark_checked(success=False, error_message=error_msg)
                
                fetch_log.completed_at = timezone.now()
                fetch_log.success = False
                fetch_log.error_message = error_msg
                fetch_log.save()
                
                logger.error(f"Failed to fetch feed {feed.feed_url}: {error_msg}")
                return f"Error fetching feed: {error_msg}"
            
    except Feed.DoesNotExist:
        logger.error(f"Feed with ID {feed_id} not found")
        return f"Feed with ID {feed_id} not found"
    except Exception as e:
        logger.error(f"Error fetching feed {feed_id}: {e}")
        
        if 'fetch_log' in locals():
            fetch_log.completed_at = timezone.now()
            fetch_log.success = False
            fetch_log.error_message = str(e)
            fetch_log.save()
            
        return f"Error: {str(e)}"


@shared_task
def fetch_article_full_content(article_id):
    """
    Fetch full content for an article from its URL.
    
    Args:
        article_id: ID of the Article model instance
    """
    try:
        article = Article.objects.get(id=article_id)
        
        if article.content:
            logger.info(f"Article {article.id} already has content, skipping")
            return "Article already has content"
        
        logger.info(f"Fetching full content for article: {article.url}")
        
        fetcher = ContentFetcher()
        content = fetcher.fetch_article_content(article.url)
        
        if content:
            article.content = content
            article.save()
            logger.info(f"Successfully fetched content for article {article.id}")
            return "Content fetched successfully"
        else:
            logger.warning(f"Could not fetch content for article {article.id}")
            return "Could not fetch content"
            
    except Article.DoesNotExist:
        logger.error(f"Article with ID {article_id} not found")
        return f"Article with ID {article_id} not found"
    except Exception as e:
        logger.error(f"Error fetching article content for {article_id}: {e}")
        return f"Error: {str(e)}"


@shared_task
def check_all_feeds():
    """
    Check all active RSS/ATOM feeds for new content.
    This is a periodic task that runs hourly.
    NOTE: Skips sitemap feeds as they are only for discovery.
    """
    logger.info("Starting periodic feed check")
    
    # Only check RSS/ATOM feeds, not sitemaps
    active_feeds = Feed.objects.filter(active=True).exclude(feed_type='SITEMAP')
    total_feeds = active_feeds.count()
    
    logger.info(f"Checking {total_feeds} active RSS/ATOM feeds")
    
    for feed in active_feeds:
        # Queue individual feed fetch tasks
        fetch_feed_content.delay(feed.id)
    
    return f"Queued {total_feeds} RSS/ATOM feed fetch tasks"


@shared_task
def discover_new_feeds():
    """
    Discover feeds for all active websites.
    This is a periodic task that runs daily.
    """
    logger.info("Starting periodic feed discovery")
    
    active_websites = Website.objects.filter(active=True)
    total_websites = active_websites.count()
    
    logger.info(f"Discovering feeds for {total_websites} active websites")
    
    for website in active_websites:
        # Queue individual website discovery tasks
        discover_feeds_for_website.delay(website.id)
    
    return f"Queued {total_websites} feed discovery tasks"


@shared_task
def fetch_all_website_content(website_id):
    """
    Fetch all content from all feeds for a specific website.
    Now also processes sitemap feeds to fetch articles from them.
    
    Args:
        website_id: ID of the Website model instance
    """
    try:
        website = Website.objects.get(id=website_id)
        logger.info(f"Starting full content fetch for website: {website.name}")
        
        # Get all active feeds for this website (including sitemaps)
        all_feeds = website.feeds.filter(active=True)
        rss_feeds = all_feeds.filter(feed_type__in=['RSS', 'ATOM'])
        sitemap_feeds = all_feeds.filter(feed_type='SITEMAP')
        
        total_rss = rss_feeds.count()
        total_sitemaps = sitemap_feeds.count()
        
        if total_rss == 0 and total_sitemaps == 0:
            logger.warning(f"No active feeds found for website {website.name}")
            return f"No active feeds found for {website.name}"
        
        logger.info(f"Processing {total_rss} RSS/ATOM feeds and {total_sitemaps} sitemaps for {website.name}")
        
        # Create FetchLog entries immediately for progress tracking
        # This ensures the frontend can detect that fetching is in progress
        for feed in rss_feeds:
            FetchLog.objects.create(feed=feed)
            logger.info(f"Created FetchLog for: {feed.title or feed.feed_url}")
        
        # Queue fetch tasks for RSS/ATOM feeds
        queued_rss_tasks = 0
        for feed in rss_feeds:
            fetch_feed_content.delay(feed.id)
            queued_rss_tasks += 1
            logger.info(f"Queued RSS/ATOM fetch task for: {feed.title or feed.feed_url}")
        
        # Skip sitemap processing for now - they're too slow
        queued_sitemap_tasks = 0
        # for sitemap_feed in sitemap_feeds:
        #     fetch_sitemap_content.delay(sitemap_feed.id)
        #     queued_sitemap_tasks += 1
        #     logger.info(f"Queued sitemap fetch task for: {sitemap_feed.feed_url}")
        
        return f"Queued {queued_rss_tasks} RSS/ATOM and {queued_sitemap_tasks} sitemap fetch tasks for {website.name}"
        
    except Website.DoesNotExist:
        logger.error(f"Website with ID {website_id} not found")
        return f"Website with ID {website_id} not found"
    except Exception as e:
        logger.error(f"Error fetching content for website {website_id}: {e}")
        return f"Error: {str(e)}"


@shared_task(time_limit=120, soft_time_limit=100)  # 2 minute hard limit, 100 second soft limit
def fetch_sitemap_content(feed_id):
    """
    Fetch and save content from a sitemap feed by extracting article URLs
    and fetching their content.
    
    Args:
        feed_id: ID of the Feed model instance (should be a SITEMAP type)
    """
    try:
        feed = Feed.objects.get(id=feed_id)
        
        if feed.feed_type != 'SITEMAP':
            logger.warning(f"Feed {feed.feed_url} is not a sitemap, skipping")
            return "Not a sitemap feed"
        
        logger.info(f"Starting sitemap content fetch for: {feed.feed_url}")
        
        # Create fetch log
        fetch_log = FetchLog.objects.create(feed=feed)
        
        fetcher = ContentFetcher()
        
        # Get URLs from sitemap
        sitemap_urls = fetcher.fetch_sitemap_urls(feed.feed_url)
        
        if not sitemap_urls:
            feed.mark_checked(success=False, error_message="No URLs found in sitemap")
            fetch_log.completed_at = timezone.now()
            fetch_log.success = False
            fetch_log.error_message = "No URLs found in sitemap"
            fetch_log.save()
            logger.warning(f"No URLs found in sitemap: {feed.feed_url}")
            return "No URLs found in sitemap"
        
        logger.info(f"Found {len(sitemap_urls)} URLs in sitemap {feed.feed_url}")
        
        new_articles = 0
        updated_articles = 0
        processed_urls = 0
        
        # Filter and limit URLs to process
        # Only process recent URLs (skip very old content from archived sitemaps)
        from datetime import datetime, timedelta
        import re
        
        # Check if this is a news sitemap (usually has recent content)
        is_news_sitemap = 'news' in feed.feed_url.lower() or 'recent' in feed.feed_url.lower()
        
        # Try to detect year in sitemap URL (e.g., sitemap-2002.xml)
        year_match = re.search(r'sitemap-(\d{4})', feed.feed_url)
        if year_match:
            sitemap_year = int(year_match.group(1))
            current_year = datetime.now().year
            # Skip sitemaps older than 2 years (unless it's a news sitemap)
            if sitemap_year < current_year - 2 and not is_news_sitemap:
                logger.info(f"Skipping old sitemap from {sitemap_year}: {feed.feed_url}")
                feed.mark_checked(success=True)
                fetch_log.completed_at = timezone.now()
                fetch_log.success = True
                fetch_log.new_articles = 0
                fetch_log.save()
                return f"Skipped old sitemap from {sitemap_year}"
        
        # Limit URLs to process
        # News sitemaps get more URLs since they're recent
        max_urls_to_process = 30 if is_news_sitemap else 10
        
        # Process each URL from the sitemap
        for url in sitemap_urls[:max_urls_to_process]:
            try:
                # Check if this looks like an article URL (basic heuristic)
                # Skip obvious non-article pages
                skip_patterns = ['/tag/', '/category/', '/author/', '/page/', '.xml', '.pdf', '/feed/', '/rss/']
                if any(pattern in url.lower() for pattern in skip_patterns):
                    continue
                
                processed_urls += 1
                
                # Check if article already exists
                existing_article = Article.objects.filter(url=url).first()
                
                if existing_article:
                    # Article exists - check if this feed should be added as additional feed
                    if feed != existing_article.feed and feed not in existing_article.additional_feeds.all():
                        existing_article.additional_feeds.add(feed)
                        logger.info(f"Article '{existing_article.title}' also found in sitemap {feed.feed_url}")
                else:
                    # Fetch the article content
                    article_content = fetcher.fetch_article_content(url)
                    
                    if article_content:
                        # Extract metadata from the page
                        metadata = fetcher.extract_metadata(url)
                        
                        # Create new article
                        # Convert datetime to timezone-aware if needed
                        pub_date = metadata.get('published_date')
                        if pub_date and not timezone.is_aware(pub_date):
                            pub_date = timezone.make_aware(pub_date)
                        elif not pub_date:
                            pub_date = timezone.now()
                        
                        # Remove non-serializable data from metadata for raw_data field
                        safe_metadata = {k: v for k, v in metadata.items() if k != 'published_date'}
                        if pub_date:
                            safe_metadata['published_date'] = pub_date.isoformat()
                        
                        # Extract featured image from metadata
                        featured_img = metadata.get('image', '')
                        
                        # Extract images from content
                        imgs = []
                        if article_content:
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(article_content, 'html.parser')
                            for img in soup.find_all('img'):
                                img_url = img.get('src', '')
                                if img_url:
                                    imgs.append({
                                        'url': img_url,
                                        'alt': img.get('alt', ''),
                                        'title': img.get('title', '')
                                    })
                        
                        article = Article.objects.create(
                            feed=feed,
                            url=url,
                            title=metadata.get('title', url),
                            content=article_content,
                            summary=metadata.get('description', '')[:500],
                            author=metadata.get('author', ''),
                            published_date=pub_date,
                            raw_data=safe_metadata,
                            tags=[],  # Sitemap doesn't provide tags
                            images=imgs,
                            featured_image=featured_img
                        )
                        new_articles += 1
                        logger.info(f"Created article from sitemap: {article.title}")
                        
                        # Rate limiting
                        import time
                        time.sleep(0.2)  # Reduced delay for faster processing
                
                # Stop if we've processed enough URLs
                if processed_urls >= 10:  # Reduced from 50 to 10 for much faster processing
                    logger.info(f"Reached processing limit for sitemap {feed.feed_url}")
                    break
                    
            except Exception as e:
                logger.error(f"Error processing URL {url}: {e}")
                continue
        
        # Update feed status
        feed.mark_checked(success=True)
        
        # Update fetch log
        fetch_log.completed_at = timezone.now()
        fetch_log.success = True
        fetch_log.new_articles = new_articles
        fetch_log.updated_articles = updated_articles
        fetch_log.save()
        
        logger.info(f"Sitemap fetch completed: {new_articles} new articles from {processed_urls} URLs")
        return f"Fetched {new_articles} new articles from {processed_urls} sitemap URLs"
        
    except Feed.DoesNotExist:
        logger.error(f"Feed with ID {feed_id} not found")
        return f"Feed with ID {feed_id} not found"
    except Exception as e:
        logger.error(f"Error fetching sitemap {feed_id}: {e}")
        
        if 'fetch_log' in locals():
            fetch_log.completed_at = timezone.now()
            fetch_log.success = False
            fetch_log.error_message = str(e)
            fetch_log.save()
            
        return f"Error: {str(e)}"


@shared_task
def check_scheduled_fetches():
    """
    Check all websites for scheduled content fetching.
    This runs every 5 minutes and fetches content for websites
    that are due based on their individual schedules.
    """
    from datetime import timedelta
    
    logger.info("Checking for scheduled content fetches")
    
    websites_to_fetch = []
    
    # Check all active websites with auto-fetch enabled
    for website in Website.objects.filter(active=True, auto_fetch_enabled=True):
        if website.is_due_for_fetch():
            websites_to_fetch.append(website)
            logger.info(f"Website {website.name} is due for content fetch")
    
    if not websites_to_fetch:
        logger.info("No websites due for fetching")
        return "No websites due for fetching"
    
    # Queue fetch tasks for due websites
    fetched_count = 0
    for website in websites_to_fetch:
        try:
            # Queue the fetch task
            fetch_all_website_content.delay(website.id)
            
            # Update last fetch time
            website.last_auto_fetch = timezone.now()
            website.save(update_fields=['last_auto_fetch'])
            
            fetched_count += 1
            logger.info(f"Queued content fetch for {website.name} (interval: {website.fetch_interval_minutes} minutes)")
            
        except Exception as e:
            logger.error(f"Error queuing fetch for {website.name}: {e}")
    
    return f"Queued content fetches for {fetched_count} websites"


@shared_task
def fetch_single_website_on_schedule(website_id):
    """
    Fetch content for a single website and update its last fetch time.
    Used for individual website scheduling.
    """
    try:
        website = Website.objects.get(id=website_id)
        
        if not website.active or not website.auto_fetch_enabled:
            logger.info(f"Skipping disabled website: {website.name}")
            return f"Website {website.name} is disabled"
        
        logger.info(f"Starting scheduled fetch for {website.name}")
        
        # Fetch all content
        result = fetch_all_website_content(website_id)
        
        # Update last fetch time
        website.last_auto_fetch = timezone.now()
        website.save(update_fields=['last_auto_fetch'])
        
        logger.info(f"Completed scheduled fetch for {website.name}: {result}")
        return result
        
    except Website.DoesNotExist:
        logger.error(f"Website with ID {website_id} not found")
        return f"Website not found"
    except Exception as e:
        logger.error(f"Error in scheduled fetch for website {website_id}: {e}")
        return f"Error: {str(e)}"


@shared_task
def cleanup_old_logs(days=30):
    """
    Clean up old fetch logs.
    
    Args:
        days: Number of days to keep logs
    """
    from datetime import timedelta
    
    cutoff_date = timezone.now() - timedelta(days=days)
    
    deleted_count = FetchLog.objects.filter(started_at__lt=cutoff_date).delete()[0]
    
    logger.info(f"Deleted {deleted_count} old fetch logs")
    return f"Deleted {deleted_count} old fetch logs"


@shared_task(bind=True)
def analyze_article_async(self, article_id, find_similar=True):
    """
    Analyze an article asynchronously using Claude AI with progress tracking.
    
    Args:
        article_id: ID of the Article to analyze
        find_similar: Whether to find similar articles
    """
    try:
        # Update progress: Starting
        self.update_state(
            state='PROGRESS',
            meta={'current': 0, 'total': 100, 'status': 'Loading article...'}
        )
        
        article = Article.objects.get(id=article_id)
        
        # Check if already analyzed
        if hasattr(article, 'analysis'):
            logger.info(f"Article {article_id} already analyzed")
            return "Article already analyzed"
        
        # Update progress: Analyzing
        self.update_state(
            state='PROGRESS',
            meta={'current': 20, 'total': 100, 'status': 'Analyzing content with AI...'}
        )
        
        analyzer = ArticleAnalyzer()
        
        # Extract summary and topics
        result = analyzer.extract_summary_and_topics(article)
        
        # Update progress: Creating analysis
        self.update_state(
            state='PROGRESS',
            meta={'current': 50, 'total': 100, 'status': 'Saving analysis results...'}
        )
        
        # Create analysis record
        # Ensure summary is not JSON
        summary = result.get('summary', '')
        if isinstance(summary, str) and summary.strip().startswith('{'):
            # Try to extract from JSON if it's still JSON
            import re
            match = re.search(r'"summary"\s*:\s*"([^"]*)', summary)
            if match:
                summary = match.group(1).replace('\\n', '\n').replace('\\"', '"')
            else:
                summary = article.summary or article.title
        
        # Ensure topics is a list
        topics = result.get('topics', [])
        if not isinstance(topics, list):
            topics = []
        
        analysis = ArticleAnalysis.objects.create(
            article=article,
            ai_summary=summary,
            topics=topics,
            entities=result.get('entities', {}),
            sentiment=result.get('sentiment', 'neutral'),
            keywords=result.get('keywords', [])
        )
        
        # Find similar articles if requested
        if find_similar:
            # Update progress: Finding similar
            self.update_state(
                state='PROGRESS',
                meta={'current': 70, 'total': 100, 'status': 'Finding similar articles...'}
            )
            
            similar = analyzer.find_similar_articles(article, threshold=0.7)
            for similar_article, score in similar[:10]:
                analysis.similar_articles.add(similar_article)
                logger.info(f"Found similar article: {similar_article.title[:50]} (score: {score:.2f})")
            
            # Update progress: Checking duplicates
            self.update_state(
                state='PROGRESS',
                meta={'current': 90, 'total': 100, 'status': 'Checking for duplicates...'}
            )
            
            # Check for duplicates
            duplicate = analyzer.check_duplicate_content(article)
            if duplicate:
                analysis.duplicate_of = duplicate
                analysis.save()
                logger.info(f"Article is duplicate of: {duplicate.title[:50]}")
        
        # Update progress: Complete
        self.update_state(
            state='PROGRESS',
            meta={'current': 100, 'total': 100, 'status': 'Analysis complete!'}
        )
        
        logger.info(f"Successfully analyzed article {article_id}")
        return f"Article analyzed successfully"
        
    except Article.DoesNotExist:
        logger.error(f"Article {article_id} not found")
        return f"Article not found"
    except Exception as e:
        logger.error(f"Error analyzing article {article_id}: {e}")
        return f"Error: {str(e)}"


@shared_task(bind=True)
def generate_content_async(self, source_article_ids, style='news', target_length=800):
    """
    Generate new content asynchronously from source articles with progress tracking.
    
    Args:
        source_article_ids: List of Article IDs to use as sources
        style: Writing style (news, blog, analysis, summary)
        target_length: Target word count
    """
    try:
        # Update progress: Starting
        self.update_state(
            state='PROGRESS',
            meta={'current': 0, 'total': 100, 'status': 'Loading source articles...'}
        )
        
        # Get source articles
        source_articles = Article.objects.filter(id__in=source_article_ids)
        
        if not source_articles.exists():
            logger.error("No valid source articles found")
            return "No valid source articles"
        
        # Update progress: Preparing
        self.update_state(
            state='PROGRESS',
            meta={'current': 20, 'total': 100, 'status': f'Preparing {source_articles.count()} source articles...'}
        )
        
        generator = ContentGenerator()
        
        # Update progress: Generating
        self.update_state(
            state='PROGRESS',
            meta={'current': 40, 'total': 100, 'status': 'Generating content with AI...'}
        )
        
        # Generate content
        result = generator.generate_article(
            source_articles=list(source_articles),
            style=style,
            target_length=target_length
        )
        
        # Update progress: Saving
        self.update_state(
            state='PROGRESS',
            meta={'current': 80, 'total': 100, 'status': 'Saving generated content...'}
        )
        
        # Save generated content
        generated = GeneratedContent.objects.create(
            title=result.get('title', 'Generated Article'),
            subtitle=result.get('subtitle', ''),
            content=result.get('content', ''),
            summary=result.get('summary', ''),
            style=style,
            topics=result.get('topics', []),
            media_items=result.get('media_items', []),
            web_sources=result.get('web_sources', []),
            generation_params={
                'style': style,
                'target_length': target_length,
                'source_count': source_articles.count()
            }
        )
        
        # Add source articles
        generated.source_articles.set(source_articles)
        
        # Update progress: Complete
        self.update_state(
            state='PROGRESS',
            meta={'current': 100, 'total': 100, 'status': 'Content generated successfully!', 'content_id': generated.id}
        )
        
        logger.info(f"Successfully generated content: {generated.title[:50]}")
        return f"Content generated successfully: ID {generated.id}"
        
    except Exception as e:
        logger.error(f"Error generating content: {e}")
        return f"Error: {str(e)}"


@shared_task
def batch_analyze_recent_articles(hours=24, limit=50):
    """
    Batch analyze recent articles that haven't been analyzed yet.
    
    Args:
        hours: Look back period in hours
        limit: Maximum number of articles to analyze
    """
    from datetime import timedelta
    
    since_date = timezone.now() - timedelta(hours=hours)
    
    # Get unanalyzed articles
    articles = Article.objects.filter(
        fetched_at__gte=since_date,
        analysis__isnull=True
    )[:limit]
    
    analyzed_count = 0
    for article in articles:
        try:
            analyze_article_async.delay(article.id)
            analyzed_count += 1
        except Exception as e:
            logger.error(f"Error queuing analysis for article {article.id}: {e}")
    
    logger.info(f"Queued {analyzed_count} articles for analysis")
    return f"Queued {analyzed_count} articles for analysis"