"""
Claude-based feed discovery module to intelligently find RSS feeds from websites.
"""
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import logging
from typing import List, Dict, Optional
import json
from anthropic import Anthropic
from django.conf import settings

logger = logging.getLogger(__name__)


class ClaudeFeedDiscoverer:
    """Uses Claude AI to intelligently discover RSS feeds from websites."""
    
    def __init__(self, base_url: str, timeout: int = 10):
        """
        Initialize the Claude feed discoverer.
        
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
        
        # Initialize Claude client
        api_key = settings.ANTHROPIC_API_KEY
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment variables")
        self.client = Anthropic(api_key=api_key)
    
    def _check_robots_txt(self) -> List[Dict]:
        """
        Check robots.txt for sitemap URLs.
        
        Returns:
            List of sitemap dictionaries
        """
        sitemaps = []
        try:
            robots_url = urljoin(self.base_url, '/robots.txt')
            response = self.session.get(robots_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Parse robots.txt for sitemap entries
            for line in response.text.split('\n'):
                line = line.strip()
                if line.lower().startswith('sitemap:'):
                    sitemap_url = line.split(':', 1)[1].strip()
                    if sitemap_url:
                        sitemaps.append({
                            'url': sitemap_url,
                            'title': f'Sitemap from robots.txt: {urlparse(sitemap_url).path}',
                            'type': 'SITEMAP'
                        })
                        logger.info(f"Found sitemap in robots.txt: {sitemap_url}")
        except Exception as e:
            logger.debug(f"Could not fetch robots.txt: {e}")
        
        return sitemaps
        
    def discover_with_claude(self) -> Dict[str, List[Dict]]:
        """
        Use Claude to analyze the website and discover RSS feeds and sitemaps.
        
        Returns:
            Dictionary with 'feeds' and 'sitemaps' lists
        """
        results = {
            'feeds': [],
            'sitemaps': []
        }
        
        try:
            # First check robots.txt for sitemaps
            robots_sitemaps = self._check_robots_txt()
            # Validate robots.txt sitemaps
            for sitemap_info in robots_sitemaps:
                validated = self._validate_sitemap_url(sitemap_info['url'])
                if validated:
                    results['sitemaps'].append(validated)
                    logger.info(f"Validated sitemap from robots.txt: {validated['url']}")
            
            # Fetch the homepage HTML
            response = self.session.get(self.base_url, timeout=self.timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'lxml')
            
            # Extract relevant information for Claude
            page_title = soup.find('title').text if soup.find('title') else ''
            
            # Get navigation links
            nav_links = []
            for nav in soup.find_all(['nav', 'header']):
                for link in nav.find_all('a', href=True):
                    href = link['href']
                    text = link.get_text(strip=True)
                    if text and href:
                        full_url = urljoin(self.base_url, href)
                        nav_links.append(f"{text}: {full_url}")
            
            # Get footer links
            footer_links = []
            for footer in soup.find_all('footer'):
                for link in footer.find_all('a', href=True):
                    href = link['href']
                    text = link.get_text(strip=True)
                    if text and href:
                        full_url = urljoin(self.base_url, href)
                        footer_links.append(f"{text}: {full_url}")
            
            # Get any existing RSS links from HTML
            rss_links = []
            for link in soup.find_all('link', type=['application/rss+xml', 'application/atom+xml']):
                if link.get('href'):
                    rss_links.append({
                        'url': urljoin(self.base_url, link['href']),
                        'title': link.get('title', '')
                    })
            
            # Prepare prompt for Claude
            prompt = self._create_discovery_prompt(
                page_title, 
                nav_links[:20],  # Limit to prevent token overflow
                footer_links[:20],
                rss_links
            )
            
            # Ask Claude to analyze and find feeds
            response = self.client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=2000,
                temperature=0,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )
            
            # Parse Claude's response
            claude_results = self._parse_claude_response(response.content[0].text)
            
            # Validate discovered feeds
            for feed_info in claude_results.get('feeds', []):
                validated = self._validate_feed_url(feed_info['url'])
                if validated:
                    results['feeds'].append(validated)
                    logger.info(f"Claude discovered valid feed: {validated['url']}")
            
            # Validate discovered sitemaps
            for sitemap_info in claude_results.get('sitemaps', []):
                validated = self._validate_sitemap_url(sitemap_info['url'])
                if validated:
                    results['sitemaps'].append(validated)
                    logger.info(f"Claude discovered valid sitemap: {validated['url']}")
                    
        except Exception as e:
            logger.error(f"Error in Claude feed discovery: {e}")
            
        return results
    
    def _create_discovery_prompt(self, page_title: str, nav_links: List[str], 
                                footer_links: List[str], existing_rss: List[Dict]) -> str:
        """
        Create a prompt for Claude to discover RSS feeds and sitemaps.
        
        Args:
            page_title: The page title
            nav_links: Navigation links from the page
            footer_links: Footer links from the page
            existing_rss: Already discovered RSS links
            
        Returns:
            Formatted prompt for Claude
        """
        prompt = f"""I'm analyzing the website: {self.base_url}
