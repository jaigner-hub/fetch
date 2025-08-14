"""
Celery tasks for RSS feed processing.
"""
from celery import shared_task
from django.utils import timezone
from django.db import transaction
import logging

from .models import Website, Feed, Article, FetchLog
from .feed_discovery import FeedDiscoverer
from .content_fetcher import ContentFetcher

logger = logging.getLogger(__name__)


@shared_task
def discover_feeds_for_website(website_id):
    """
    Discover and save feeds for a specific website.
    
    Args:
        website_id: ID of the Website model instance
    """
    try:
        website = Website.objects.get(id=website_id)
        logger.info(f"Starting feed discovery for {website.name} ({website.url})")
        
        discoverer = FeedDiscoverer(website.url)
        results = discoverer.discover_all()
        
        feeds_created = 0
        
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
        for sitemap_info in results['sitemaps']:
            feed, created = Feed.objects.get_or_create(
                feed_url=sitemap_info['url'],
                defaults={
                    'website': website,
                    'feed_type': 'SITEMAP',
                    'title': sitemap_info.get('title', 'Sitemap'),
                }
            )
            
            if created:
                feeds_created += 1
                logger.info(f"Created new sitemap: {feed.feed_url}")
        
        logger.info(f"Feed discovery completed for {website.name}. Created {feeds_created} new feeds.")
        return f"Discovered {feeds_created} new feeds for {website.name}"
        
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
    
    Args:
        feed_id: ID of the Feed model instance
    """
    try:
        feed = Feed.objects.get(id=feed_id)
        logger.info(f"Starting content fetch for feed: {feed.feed_url}")
        
        # Create fetch log
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
                                raw_data=article_data.get('raw_data', {})
                            )
                            new_articles += 1
                            logger.info(f"Created new article: {article.title}")
                        except Exception as e:
                            # Handle case where article exists but belongs to different feed
                            # This can happen when the same article appears in multiple category feeds
                            try:
                                article = Article.objects.get(url=article_data['url'])
                                # Add this feed as an additional feed for the article
                                if feed != article.feed and feed not in article.additional_feeds.all():
                                    article.additional_feeds.add(feed)
                                    logger.info(f"Article '{article.title}' also found in feed {feed.title}")
                            except Exception as inner_e:
                                logger.error(f"Error handling duplicate article: {inner_e}")
                    
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
                
        elif feed.feed_type == 'SITEMAP':
            # Handle sitemap - fetch URLs and create Article entries
            urls = fetcher.fetch_sitemap_urls(feed.feed_url)
            logger.info(f"Found {len(urls)} URLs in sitemap {feed.feed_url}")
            
            new_articles = 0
            skipped_urls = 0
            for url in urls:
                # Skip URLs that are too long
                if len(url) > 2048:
                    logger.warning(f"Skipping URL (too long): {url[:100]}...")
                    skipped_urls += 1
                    continue
                    
                # Check if article already exists
                if not Article.objects.filter(url=url).exists():
                    try:
                        # Create a basic article entry from the sitemap URL
                        # The content can be fetched later if needed
                        title = url.split('/')[-1] or url
                        # Truncate title if too long
                        if len(title) > 500:
                            title = title[:497] + '...'
                            
                        Article.objects.create(
                            feed=feed,
                            url=url,
                            title=title,
                            summary=f"Article from sitemap: {feed.feed_url}",
                            published_date=timezone.now()  # Use current time as placeholder
                        )
                        new_articles += 1
                    except Exception as e:
                        logger.error(f"Failed to create article for URL {url}: {e}")
                        continue
            
            logger.info(f"Created {new_articles} new articles from sitemap")
            if skipped_urls > 0:
                logger.warning(f"Skipped {skipped_urls} URLs due to length restrictions")
            
            feed.mark_checked(success=True)
            fetch_log.completed_at = timezone.now()
            fetch_log.success = True
            fetch_log.new_articles = new_articles
            fetch_log.save()
            
            result_msg = f"Found {len(urls)} URLs in sitemap, created {new_articles} new articles"
            if skipped_urls > 0:
                result_msg += f" (skipped {skipped_urls} URLs due to length)"
            return result_msg
            
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
    Check all active feeds for new content.
    This is a periodic task that runs hourly.
    """
    logger.info("Starting periodic feed check")
    
    active_feeds = Feed.objects.filter(active=True)
    total_feeds = active_feeds.count()
    
    logger.info(f"Checking {total_feeds} active feeds")
    
    for feed in active_feeds:
        # Queue individual feed fetch tasks
        fetch_feed_content.delay(feed.id)
    
    return f"Queued {total_feeds} feed fetch tasks"


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