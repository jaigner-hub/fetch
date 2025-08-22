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
    Discover and save RSS/Atom feeds for a specific website.
    
    Args:
        website_id: ID of the Website model instance
    """
    try:
        website = Website.objects.get(id=website_id)
        logger.info(f"Starting feed discovery for {website.name} ({website.url})")
        
        # Try OpenRouter-based discovery if API key is available
        if hasattr(settings, 'OPENROUTER_API_KEY') and settings.OPENROUTER_API_KEY:
            try:
                from .openrouter_feed_discovery import OpenRouterFeedDiscoverer
                logger.info("Using OpenRouter AI for intelligent feed discovery")
                discoverer = OpenRouterFeedDiscoverer(website.url)
                results = discoverer.discover_feeds_intelligently()
            except Exception as e:
                logger.warning(f"OpenRouter discovery failed, falling back to traditional: {e}")
                discoverer = FeedDiscoverer(website.url)
                results = discoverer.discover_all()
        else:
            logger.info("Using traditional feed discovery (no OpenRouter API key)")
            discoverer = FeedDiscoverer(website.url)
            results = discoverer.discover_all()
        
        feeds_created = 0
        sitemaps_created = 0
        
        # Process discovered feeds
        for feed_info in results['feeds']:
            # Check if feed was manually excluded
            existing_feed = Feed.objects.filter(feed_url=feed_info['url']).first()
            if existing_feed and existing_feed.manual_exclude:
                logger.info(f"Skipping manually excluded feed: {feed_info['url']}")
                continue
                
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
        
        # Process discovered sitemaps (selective, high-value only)
        for sitemap_info in results.get('sitemaps', []):
            # Check if sitemap was manually excluded
            existing_sitemap = Feed.objects.filter(feed_url=sitemap_info['url']).first()
            if existing_sitemap and existing_sitemap.manual_exclude:
                logger.info(f"Skipping manually excluded sitemap: {sitemap_info['url']}")
                continue
                
            sitemap, created = Feed.objects.get_or_create(
                feed_url=sitemap_info['url'],
                defaults={
                    'website': website,
                    'feed_type': 'SITEMAP',
                    'title': sitemap_info.get('title', 'Sitemap'),
                    'description': f"Recent content sitemap (Priority: {sitemap_info.get('priority', 50)})",
                }
            )
            
            if created:
                sitemaps_created += 1
                logger.info(f"Created selective sitemap: {sitemap.feed_url} (priority: {sitemap_info.get('priority', 50)})")
        
        logger.info(f"Feed discovery completed for {website.name}. Created {feeds_created} feeds and {sitemaps_created} selective sitemaps.")
        return f"Discovered {feeds_created} feeds and {sitemaps_created} selective sitemaps for {website.name}"
        
    except Website.DoesNotExist:
        logger.error(f"Website with ID {website_id} not found")
        return f"Website with ID {website_id} not found"
    except Exception as e:
        logger.error(f"Error discovering feeds for website {website_id}: {e}")
        return f"Error: {str(e)}"


@shared_task
def fetch_feed_content(feed_id):
    """
    Fetch and save content from a specific RSS/Atom feed or selective sitemap.
    
    Args:
        feed_id: ID of the Feed model instance
    """
    try:
        feed = Feed.objects.get(id=feed_id)
        
        # Route to appropriate handler based on feed type
        if feed.feed_type == 'SITEMAP':
            return fetch_selective_sitemap_content(feed_id)
        
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
                            # Validate article before creating
                            title = article_data.get('title', '').strip()
                            content = article_data.get('content', '')
                            
                            # Skip articles with no or bad title
                            if not title or len(title) < 10 or title == '...':
                                logger.warning(f"Skipping article with bad title: {article_data['url']}")
                                continue
                            
                            # Skip articles with minimal content
                            if content:
                                from bs4 import BeautifulSoup
                                soup = BeautifulSoup(content, 'html.parser')
                                text = soup.get_text(strip=True)
                                word_count = len(text.split())
                                if word_count < 100:  # Minimum 100 words
                                    logger.warning(f"Skipping article with only {word_count} words: {article_data['url']}")
                                    continue
                            else:
                                logger.warning(f"Skipping article with no content: {article_data['url']}")
                                continue
                            
                            # Create new article
                            article = Article.objects.create(
                                feed=feed,
                                url=article_data['url'],
                                title=title,
                                content=content,
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
                            
                            # Queue for automatic analysis
                            analyze_article_after_fetch.delay(article.id)
                    
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


@shared_task(time_limit=180, soft_time_limit=150)  # 3 minute limit for sitemap processing
def fetch_selective_sitemap_content(feed_id):
    """
    Fetch and save content from a sitemap, but only for recent articles (48 hours).
    
    Args:
        feed_id: ID of the Feed model instance (should be SITEMAP type)
    """
    try:
        feed = Feed.objects.get(id=feed_id)
        
        if feed.feed_type != 'SITEMAP':
            logger.warning(f"Feed {feed.feed_url} is not a sitemap, skipping")
            return "Not a sitemap feed"
        
        logger.info(f"Starting selective sitemap content fetch for: {feed.feed_url}")
        
        # Create fetch log
        fetch_log = FetchLog.objects.create(feed=feed)
        
        # Use our selective sitemap processor
        from .sitemap_processor import SelectiveSitemapProcessor
        from .content_fetcher import ContentFetcher
        
        processor = SelectiveSitemapProcessor(max_age_hours=48)
        fetcher = ContentFetcher()
        
        # Get recent article URLs from sitemap
        recent_urls = processor.fetch_sitemap_urls(feed.feed_url)
        
        if not recent_urls:
            feed.mark_checked(success=True)  # Not an error, just no recent content
            fetch_log.completed_at = timezone.now()
            fetch_log.success = True
            fetch_log.new_articles = 0
            fetch_log.save()
            logger.info(f"No recent URLs found in sitemap: {feed.feed_url}")
            return "No recent content in sitemap"
        
        logger.info(f"Found {len(recent_urls)} recent URLs in sitemap {feed.feed_url}")
        
        new_articles = 0
        processed_urls = 0
        max_articles_per_sitemap = 20  # Limit to prevent overwhelming the system
        
        for url_info in recent_urls[:max_articles_per_sitemap]:
            url = url_info['url']
            
            try:
                processed_urls += 1
                
                # Check if article already exists
                if Article.objects.filter(url=url).exists():
                    logger.debug(f"Article already exists: {url}")
                    continue
                
                # Fetch the article content
                article_content = fetcher.fetch_article_content(url)
                
                if not article_content:
                    logger.debug(f"No content fetched from: {url}")
                    continue
                
                # Validate it's actually an article
                if not processor.validate_article_content(url, article_content):
                    logger.debug(f"Content validation failed for: {url}")
                    continue
                
                # Extract metadata
                metadata = fetcher.extract_metadata(url)
                
                # Get clean text for final validation
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(article_content, 'html.parser')
                text_content = soup.get_text(strip=True)
                
                # Final content check
                if len(text_content) < 300:
                    logger.debug(f"Insufficient text content ({len(text_content)} chars): {url}")
                    continue
                
                # Validate title
                title = metadata.get('title', '').strip()
                if not title or len(title) < 10:
                    logger.warning(f"Bad/missing title from: {url}")
                    continue
                
                # Process publication date
                pub_date = metadata.get('published_date')
                if pub_date and not timezone.is_aware(pub_date):
                    pub_date = timezone.make_aware(pub_date)
                elif not pub_date:
                    # Use lastmod from sitemap if available
                    if url_info.get('lastmod'):
                        pub_date = processor.parse_sitemap_date(url_info['lastmod'])
                    if not pub_date:
                        pub_date = timezone.now()
                
                # Prepare metadata for storage
                safe_metadata = {k: v for k, v in metadata.items() if k != 'published_date'}
                if pub_date:
                    safe_metadata['published_date'] = pub_date.isoformat()
                
                # Extract images
                imgs = []
                for img in soup.find_all('img'):
                    img_url = img.get('src', '')
                    if img_url:
                        imgs.append({
                            'url': img_url,
                            'alt': img.get('alt', ''),
                            'title': img.get('title', '')
                        })
                
                # Create article
                article = Article.objects.create(
                    feed=feed,
                    url=url,
                    title=title,
                    content=article_content,
                    summary=metadata.get('description', '')[:500],
                    author=metadata.get('author', ''),
                    published_date=pub_date,
                    raw_data=safe_metadata,
                    tags=[],
                    images=imgs,
                    featured_image=metadata.get('image', '')
                )
                new_articles += 1
                logger.info(f"Created article from selective sitemap: {article.title}")
                
                # Queue for automatic analysis
                analyze_article_after_fetch.delay(article.id)
                
                # Rate limiting
                import time
                time.sleep(0.5)  # Be polite to servers
                
            except Exception as e:
                logger.error(f"Error processing URL {url}: {e}")
                continue
        
        # Update feed status
        feed.mark_checked(success=True)
        
        # Update fetch log
        fetch_log.completed_at = timezone.now()
        fetch_log.success = True
        fetch_log.new_articles = new_articles
        fetch_log.save()
        
        logger.info(f"Selective sitemap fetch completed: {new_articles} new articles from {processed_urls} URLs")
        return f"Fetched {new_articles} recent articles from sitemap"
        
    except Feed.DoesNotExist:
        logger.error(f"Feed with ID {feed_id} not found")
        return f"Feed not found"
    except Exception as e:
        logger.error(f"Error fetching selective sitemap {feed_id}: {e}")
        
        if 'fetch_log' in locals():
            fetch_log.completed_at = timezone.now()
            fetch_log.success = False
            fetch_log.error_message = str(e)
            fetch_log.save()
        
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
    Fetch all content from all RSS/Atom feeds for a specific website.
    
    Args:
        website_id: ID of the Website model instance
    """
    try:
        website = Website.objects.get(id=website_id)
        logger.info(f"Starting full content fetch for website: {website.name}")
        
        # Get all active RSS/Atom feeds for this website
        rss_feeds = website.feeds.filter(active=True, feed_type__in=['RSS', 'ATOM'])
        
        total_feeds = rss_feeds.count()
        
        if total_feeds == 0:
            logger.warning(f"No active feeds found for website {website.name}")
            return f"No active feeds found for {website.name}"
        
        logger.info(f"Processing {total_feeds} RSS/ATOM feeds for {website.name}")
        
        # Create FetchLog entries immediately for progress tracking
        # This ensures the frontend can detect that fetching is in progress
        for feed in rss_feeds:
            FetchLog.objects.create(feed=feed)
            logger.info(f"Created FetchLog for: {feed.title or feed.feed_url}")
        
        # Queue fetch tasks for RSS/ATOM feeds
        queued_tasks = 0
        for feed in rss_feeds:
            fetch_feed_content.delay(feed.id)
            queued_tasks += 1
            logger.info(f"Queued RSS/ATOM fetch task for: {feed.title or feed.feed_url}")
        
        return f"Queued {queued_tasks} RSS/ATOM fetch tasks for {website.name}"
        
    except Website.DoesNotExist:
        logger.error(f"Website with ID {website_id} not found")
        return f"Website with ID {website_id} not found"
    except Exception as e:
        logger.error(f"Error fetching content for website {website_id}: {e}")
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
        
        # Check if already analyzed (this check might be bypassed if analysis was deleted for re-analysis)
        if hasattr(article, 'analysis'):
            logger.info(f"Article {article_id} already analyzed, skipping")
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
            
            similar = analyzer.find_similar_articles(article, threshold=0.85)
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
def analyze_article_after_fetch(article_id, delay_seconds=60):
    """
    Queue article for analysis after a short delay to allow batch processing.
    
    Args:
        article_id: ID of the Article to analyze
        delay_seconds: Delay before analysis (to allow batching)
    """
    try:
        # Check if article already has analysis
        article = Article.objects.get(id=article_id)
        if hasattr(article, 'analysis'):
            logger.info(f"Article {article_id} already analyzed, skipping")
            return "Article already analyzed"
        
        # Queue for analysis with a small delay
        analyze_article_async.apply_async(
            args=[article_id],
            kwargs={'find_similar': True},
            countdown=delay_seconds
        )
        logger.info(f"Queued article {article_id} for analysis in {delay_seconds} seconds")
        return "Queued for analysis"
    except Article.DoesNotExist:
        logger.error(f"Article {article_id} not found")
        return "Article not found"
    except Exception as e:
        logger.error(f"Error queuing article {article_id} for analysis: {e}")
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


