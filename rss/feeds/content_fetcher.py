"""
Content fetching module to retrieve and parse articles from RSS feeds.
"""
import requests
import feedparser
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime
from typing import List, Dict, Optional
import logging
import hashlib
from django.utils import timezone
import pytz
import time
from collections import defaultdict

logger = logging.getLogger(__name__)


class ContentFetcher:
    """Fetches and processes content from RSS feeds and web pages."""
    
    def __init__(self, timeout: int = 15, rate_limit_delay: float = 1.0):
        """
        Initialize the content fetcher.
        
        Args:
            timeout: Request timeout in seconds
            rate_limit_delay: Delay between requests to same domain in seconds
        """
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self.last_request_time = defaultdict(float)  # Track last request time per domain
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; RSS Content Fetcher/1.0)'
        })
        
    def fetch_feed_content(self, feed_url: str) -> Dict:
        """
        Fetch and parse RSS/Atom feed content.
        
        Args:
            feed_url: URL of the RSS/Atom feed
            
        Returns:
            Dictionary with feed info and articles
        """
        result = {
            'success': False,
            'feed_info': {},
            'articles': [],
            'error': None
        }
        
        try:
            # Fetch the feed
            response = self.session.get(feed_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Parse the feed
            parsed = feedparser.parse(response.content)
            
            # Check if parsing was successful
            if parsed.bozo:
                logger.warning(f"Feed parsing warning for {feed_url}: {parsed.bozo_exception}")
            
            # Extract feed info
            feed_data = parsed.get('feed', {})
            result['feed_info'] = {
                'title': feed_data.get('title', ''),
                'description': feed_data.get('description', ''),
                'link': feed_data.get('link', ''),
                'language': feed_data.get('language', ''),
                'updated': self._parse_date(feed_data.get('updated_parsed'))
            }
            
            # Extract articles
            for entry in parsed.entries:
                article = self._parse_entry(entry, feed_url)
                if article:
                    result['articles'].append(article)
            
            result['success'] = True
            logger.info(f"Successfully fetched {len(result['articles'])} articles from {feed_url}")
            
        except requests.RequestException as e:
            result['error'] = f"Network error: {str(e)}"
            logger.error(f"Error fetching feed {feed_url}: {e}")
        except Exception as e:
            result['error'] = f"Parse error: {str(e)}"
            logger.error(f"Error parsing feed {feed_url}: {e}")
            
        return result
    
    def _parse_entry(self, entry: Dict, feed_url: str) -> Optional[Dict]:
        """
        Parse a feed entry into an article dictionary.
        
        Args:
            entry: Feed entry from feedparser
            feed_url: URL of the feed (for relative URL resolution)
            
        Returns:
            Article dictionary or None if parsing fails
        """
        try:
            # Get the article URL
            article_url = entry.get('link', '')
            if not article_url:
                article_url = entry.get('id', '')
            
            if not article_url:
                logger.warning("Entry has no URL, skipping")
                return None
            
            # Make sure URL is absolute
            article_url = urljoin(feed_url, article_url)
            
            # Get title
            title = entry.get('title', 'Untitled')
            
            # Get RSS content/summary (usually just a snippet)
            rss_content = ''
            summary = entry.get('summary', '')
            
            # Try to get content from RSS feed
            if hasattr(entry, 'content'):
                for content_item in entry.content:
                    if content_item.get('value'):
                        rss_content = content_item['value']
                        break
            
            # If no RSS content, use summary
            if not rss_content:
                rss_content = summary
            
            # Fetch full article content from the article URL
            full_content = self.fetch_article_content(article_url)
            
            # Use full content if available, otherwise fall back to RSS content
            content = full_content if full_content else rss_content
            
            # Get author
            author = entry.get('author', '')
            if not author and hasattr(entry, 'authors'):
                authors = []
                for author_dict in entry.authors:
                    if author_dict.get('name'):
                        authors.append(author_dict['name'])
                author = ', '.join(authors)
            
            # Get published date
            published_date = None
            if hasattr(entry, 'published_parsed'):
                published_date = self._parse_date(entry.published_parsed)
            elif hasattr(entry, 'updated_parsed'):
                published_date = self._parse_date(entry.updated_parsed)
            
            # Calculate content hash for deduplication
            content_hash = self._calculate_content_hash(title, content, summary)
            
            # Get tags/categories
            tags = []
            if hasattr(entry, 'tags'):
                tags = [tag.get('term', '') for tag in entry.tags if tag.get('term')]
            
            return {
                'url': article_url,
                'title': title,
                'content': content,
                'summary': summary,
                'author': author,
                'published_date': published_date,
                'content_hash': content_hash,
                'tags': tags,
                'raw_data': dict(entry)  # Store original data
            }
            
        except Exception as e:
            logger.error(f"Error parsing entry: {e}")
            return None
    
    def _apply_rate_limit(self, url: str):
        """Apply rate limiting per domain."""
        domain = urlparse(url).netloc
        now = time.time()
        last_request = self.last_request_time[domain]
        
        if last_request > 0:
            time_since_last = now - last_request
            if time_since_last < self.rate_limit_delay:
                sleep_time = self.rate_limit_delay - time_since_last
                logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s for domain {domain}")
                time.sleep(sleep_time)
        
        self.last_request_time[domain] = time.time()
    
    def fetch_article_content(self, article_url: str, max_retries: int = 2) -> Optional[str]:
        """
        Fetch and extract main content from an article web page.
        
        Args:
            article_url: URL of the article
            max_retries: Maximum number of retry attempts
            
        Returns:
            Extracted article content or None
        """
        # Apply rate limiting
        self._apply_rate_limit(article_url)
        
        for attempt in range(max_retries + 1):
            try:
                # Add headers to avoid being blocked
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1'
                }
                
                response = self.session.get(article_url, timeout=self.timeout, headers=headers)
                response.raise_for_status()
                
                # Check content type
                content_type = response.headers.get('content-type', '').lower()
                if 'text/html' not in content_type and 'application/xhtml' not in content_type:
                    logger.warning(f"Non-HTML content type for {article_url}: {content_type}")
                    return None
                
                # Parse HTML
                soup = BeautifulSoup(response.content, 'lxml')
                
                # Remove script and style elements
                for script in soup(['script', 'style']):
                    script.decompose()
                
                # Try to find article content using common patterns
                content = self._extract_article_content(soup)
                
                if content:
                    logger.info(f"Successfully extracted {len(content)} characters from {article_url}")
                    return content
                else:
                    logger.warning(f"Could not extract meaningful content from {article_url}")
                    return None
                    
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout fetching {article_url} (attempt {attempt + 1}/{max_retries + 1})")
                if attempt < max_retries:
                    continue
                return None
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 403:
                    logger.warning(f"Access forbidden (403) for {article_url}")
                elif e.response.status_code == 404:
                    logger.warning(f"Article not found (404): {article_url}")
                else:
                    logger.error(f"HTTP error {e.response.status_code} for {article_url}")
                return None
            except requests.RequestException as e:
                logger.error(f"Network error fetching {article_url}: {e}")
                return None
            except Exception as e:
                logger.error(f"Unexpected error processing {article_url}: {e}")
                return None
        
        return None
    
    def _extract_article_content(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Extract article content from parsed HTML.
        
        Args:
            soup: BeautifulSoup parsed HTML
            
        Returns:
            Extracted content text or None
        """
        # Remove unwanted elements first
        for element in soup(['nav', 'header', 'footer', 'aside', 'form', 'button']):
            element.decompose()
        
        # Remove elements with specific classes/ids that typically contain non-content
        for selector in ['.sidebar', '.navigation', '.menu', '.advertisement', 
                        '.ads', '#header', '#footer', '#comments', '.social-share',
                        '.related-posts', '.recommended', '.newsletter', '.popup']:
            for element in soup.select(selector):
                element.decompose()
        
        # Try to find article content using various selectors
        content_selectors = [
            'article',
            '[role="main"] article',
            'main article',
            '.post-content',
            '.entry-content',
            '.article-content',
            '.article__body',
            '.story-content',
            '.content-body',
            '.article-text',
            '.post-body',
            '.content',
            '#content',
            '.post',
            '.article-body',
            '.story-body',
            '[itemprop="articleBody"]',
            '.field-name-body',
            '.node-content',
            '.entry',
            '.single-content'
        ]
        
        for selector in content_selectors:
            elements = soup.select(selector)
            if elements:
                # Get the first matching element
                content_elem = elements[0]
                
                # Remove any remaining script/style tags within content
                for tag in content_elem(['script', 'style', 'noscript']):
                    tag.decompose()
                
                # Extract text with better formatting
                paragraphs = content_elem.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'li'])
                text_blocks = []
                
                for elem in paragraphs:
                    text = elem.get_text(strip=True)
                    if len(text) > 30:  # Filter out very short blocks
                        # Add appropriate spacing for headers
                        if elem.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                            text_blocks.append(f"\n{text}\n")
                        else:
                            text_blocks.append(text)
                
                if text_blocks and len('\n'.join(text_blocks)) > 200:
                    return '\n\n'.join(text_blocks)
        
        # Fallback: try to find the largest concentration of paragraphs
        all_paragraphs = soup.find_all('p')
        if all_paragraphs:
            # Group consecutive paragraphs
            text_blocks = []
            current_block = []
            
            for p in all_paragraphs:
                text = p.get_text(strip=True)
                if len(text) > 50:  # Ignore short paragraphs
                    current_block.append(text)
                elif current_block:
                    # End of a block
                    if len(current_block) > 2:  # At least 3 paragraphs
                        text_blocks.extend(current_block)
                    current_block = []
            
            # Don't forget the last block
            if len(current_block) > 2:
                text_blocks.extend(current_block)
            
            if text_blocks:
                return '\n\n'.join(text_blocks)
        
        # Last resort: get all text but filter aggressively
        body_text = soup.get_text(separator='\n', strip=True)
        lines = [line.strip() for line in body_text.split('\n') if len(line.strip()) > 50]
        
        if lines and len('\n'.join(lines)) > 500:
            return '\n'.join(lines)
        
        return None
    
    def _parse_date(self, date_tuple) -> Optional[datetime]:
        """
        Parse date from feedparser time tuple.
        
        Args:
            date_tuple: Time tuple from feedparser
            
        Returns:
            Datetime object or None
        """
        if not date_tuple:
            return None
            
        try:
            # Convert time tuple to datetime
            dt = datetime(*date_tuple[:6])
            
            # Make timezone-aware
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone=pytz.UTC)
                
            return dt
        except Exception as e:
            logger.debug(f"Error parsing date: {e}")
            return None
    
    def _calculate_content_hash(self, title: str, content: str, summary: str) -> str:
        """
        Calculate hash of content for deduplication.
        
        Args:
            title: Article title
            content: Article content
            summary: Article summary
            
        Returns:
            SHA256 hash of the content
        """
        content_to_hash = f"{title}{content}{summary}"
        return hashlib.sha256(content_to_hash.encode()).hexdigest()
    
    def fetch_sitemap_urls(self, sitemap_url: str) -> List[str]:
        """
        Fetch and parse URLs from a sitemap.
        
        Args:
            sitemap_url: URL of the sitemap
            
        Returns:
            List of URLs found in the sitemap
        """
        urls = []
        
        try:
            response = self.session.get(sitemap_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Parse XML
            soup = BeautifulSoup(response.content, 'lxml-xml')
            
            # Find all URL elements
            url_elements = soup.find_all('url')
            
            for url_elem in url_elements:
                loc = url_elem.find('loc')
                if loc and loc.text:
                    urls.append(loc.text.strip())
            
            # Also check for sitemap index (nested sitemaps)
            sitemap_elements = soup.find_all('sitemap')
            for sitemap_elem in sitemap_elements:
                loc = sitemap_elem.find('loc')
                if loc and loc.text:
                    # Recursively fetch nested sitemap
                    nested_urls = self.fetch_sitemap_urls(loc.text.strip())
                    urls.extend(nested_urls)
            
            logger.info(f"Found {len(urls)} URLs in sitemap {sitemap_url}")
            
        except Exception as e:
            logger.error(f"Error fetching sitemap {sitemap_url}: {e}")
            
        return urls