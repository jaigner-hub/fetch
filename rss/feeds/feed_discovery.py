"""
Feed discovery module to find RSS feeds and sitemaps from websites.
"""
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import feedparser
import logging
from typing import List, Dict, Optional
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

logger = logging.getLogger(__name__)


class FeedDiscoverer:
    """Discovers RSS feeds and sitemaps from websites."""
    
    # Common feed URL patterns to check
    COMMON_FEED_PATHS = [
        '/rss',
        '/rss.xml',
        '/feed',
        '/feed.xml',
        '/feeds',
        '/atom',
        '/atom.xml',
        '/index.rss',
        '/index.xml',
        '/blog/rss',
        '/blog/feed',
        '/news/rss',
        '/news/feed',
    ]
    
    # Common category patterns for news sites
    CATEGORY_PATTERNS = [
        'movies', 'tv', 'television', 'business', 'tech', 'technology',
        'lifestyle', 'awards', 'news', 'features', 'reviews', 'politics',
        'international', 'asia', 'europe', 'video', 'lists', 'opinion',
        'sports', 'entertainment', 'culture', 'arts', 'science', 'health',
        'fashion', 'style', 'food', 'travel', 'music', 'books'
    ]
    
    # Subcategory patterns (combined with categories)
    SUBCATEGORY_PATTERNS = [
        '{}-news', '{}-reviews', '{}-features', '{}-latest',
        '{}/{}-news', '{}/{}-reviews', '{}/{}-features'
    ]
    
    # Special section patterns
    SPECIAL_SECTIONS = [
        'heat-vision', 'live-feed', 'behind-the-screen', 'breaking-news',
        'trending', 'latest', 'popular', 'featured', 'exclusive'
    ]
    
    # Studio/Network specific patterns (for entertainment sites)
    STUDIO_PATTERNS = [
        't/netflix', 't/disney', 't/warner-bros', 't/amazon', 't/paramount',
        't/hbo', 't/apple', 't/hulu', 't/nbc', 't/cbs', 't/abc', 't/fox'
    ]
    
    # Common sitemap paths
    SITEMAP_PATHS = [
        '/sitemap.xml',
        '/sitemap_index.xml',
        '/sitemap',
        '/sitemaps.xml',
    ]
    
    def __init__(self, base_url: str, timeout: int = 10):
        """
        Initialize the feed discoverer.
        
        Args:
            base_url: The base URL of the website to discover feeds from
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; RSS Feed Discoverer/1.0)'
        })
        
    def discover_all(self) -> Dict[str, List[Dict]]:
        """
        Discover all feeds and sitemaps from the website.
        
        Returns:
            Dictionary with 'feeds' and 'sitemaps' lists
        """
        results = {
            'feeds': [],
            'sitemaps': []
        }
        
        # Try to discover from HTML
        html_feeds = self._discover_from_html()
        results['feeds'].extend(html_feeds)
        
        # Try common feed paths
        common_feeds = self._check_common_paths()
        results['feeds'].extend(common_feeds)
        
        # Try category-based feed discovery
        category_feeds = self._check_category_feeds()
        results['feeds'].extend(category_feeds)
        
        # Try to discover sitemaps
        sitemaps = self._discover_sitemaps()
        results['sitemaps'].extend(sitemaps)
        
        # Remove duplicates
        results['feeds'] = self._deduplicate_feeds(results['feeds'])
        results['sitemaps'] = self._deduplicate_feeds(results['sitemaps'])
        
        return results
    
    def _discover_from_html(self) -> List[Dict]:
        """
        Discover feeds from HTML link tags.
        
        Returns:
            List of discovered feed dictionaries
        """
        feeds = []
        
        try:
            response = self.session.get(self.base_url, timeout=self.timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'lxml')
            
            # Look for RSS/Atom links in HTML head
            feed_types = [
                'application/rss+xml',
                'application/atom+xml',
                'application/rdf+xml',
                'application/feed+json',
                'text/xml'
            ]
            
            for feed_type in feed_types:
                links = soup.find_all('link', type=feed_type)
                for link in links:
                    if link.get('href'):
                        feed_url = urljoin(self.base_url, link['href'])
                        feed_info = {
                            'url': feed_url,
                            'title': link.get('title', ''),
                            'type': self._determine_feed_type(feed_url, feed_type)
                        }
                        feeds.append(feed_info)
                        logger.info(f"Found feed from HTML: {feed_url}")
            
            # Also look for alternate links
            alternates = soup.find_all('link', rel='alternate')
            for link in alternates:
                if link.get('type') in feed_types and link.get('href'):
                    feed_url = urljoin(self.base_url, link['href'])
                    feed_info = {
                        'url': feed_url,
                        'title': link.get('title', ''),
                        'type': self._determine_feed_type(feed_url, link.get('type'))
                    }
                    if feed_info not in feeds:
                        feeds.append(feed_info)
                        logger.info(f"Found alternate feed: {feed_url}")
                        
        except requests.RequestException as e:
            logger.error(f"Error fetching HTML from {self.base_url}: {e}")
        except Exception as e:
            logger.error(f"Error parsing HTML from {self.base_url}: {e}")
            
        return feeds
    
    def _check_common_paths(self) -> List[Dict]:
        """
        Check common feed URL paths.
        
        Returns:
            List of discovered feed dictionaries
        """
        feeds = []
        
        for path in self.COMMON_FEED_PATHS:
            feed_url = urljoin(self.base_url, path)
            
            try:
                response = self.session.head(feed_url, timeout=self.timeout, allow_redirects=True)
                
                # If HEAD request succeeds, try to validate it's actually a feed
                if response.status_code == 200:
                    # Do a GET request to validate
                    response = self.session.get(feed_url, timeout=self.timeout)
                    
                    # Try to parse as feed
                    parsed = feedparser.parse(response.content)
                    
                    if parsed.entries or parsed.get('feed', {}):
                        feed_info = {
                            'url': feed_url,
                            'title': parsed.feed.get('title', ''),
                            'type': self._determine_feed_type_from_parsed(parsed)
                        }
                        feeds.append(feed_info)
                        logger.info(f"Found feed at common path: {feed_url}")
                        
            except requests.RequestException:
                # Silently skip - these are just guesses
                pass
            except Exception as e:
                logger.debug(f"Error checking {feed_url}: {e}")
                
        return feeds
    
    def _check_category_feeds(self) -> List[Dict]:
        """
        Check category-based feed URLs using parallel processing.
        
        Returns:
            List of discovered feed dictionaries
        """
        feeds = []
        feed_suffixes = ['/feed', '/rss']
        urls_to_check = []
        
        # Build list of URLs to check
        # Main categories - only check most common patterns to save time
        for category in self.CATEGORY_PATTERNS[:15]:  # Limit to first 15 most common
            for suffix in feed_suffixes:
                # Try only the most common URL patterns
                patterns = [
                    f'/{category}{suffix}',  # /movies/feed
                    f'/c/{category}{suffix}',  # /c/movies/feed
                ]
                
                for pattern in patterns:
                    feed_url = urljoin(self.base_url, pattern)
                    urls_to_check.append(('category', feed_url))
        
        # Check subcategories (e.g., movies/movie-news)
        for category in ['movies', 'tv', 'business', 'lifestyle']:
            subcategories = [
                f'{category}/{category}-news', f'{category}/{category}-reviews', 
                f'{category}/{category}-features'
            ]
            
            for subcat in subcategories:
                for suffix in feed_suffixes:
                    feed_url = urljoin(self.base_url, f'/{subcat}{suffix}')
                    urls_to_check.append(('subcategory', feed_url))
        
        # Check special sections (limit to most common)
        for section in self.SPECIAL_SECTIONS[:5]:
            for suffix in feed_suffixes:
                feed_url = urljoin(self.base_url, f'/{section}{suffix}')
                urls_to_check.append(('special', feed_url))
        
        # Check studio/network specific feeds
        for studio in self.STUDIO_PATTERNS:
            for suffix in feed_suffixes:
                feed_url = urljoin(self.base_url, f'/{studio}{suffix}')
                urls_to_check.append(('studio', feed_url))
        
        # Process URLs in parallel
        logger.info(f"Checking {len(urls_to_check)} potential category feed URLs...")
        with ThreadPoolExecutor(max_workers=10) as executor:
            # Submit all tasks
            future_to_url = {
                executor.submit(self._validate_feed_url, url): (feed_type, url) 
                for feed_type, url in urls_to_check
            }
            
            # Collect results
            for future in as_completed(future_to_url):
                feed_type, url = future_to_url[future]
                try:
                    feed_info = future.result(timeout=10)
                    if feed_info:
                        feeds.append(feed_info)
                        logger.info(f"Found {feed_type} feed: {url}")
                except Exception as e:
                    logger.debug(f"Error checking {url}: {e}")
        
        return feeds
    
    def _validate_feed_url(self, feed_url: str) -> Optional[Dict]:
        """
        Quick validation of a feed URL using HEAD request first.
        
        Args:
            feed_url: URL to validate
            
        Returns:
            Feed info dictionary if valid, None otherwise
        """
        try:
            # First try HEAD request without following redirects
            response = self.session.head(feed_url, timeout=3, allow_redirects=False)
            
            # Check if it's a redirect with RSS content type
            if response.status_code in [301, 302]:
                content_type = response.headers.get('content-type', '').lower()
                if any(x in content_type for x in ['xml', 'rss', 'atom', 'feed']):
                    # It's a feed redirect, get the final URL
                    final_url = response.headers.get('location', feed_url)
                    if not final_url.startswith('http'):
                        final_url = urljoin(feed_url, final_url)
                    return {
                        'url': final_url,
                        'title': '',
                        'type': 'RSS' if 'rss' in content_type else 'ATOM',
                        'valid': True
                    }
            
            # Now follow redirects for actual check
            response = self.session.head(feed_url, timeout=3, allow_redirects=True)
            
            if response.status_code == 200:
                # Check content type
                content_type = response.headers.get('content-type', '').lower()
                if any(x in content_type for x in ['xml', 'rss', 'atom', 'feed']):
                    # Likely a feed
                    return {
                        'url': feed_url,
                        'title': '',
                        'type': 'RSS' if 'rss' in content_type else 'ATOM',
                        'valid': True
                    }
                    
        except requests.Timeout:
            logger.debug(f"Timeout checking {feed_url}")
        except requests.RequestException as e:
            logger.debug(f"Request error for {feed_url}: {e}")
        except Exception as e:
            logger.debug(f"Error validating {feed_url}: {e}")
            
        return None
    
    def _discover_sitemaps(self) -> List[Dict]:
        """
        Discover sitemaps from robots.txt and common paths.
        
        Returns:
            List of discovered sitemap dictionaries
        """
        sitemaps = []
        sitemap_urls = []
        
        # Check robots.txt for sitemaps
        robots_url = urljoin(self.base_url, '/robots.txt')
        try:
            response = self.session.get(robots_url, timeout=self.timeout)
            if response.status_code == 200:
                for line in response.text.splitlines():
                    if line.lower().startswith('sitemap:'):
                        sitemap_url = line.split(':', 1)[1].strip()
                        if not sitemap_url.startswith('http'):
                            sitemap_url = urljoin(self.base_url, sitemap_url)
                        sitemap_urls.append(sitemap_url)
                        logger.info(f"Found sitemap in robots.txt: {sitemap_url}")
                        
        except requests.RequestException as e:
            logger.debug(f"Error fetching robots.txt: {e}")
            
        # Check common sitemap paths
        for path in self.SITEMAP_PATHS:
            sitemap_url = urljoin(self.base_url, path)
            
            try:
                response = self.session.head(sitemap_url, timeout=self.timeout, allow_redirects=True)
                
                if response.status_code == 200:
                    if sitemap_url not in sitemap_urls:
                        sitemap_urls.append(sitemap_url)
                        logger.info(f"Found sitemap at common path: {sitemap_url}")
                        
            except requests.RequestException:
                # Silently skip
                pass
        
        # Process all discovered sitemap URLs and expand sitemap indexes
        for url in sitemap_urls:
            expanded_sitemaps = self._expand_sitemap(url)
            sitemaps.extend(expanded_sitemaps)
                
        return sitemaps
    
    def discover_feeds_from_sitemap(self, sitemap_url: str) -> List[Dict]:
        """
        Discover RSS/Atom feed URLs from a sitemap.
        
        Args:
            sitemap_url: URL of the sitemap to analyze
            
        Returns:
            List of discovered feed dictionaries
        """
        feeds = []
        
        try:
            response = self.session.get(sitemap_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Parse XML
            root = ET.fromstring(response.content)
            ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
            
            # Check if this is a sitemap index
            sitemap_elements = root.findall('ns:sitemap', ns)
            if sitemap_elements:
                # Process nested sitemaps
                for sitemap_elem in sitemap_elements:
                    loc_elem = sitemap_elem.find('ns:loc', ns)
                    if loc_elem is not None and loc_elem.text:
                        nested_url = loc_elem.text.strip()
                        # Recursively discover feeds from nested sitemaps
                        nested_feeds = self.discover_feeds_from_sitemap(nested_url)
                        feeds.extend(nested_feeds)
            else:
                # Regular sitemap - look for RSS/Atom feed URLs
                url_elements = root.findall('ns:url', ns)
                for url_elem in url_elements:
                    loc_elem = url_elem.find('ns:loc', ns)
                    if loc_elem is not None and loc_elem.text:
                        url = loc_elem.text.strip()
                        # Check if this URL looks like a feed
                        if self._is_feed_url(url):
                            # Validate it's actually a feed
                            feed_info = self.validate_feed(url)
                            if feed_info:
                                feeds.append(feed_info)
                                logger.info(f"Found feed in sitemap: {url}")
                                
        except Exception as e:
            logger.error(f"Error processing sitemap {sitemap_url}: {e}")
            
        return feeds
    
    def _is_feed_url(self, url: str) -> bool:
        """
        Check if a URL looks like it might be a feed URL.
        
        Args:
            url: URL to check
            
        Returns:
            True if URL appears to be a feed
        """
        import re
        url_lower = url.lower()
        
        # Exclude URLs that look like articles (have long numeric IDs)
        if re.search(r'/\d{6,}/', url):
            return False
            
        feed_indicators = [
            '/rss', '/feed', '/atom', '.rss', '.xml',
            '/blog/feed', '/news/feed', '/blog/rss', '/news/rss'
        ]
        
        return any(indicator in url_lower for indicator in feed_indicators)
    
    def _expand_sitemap(self, sitemap_url: str, max_depth: int = 2, current_depth: int = 0) -> List[Dict]:
        """
        Expand a sitemap URL, handling sitemap index files.
        NOTE: This now only returns sitemap metadata, not for article fetching.
        
        Args:
            sitemap_url: URL of the sitemap to expand
            max_depth: Maximum depth for recursive expansion
            current_depth: Current recursion depth
            
        Returns:
            List of sitemap dictionaries (for reference only)
        """
        sitemaps = []
        
        if current_depth >= max_depth:
            # Max depth reached, just return the sitemap as-is
            sitemaps.append({
                'url': sitemap_url,
                'title': 'Sitemap',
                'type': 'SITEMAP'
            })
            return sitemaps
        
        try:
            response = self.session.get(sitemap_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Try to parse as XML
            try:
                root = ET.fromstring(response.content)
                
                # Define namespace
                ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
                
                # Check if this is a sitemap index (contains <sitemap> elements)
                sitemap_elements = root.findall('ns:sitemap', ns)
                
                if sitemap_elements:
                    # This is a sitemap index - expand all referenced sitemaps
                    logger.info(f"Found sitemap index at {sitemap_url} with {len(sitemap_elements)} sitemaps")
                    
                    for sitemap_elem in sitemap_elements:
                        loc_elem = sitemap_elem.find('ns:loc', ns)
                        if loc_elem is not None and loc_elem.text:
                            nested_url = loc_elem.text.strip()
                            logger.info(f"Found nested sitemap: {nested_url}")
                            
                            # Recursively expand nested sitemaps
                            nested_sitemaps = self._expand_sitemap(
                                nested_url, 
                                max_depth=max_depth, 
                                current_depth=current_depth + 1
                            )
                            sitemaps.extend(nested_sitemaps)
                else:
                    # This is a regular sitemap (contains <url> elements)
                    url_elements = root.findall('ns:url', ns)
                    
                    if url_elements:
                        # Valid sitemap with URLs
                        sitemaps.append({
                            'url': sitemap_url,
                            'title': f'Sitemap ({len(url_elements)} URLs)',
                            'type': 'SITEMAP'
                        })
                        logger.info(f"Found regular sitemap at {sitemap_url} with {len(url_elements)} URLs")
                    else:
                        # Empty or unrecognized format
                        sitemaps.append({
                            'url': sitemap_url,
                            'title': 'Sitemap',
                            'type': 'SITEMAP'
                        })
                        
            except ET.ParseError as e:
                # Not valid XML, treat as regular sitemap
                logger.debug(f"Could not parse {sitemap_url} as XML: {e}")
                sitemaps.append({
                    'url': sitemap_url,
                    'title': 'Sitemap',
                    'type': 'SITEMAP'
                })
                
        except requests.RequestException as e:
            logger.error(f"Error fetching sitemap {sitemap_url}: {e}")
            # Still add it as it might be accessible later
            sitemaps.append({
                'url': sitemap_url,
                'title': 'Sitemap (unreachable)',
                'type': 'SITEMAP'
            })
            
        return sitemaps
    
    def _determine_feed_type(self, url: str, content_type: str) -> str:
        """
        Determine feed type from URL and content type.
        
        Args:
            url: Feed URL
            content_type: Content-Type header value
            
        Returns:
            Feed type (RSS, ATOM, or SITEMAP)
        """
        url_lower = url.lower()
        content_lower = content_type.lower()
        
        if 'atom' in url_lower or 'atom' in content_lower:
            return 'ATOM'
        elif 'sitemap' in url_lower:
            return 'SITEMAP'
        else:
            return 'RSS'
    
    def _determine_feed_type_from_parsed(self, parsed) -> str:
        """
        Determine feed type from parsed feed object.
        
        Args:
            parsed: Parsed feedparser object
            
        Returns:
            Feed type (RSS or ATOM)
        """
        if hasattr(parsed, 'version'):
            if 'atom' in parsed.version.lower():
                return 'ATOM'
        return 'RSS'
    
    def _deduplicate_feeds(self, feeds: List[Dict]) -> List[Dict]:
        """
        Remove duplicate feeds based on URL.
        
        Args:
            feeds: List of feed dictionaries
            
        Returns:
            Deduplicated list of feeds
        """
        seen_urls = set()
        unique_feeds = []
        
        for feed in feeds:
            if feed['url'] not in seen_urls:
                seen_urls.add(feed['url'])
                unique_feeds.append(feed)
                
        return unique_feeds
    
    def validate_feed(self, feed_url: str) -> Optional[Dict]:
        """
        Validate if a URL is actually a valid feed.
        
        Args:
            feed_url: URL to validate
            
        Returns:
            Feed info dictionary if valid, None otherwise
        """
        try:
            response = self.session.get(feed_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Try to parse as feed
            parsed = feedparser.parse(response.content)
            
            if parsed.entries or parsed.get('feed', {}):
                return {
                    'url': feed_url,
                    'title': parsed.feed.get('title', ''),
                    'type': self._determine_feed_type_from_parsed(parsed),
                    'description': parsed.feed.get('description', ''),
                    'valid': True
                }
            
        except Exception as e:
            logger.error(f"Error validating feed {feed_url}: {e}")
            
        return None