@shared_task
def generate_article_clusters(hours=48, min_cluster_size=2, force_regenerate=False):
    """
    Generate article clusters for recent content.
    
    Args:
        hours: Time window to look for articles (default 48 hours)
        min_cluster_size: Minimum number of articles to form a cluster
        force_regenerate: Whether to regenerate existing clusters
    """
    from .local_clustering import LocalClusterBuilder
    from .models import Article, ArticleCluster
    from django.utils import timezone
    from datetime import timedelta
    
    logger.info(f"Starting cluster generation for last {hours} hours")
    
    # Get time window
    since = timezone.now() - timedelta(hours=hours)
    
    # Get articles in time window - limit to a manageable number
    # Convert QuerySet to list to avoid issues with numpy indexing
    articles_qs = Article.objects.filter(
        published_date__gte=since
    ).select_related('feed__website').order_by('-published_date')[:500]  # Limit to 500 most recent
    
    articles = list(articles_qs)  # Convert to list for indexing
    article_count = len(articles)
    logger.info(f"Processing {article_count} most recent articles from time window")
    
    if article_count < min_cluster_size:
        logger.info("Not enough articles to form clusters")
        return f"Not enough articles to cluster (found {article_count}, need at least {min_cluster_size})"
    
    # Optionally clear existing clusters in this time window
    if force_regenerate:
        old_clusters = ArticleCluster.objects.filter(
            latest_article__gte=since
        )
        old_count = old_clusters.count()
        old_clusters.delete()
        logger.info(f"Deleted {old_count} existing clusters")
    
    # Initialize clustering (without sentence transformers for speed)
    clusterer = LocalClusterBuilder(
        use_sentence_transformers=False,  # Disabled for speed
        similarity_threshold=0.45,
        min_cluster_size=min_cluster_size
    )
    
    # Build clusters
    clusters = clusterer.build_clusters(
        articles,
        min_cluster_size=min_cluster_size,
        similarity_threshold=0.45,
        time_window_hours=hours,
        method='tfidf'  # Use only TF-IDF for speed
    )
    
    # Create cluster objects in database
    created_clusters = 0
    total_articles_clustered = 0
    
    for cluster_articles in clusters:
        if len(cluster_articles) >= min_cluster_size:
            # Check if cluster already exists
            article_ids = [a.id for a in cluster_articles]
            existing = ArticleCluster.objects.filter(
                articles__in=article_ids
            ).distinct().first()
            
            if not existing:
                # Create new cluster
                cluster = clusterer.create_cluster_from_articles(cluster_articles)
                if cluster:
                    created_clusters += 1
                    total_articles_clustered += len(cluster_articles)
                    logger.info(f"Created cluster '{cluster.title}' with {len(cluster_articles)} articles")
    
    logger.info(f"Cluster generation complete. Created {created_clusters} clusters with {total_articles_clustered} articles")
    return f"Created {created_clusters} clusters with {total_articles_clustered} articles from {article_count} total articles"