Page title: {page_title}

Navigation links found:
{chr(10).join(nav_links[:20]) if nav_links else 'None found'}

Footer links found:
{chr(10).join(footer_links[:20]) if footer_links else 'None found'}

RSS/Atom links already found in HTML:
{json.dumps(existing_rss, indent=2) if existing_rss else 'None found'}

Based on this information, please help me discover all RSS/Atom feeds AND XML sitemaps for this website. 

For RSS/Atom feeds, look for:
1. Links that mention RSS, Feed, Atom, Subscribe, or similar terms
2. Common RSS feed URL patterns (e.g., /feed, /rss, /atom)
3. Category-specific feeds (e.g., /category/news/feed)
4. Section-specific feeds (e.g., /movies/feed, /tv/feed, /business/feed)

For sitemaps, look for:
1. Links to sitemap.xml, sitemap_index.xml
2. Common sitemap patterns like /sitemap.xml, /sitemap-news.xml, /sitemap-videos.xml
3. Category-specific sitemaps (e.g., /sitemap-posts.xml, /sitemap-articles.xml)
4. Date-based sitemaps (e.g., /sitemap-2024.xml)

For news/media sites, also suggest checking common patterns like:
- /feed/
- /rss/
- /[category]/feed/
- /[category]/rss/
- /sitemap.xml
- /sitemap_index.xml
- /news-sitemap.xml

Return your findings as a JSON object with this format:
{{
  "feeds": [
    {{
      "url": "full_feed_url",
      "title": "Feed title or description",
      "type": "rss"
    }}
  ],
  "sitemaps": [
    {{
      "url": "full_sitemap_url",
      "title": "Sitemap description",
      "type": "sitemap"
    }}
  ]
}}

