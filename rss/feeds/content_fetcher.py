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

logger = logging.getLogger(__name__)


class ContentFetcher:
    """Fetches and processes content from RSS feeds and web pages."""
    
    def __init__(self, timeout: int = 15):
        """
        Initialize the content fetcher.
        
        Args:
            timeout: Request timeout in seconds
        """
        self.timeout = timeout
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
            
            # Get content/summary
            content = ''
            summary = entry.get('summary', '')
            
            # Try to get full content from various fields
            if hasattr(entry, 'content'):
                for content_item in entry.content:
                    if content_item.get('value'):
                        content = content_item['value']
                        break
            
            # If no content, use summary
            if not content:
                content = summary
            
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
    
    def fetch_article_content(self, article_url: str) -> Optional[str]:
        """
        Fetch and extract main content from an article web page.
        
        Args:
            article_url: URL of the article
            
        Returns:
            Extracted article content or None
        """
        try:
            response = self.session.get(article_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Parse HTML
            soup = BeautifulSoup(response.content, 'lxml')
            
            # Remove script and style elements
            for script in soup(['script', 'style']):
                script.decompose()
            
            # Try to find article content using common patterns
            content = self._extract_article_content(soup)
            
            if content:
                logger.info(f"Successfully extracted content from {article_url}")
                return content
            else:
                logger.warning(f"Could not extract content from {article_url}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"Error fetching article {article_url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error processing article {article_url}: {e}")
            return None
    
    def _extract_article_content(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Extract article content from parsed HTML.
        
        Args:
            soup: BeautifulSoup parsed HTML
            
        Returns:
            Extracted content text or None
        """
        # Try to find article content using various selectors
        content_selectors = [
            'article',
            'main',
            '[role="main"]',
            '.post-content',
            '.entry-content',
            '.article-content',
            '.content',
            '#content',
            '.post',
            '.article-body',
            '.story-body',
            '[itemprop="articleBody"]'
        ]
        
        for selector in content_selectors:
            elements = soup.select(selector)
            if elements:
                # Get the first matching element
                content_elem = elements[0]
                
                # Extract text
                text = content_elem.get_text(separator='\n', strip=True)
                
                # Only return if we have substantial content
                if len(text) > 200:
                    return text
        
        # Fallback: try to find the largest text block
        paragraphs = soup.find_all('p')
        if paragraphs:
            text_blocks = []
            for p in paragraphs:
                text = p.get_text(strip=True)
                if len(text) > 50:  # Ignore short paragraphs
                    text_blocks.append(text)
            
            if text_blocks:
                return '\n\n'.join(text_blocks)
        
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