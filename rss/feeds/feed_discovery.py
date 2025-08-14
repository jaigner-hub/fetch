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
    
    def _expand_sitemap(self, sitemap_url: str, max_depth: int = 2, current_depth: int = 0) -> List[Dict]:
        """
        Expand a sitemap URL, handling sitemap index files.
        
        Args:
            sitemap_url: URL of the sitemap to expand
            max_depth: Maximum depth for recursive expansion
            current_depth: Current recursion depth
            
        Returns:
            List of sitemap dictionaries
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