Important: 
- Provide complete URLs (starting with http:// or https://)
- Include both discovered feeds/sitemaps and suggested URLs to check
- For large media sites, suggest category-specific feed and sitemap patterns
- Always check for robots.txt which often contains sitemap locations
"""
        return prompt
    
    def _parse_claude_response(self, response_text: str) -> Dict[str, List[Dict]]:
        """
        Parse Claude's response to extract feed and sitemap information.
        
        Args:
            response_text: Claude's response text
            
        Returns:
            Dictionary with 'feeds' and 'sitemaps' lists
        """
        result = {'feeds': [], 'sitemaps': []}
        
        try:
            import re
            # First try to find a JSON object
            json_obj_match = re.search(r'\{[\s\S]*\}', response_text)
            
            if json_obj_match:
                try:
                    parsed_data = json.loads(json_obj_match.group(0))
                    
                    # Process feeds
                    if 'feeds' in parsed_data and isinstance(parsed_data['feeds'], list):
                        for feed in parsed_data['feeds']:
                            if isinstance(feed, dict) and 'url' in feed:
                                url = feed['url']
                                if not url.startswith(('http://', 'https://')):
                                    url = urljoin(self.base_url, url)
                                
                                result['feeds'].append({
                                    'url': url,
                                    'title': feed.get('title', ''),
                                    'type': 'RSS'
                                })
                    
                    # Process sitemaps
                    if 'sitemaps' in parsed_data and isinstance(parsed_data['sitemaps'], list):
                        for sitemap in parsed_data['sitemaps']:
                            if isinstance(sitemap, dict) and 'url' in sitemap:
                                url = sitemap['url']
                                if not url.startswith(('http://', 'https://')):
                                    url = urljoin(self.base_url, url)
                                
                                result['sitemaps'].append({
                                    'url': url,
                                    'title': sitemap.get('title', ''),
                                    'type': 'SITEMAP'
                                })
                                
                except json.JSONDecodeError:
                    # Fall back to looking for just an array (old format)
                    json_matches = re.findall(r'\[[\s\S]*?\]', response_text)
                    
                    for json_match in json_matches:
                        try:
                            parsed_feeds = json.loads(json_match)
                            
                            for feed in parsed_feeds:
                                if isinstance(feed, dict) and 'url' in feed:
                                    url = feed['url']
                                    if not url.startswith(('http://', 'https://')):
                                        url = urljoin(self.base_url, url)
                                    
                                    result['feeds'].append({
                                        'url': url,
                                        'title': feed.get('title', ''),
                                        'type': 'RSS'
                                    })
                            
                            if result['feeds']:
                                break
                                
                        except json.JSONDecodeError:
                            continue
                    
            if not result['feeds'] and not result['sitemaps']:
                logger.warning("Could not extract valid JSON from Claude's response")
                
        except Exception as e:
            logger.error(f"Error processing Claude's response: {e}")
            
        return result
    
    def _validate_feed_url(self, feed_url: str) -> Optional[Dict]:
        """
        Validate if a URL is actually a valid RSS/Atom feed.
        
        Args:
            feed_url: URL to validate
            
        Returns:
            Feed info dictionary if valid, None otherwise
        """
        try:
            import feedparser
            
            response = self.session.get(feed_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Try to parse as feed
            parsed = feedparser.parse(response.content)
            
            if parsed.entries or parsed.get('feed', {}):
                feed_type = 'ATOM' if 'atom' in parsed.get('version', '').lower() else 'RSS'
                
                return {
                    'url': feed_url,
                    'title': parsed.feed.get('title', ''),
                    'type': feed_type,
                    'description': parsed.feed.get('description', ''),
                    'valid': True
                }
            
        except Exception as e:
            logger.debug(f"Feed validation failed for {feed_url}: {e}")
            
        return None
    
    def _validate_sitemap_url(self, sitemap_url: str) -> Optional[Dict]:
        """
        Validate if a URL is actually a valid XML sitemap.
        
        Args:
            sitemap_url: URL to validate
            
        Returns:
            Sitemap info dictionary if valid, None otherwise
        """
        try:
            response = self.session.get(sitemap_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Check if it's XML content
            content_type = response.headers.get('content-type', '').lower()
            if 'xml' in content_type or sitemap_url.endswith('.xml'):
                # Try to parse as XML
                soup = BeautifulSoup(response.content, 'lxml-xml')
                
                # Check for sitemap or sitemapindex root element
                if soup.find('urlset') or soup.find('sitemapindex'):
                    return {
                        'url': sitemap_url,
                        'title': f'Sitemap: {urlparse(sitemap_url).path}',
                        'type': 'SITEMAP',
                        'valid': True
                    }
            
        except Exception as e:
            logger.debug(f"Sitemap validation failed for {sitemap_url}: {e}")
            
        return None
    
    def discover_feeds_intelligently(self) -> Dict[str, List[Dict]]:
        """
        Discover feeds using Claude AI with fallback to traditional methods.
        
        Returns:
            Dictionary with 'feeds' and 'sitemaps' lists
        """
        results = {
            'feeds': [],
            'sitemaps': []
        }
        
        try:
            # Try Claude-based discovery first
            claude_results = self.discover_with_claude()
            results['feeds'].extend(claude_results['feeds'])
            results['sitemaps'].extend(claude_results['sitemaps'])
            
            # If Claude didn't find many feeds, also try traditional discovery
            if len(results['feeds']) < 3:
                logger.info("Claude found few feeds, trying traditional discovery as fallback")
                from .feed_discovery import FeedDiscoverer
                
                traditional_discoverer = FeedDiscoverer(self.base_url)
                traditional_results = traditional_discoverer.discover_all()
                
                # Add feeds not already discovered by Claude
                existing_urls = {f['url'] for f in results['feeds']}
                for feed in traditional_results['feeds']:
                    if feed['url'] not in existing_urls:
                        results['feeds'].append(feed)
                
                results['sitemaps'].extend(traditional_results['sitemaps'])
                
        except Exception as e:
            logger.error(f"Error in intelligent feed discovery: {e}")
            # Fall back to traditional discovery
            from .feed_discovery import FeedDiscoverer
            
            traditional_discoverer = FeedDiscoverer(self.base_url)
            return traditional_discoverer.discover_all()
            
        return results
    
    def validate_feed(self, feed_url: str) -> Optional[Dict]:
        """
        Validate a feed URL by checking if it's a valid RSS/Atom feed.
        
        Args:
            feed_url: URL to validate
            
        Returns:
            Dictionary with feed info if valid, None otherwise
        """
        return self._validate_feed_url(feed_url)
    
    def discover_feeds_from_sitemap(self, sitemap_url: str) -> List[Dict]:
        """
        Discover RSS/Atom feeds from a sitemap URL.
        For Claude-based discovery, we don't need sitemaps.
        
        Args:
            sitemap_url: URL of the sitemap
            
        Returns:
            Empty list (Claude doesn't use sitemaps)
        """
        # Claude-based discovery doesn't need sitemaps
        return []