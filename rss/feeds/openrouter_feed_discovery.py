"""
OpenRouter-based feed discovery module to intelligently find RSS feeds from websites.
Uses OpenRouter.ai API to access Claude and other models.
"""
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import logging
from typing import List, Dict, Optional
import json
from django.conf import settings

logger = logging.getLogger(__name__)


class OpenRouterFeedDiscoverer:
    """Uses OpenRouter AI to intelligently discover RSS feeds from websites."""
    
    # OpenRouter API endpoint
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
    
    # Model to use (Claude 3.5 Sonnet via OpenRouter)
    MODEL = "anthropic/claude-3.5-sonnet"
    
    def __init__(self, base_url: str, timeout: int = 10):
        """
        Initialize the OpenRouter feed discoverer.
        
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
        
        # Get OpenRouter API key
        self.api_key = getattr(settings, 'OPENROUTER_API_KEY', None)
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not found in settings")
    
    def _make_openrouter_request(self, prompt: str) -> Optional[str]:
        """
        Make a request to OpenRouter API.
        
        Args:
            prompt: The prompt to send to the AI
            
        Returns:
            The AI response text or None if error
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.base_url,  # Required by OpenRouter
            "X-Title": "RSS Feed Discovery"  # Optional but recommended
        }
        
        data = {
            "model": self.MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an expert at finding RSS feeds and sitemaps on websites. Provide responses in valid JSON format only."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.3,
            "max_tokens": 2000
        }
        
        try:
            response = requests.post(
                self.OPENROUTER_API_URL,
                headers=headers,
                json=data,
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                return result['choices'][0]['message']['content']
            else:
                logger.error(f"Unexpected OpenRouter response format: {result}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"OpenRouter API request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Error processing OpenRouter response: {e}")
            return None
    
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
            if response.status_code == 200:
                for line in response.text.splitlines():
                    if line.lower().startswith('sitemap:'):
                        sitemap_url = line.split(':', 1)[1].strip()
                        if not sitemap_url.startswith('http'):
                            sitemap_url = urljoin(self.base_url, sitemap_url)
                        sitemaps.append({
                            'url': sitemap_url,
                            'type': 'SITEMAP',
                            'title': 'Sitemap from robots.txt'
                        })
        except:
            pass
        return sitemaps
    
    def _fetch_homepage_html(self) -> Optional[str]:
        """
        Fetch the homepage HTML.
        
        Returns:
            HTML content or None
        """
        try:
            response = self.session.get(self.base_url, timeout=self.timeout)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.error(f"Error fetching homepage: {e}")
            return None
    
    def discover_feeds_intelligently(self) -> Dict[str, List[Dict]]:
        """
        Use OpenRouter AI to intelligently discover feeds from the website.
        
        Returns:
            Dictionary with 'feeds' and 'sitemaps' lists
        """
        results = {
            'feeds': [],
            'sitemaps': []
        }
        
        # First check robots.txt for sitemaps
        sitemaps = self._check_robots_txt()
        results['sitemaps'].extend(sitemaps)
        
        # Fetch homepage HTML
        html_content = self._fetch_homepage_html()
        if not html_content:
            logger.warning(f"Could not fetch homepage for {self.base_url}")
            return results
        
        # Parse HTML to extract potential feed links
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Look for feed links in HTML
        feed_links = []
        for link in soup.find_all('link', type=['application/rss+xml', 'application/atom+xml']):
            href = link.get('href')
            if href:
                feed_url = urljoin(self.base_url, href)
                feed_links.append({
                    'url': feed_url,
                    'title': link.get('title', ''),
                    'type': 'ATOM' if 'atom' in link.get('type', '').lower() else 'RSS'
                })
        
        # Extract navigation links for AI analysis
        nav_links = []
        for link in soup.find_all('a', href=True)[:100]:  # Limit to first 100 links
            href = link['href']
            text = link.get_text(strip=True)
            if text and len(text) < 50:  # Skip very long link texts
                nav_links.append({'text': text, 'href': href})
        
        # Prepare prompt for OpenRouter
        prompt = f"""Analyze this website's navigation and suggest RSS feed URLs.

Website: {self.base_url}
Domain: {urlparse(self.base_url).netloc}

Found feed links in HTML:
{json.dumps(feed_links, indent=2) if feed_links else 'None found'}

Navigation links (first 100):
{json.dumps(nav_links[:30], indent=2)}

Based on this information, suggest likely RSS/Atom feed URLs for this website.
Consider common patterns like /feed, /rss, /atom, /feeds, category feeds, etc.

Return a JSON object with this exact structure:
{{
    "feeds": [
        {{"url": "full_url", "type": "RSS_or_ATOM", "title": "Feed Title", "confidence": "high/medium/low"}},
        ...
    ],
    "sitemaps": [
        {{"url": "full_url", "type": "SITEMAP", "title": "Sitemap Title"}},
        ...
    ]
}}

Focus on finding actual content feeds, not just any XML file.
Include confidence levels for feeds (high = found in HTML, medium = common pattern, low = guess).
"""
        
        # Get AI suggestions
        ai_response = self._make_openrouter_request(prompt)
        
        if ai_response:
            try:
                # Parse AI response
                ai_data = json.loads(ai_response)
                
                # Add discovered feeds
                for feed in ai_data.get('feeds', []):
                    # Validate URL
                    feed_url = feed.get('url', '')
                    if not feed_url.startswith('http'):
                        feed_url = urljoin(self.base_url, feed_url)
                    
                    # Check if feed is valid before adding
                    if self._validate_feed_url(feed_url):
                        results['feeds'].append({
                            'url': feed_url,
                            'type': feed.get('type', 'RSS'),
                            'title': feed.get('title', ''),
                            'confidence': feed.get('confidence', 'medium')
                        })
                
                # Add discovered sitemaps
                for sitemap in ai_data.get('sitemaps', []):
                    sitemap_url = sitemap.get('url', '')
                    if not sitemap_url.startswith('http'):
                        sitemap_url = urljoin(self.base_url, sitemap_url)
                    
                    # Only add if not already in results
                    if not any(s['url'] == sitemap_url for s in results['sitemaps']):
                        results['sitemaps'].append({
                            'url': sitemap_url,
                            'type': 'SITEMAP',
                            'title': sitemap.get('title', 'Sitemap')
                        })
                        
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse AI response as JSON: {e}")
                logger.debug(f"AI response was: {ai_response}")
        
        # Add any feeds found in HTML that weren't included by AI
        for feed_link in feed_links:
            if not any(f['url'] == feed_link['url'] for f in results['feeds']):
                results['feeds'].append(feed_link)
        
        # Remove duplicates
        results['feeds'] = self._deduplicate_feeds(results['feeds'])
        results['sitemaps'] = self._deduplicate_feeds(results['sitemaps'])
        
        return results
    
    def _validate_feed_url(self, url: str) -> bool:
        """
        Quick validation to check if a URL might be a feed.
        
        Args:
            url: URL to validate
            
        Returns:
            True if URL might be valid
        """
        try:
            # Quick HEAD request
            response = self.session.head(url, timeout=5, allow_redirects=True)
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '').lower()
                return any(x in content_type for x in ['xml', 'rss', 'atom', 'feed'])
            return False
        except:
            # If we can't validate, include it anyway
            return True
    
    def _deduplicate_feeds(self, feeds: List[Dict]) -> List[Dict]:
        """
        Remove duplicate feeds based on URL.
        
        Args:
            feeds: List of feed dictionaries
            
        Returns:
            Deduplicated list
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
            import feedparser
            
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