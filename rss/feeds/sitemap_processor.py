"""
Selective Sitemap Processor

This module provides intelligent sitemap processing that only fetches
recent content (within 48 hours by default) and filters out non-article pages.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
import logging
from django.utils import timezone
import pytz

logger = logging.getLogger(__name__)


class SelectiveSitemapProcessor:
    """Process sitemaps selectively, only fetching recent article content."""
    
    # Maximum age for content (default 48 hours)
    DEFAULT_MAX_AGE_HOURS = 48
    
    # Patterns that indicate a URL is likely an article
    ARTICLE_URL_PATTERNS = [
        r'/\d{4}/\d{2}/\d{2}/',  # Date in URL: /2024/12/25/
        r'/\d{4}-\d{2}-\d{2}',   # Date in URL: /2024-12-25
        r'/article[s]?/',
        r'/story/',
        r'/news/',
        r'/post[s]?/',
        r'/blog/',
        r'/(movies?|tv|television|sports?|tech|business|lifestyle|entertainment)/',
    ]
    
    # Patterns that indicate a URL is NOT an article
    EXCLUDE_URL_PATTERNS = [
        r'/tag[s]?/',
        r'/category/',
        r'/author/',
        r'/page/\d+',
        r'/search/',
        r'/about',
        r'/contact',
        r'/privacy',
        r'/terms',
        r'/sitemap',
        r'/feed',
        r'/rss',
        r'/archive',
        r'\.(pdf|zip|doc|docx|xls|xlsx)$',
    ]
    
    # High-value sitemap patterns (should be prioritized)
    HIGH_VALUE_SITEMAP_PATTERNS = [
        r'news[-_]?sitemap',
        r'article[-_]?sitemap',
        r'post[-_]?sitemap',
        r'recent',
        r'latest',
        r'chunk-0',  # Often the most recent chunk
        r'\d{4}[-_]\d{2}',  # Year-month sitemaps
    ]
    
    def __init__(self, max_age_hours: int = None):
        """
        Initialize the selective sitemap processor.
        
        Args:
            max_age_hours: Maximum age of content to process (default: 48 hours)
        """
        self.max_age_hours = max_age_hours or self.DEFAULT_MAX_AGE_HOURS
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; SelectiveRSSBot/1.0)'
        })
    
    def is_high_value_sitemap(self, sitemap_url: str) -> bool:
        """
        Check if a sitemap URL appears to be high-value (recent content).
        
        Args:
            sitemap_url: URL of the sitemap
            
        Returns:
            True if sitemap appears to contain recent content
        """
        url_lower = sitemap_url.lower()
        for pattern in self.HIGH_VALUE_SITEMAP_PATTERNS:
            if re.search(pattern, url_lower):
                return True
        return False
    
    def should_process_sitemap(self, sitemap_url: str) -> Tuple[bool, str]:
        """
        Determine if a sitemap should be processed based on its URL.
        
        Args:
            sitemap_url: URL of the sitemap
            
        Returns:
            Tuple of (should_process, reason)
        """
        url_lower = sitemap_url.lower()
        
        # Skip old year-specific sitemaps
        current_year = datetime.now().year
        year_match = re.search(r'(\d{4})', sitemap_url)
        if year_match:
            sitemap_year = int(year_match.group(1))
            if sitemap_year < current_year - 1:  # Skip sitemaps older than last year
                return False, f"Old sitemap from {sitemap_year}"
        
        # Prioritize high-value sitemaps
        if self.is_high_value_sitemap(sitemap_url):
            return True, "High-value sitemap pattern"
        
        # Skip if it looks like an archive
        if 'archive' in url_lower or 'old' in url_lower:
            return False, "Archive sitemap"
        
        # Default: process it
        return True, "Default processing"
    
    def parse_sitemap_date(self, date_str: str) -> Optional[datetime]:
        """
        Parse a date string from a sitemap.
        
        Args:
            date_str: Date string from sitemap
            
        Returns:
            Parsed datetime or None
        """
        if not date_str:
            return None
        
        # Common date formats in sitemaps
        date_formats = [
            '%Y-%m-%dT%H:%M:%S%z',
            '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%dT%H:%M:%S',  # No timezone (like The Ringer)
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d',
        ]
        
        for fmt in date_formats:
            try:
                dt = datetime.strptime(date_str.replace('+00:00', 'Z'), fmt)
                if not dt.tzinfo:
                    dt = pytz.UTC.localize(dt)
                return dt
            except (ValueError, AttributeError):
                continue
        
        return None
    
    def is_recent_content(self, lastmod: Optional[str]) -> bool:
        """
        Check if content is recent based on lastmod date.
        
        Args:
            lastmod: Last modification date from sitemap
            
        Returns:
            True if content is recent (within max_age_hours)
        """
        if not lastmod:
            return False  # No date = skip for safety
        
        parsed_date = self.parse_sitemap_date(lastmod)
        if not parsed_date:
            return False
        
        # Make timezone-aware for comparison
        if not parsed_date.tzinfo:
            parsed_date = pytz.UTC.localize(parsed_date)
        
        cutoff_date = timezone.now() - timedelta(hours=self.max_age_hours)
        return parsed_date >= cutoff_date
    
    def is_article_url(self, url: str) -> bool:
        """
        Determine if a URL is likely an article based on patterns.
        
        Args:
            url: URL to check
            
        Returns:
            True if URL appears to be an article
        """
        # Check exclusion patterns first
        for pattern in self.EXCLUDE_URL_PATTERNS:
            if re.search(pattern, url, re.IGNORECASE):
                return False
        
        # Check inclusion patterns
        for pattern in self.ARTICLE_URL_PATTERNS:
            if re.search(pattern, url, re.IGNORECASE):
                return True
        
        # Additional heuristic: URLs with long slugs are often articles
        path = urlparse(url).path
        if path:
            # Remove leading/trailing slashes
            path = path.strip('/')
            parts = path.split('/')
            
            # If the last part is a long slug, likely an article
            if parts and len(parts[-1]) > 20 and '-' in parts[-1]:
                return True
        
        return False
    
    def fetch_sitemap_urls(self, sitemap_url: str) -> List[Dict]:
        """
        Fetch and parse URLs from a sitemap, filtering for recent articles only.
        
        Args:
            sitemap_url: URL of the sitemap
            
        Returns:
            List of URL dictionaries with metadata
        """
        recent_urls = []
        
        try:
            logger.info(f"Fetching sitemap: {sitemap_url}")
            response = self.session.get(sitemap_url, timeout=10)
            response.raise_for_status()
            
            # Parse XML
            root = ET.fromstring(response.content)
            
            # Handle namespace (some use https, some use http)
            # Try to detect namespace from root element
            if root.tag.startswith('{'):
                actual_ns = root.tag.split('}')[0][1:]
            else:
                actual_ns = 'http://www.sitemaps.org/schemas/sitemap/0.9'
            
            ns = {'ns': actual_ns,
                  'news': 'http://www.google.com/schemas/sitemap-news/0.9'}
            
            # Check if this is a sitemap index
            sitemap_elements = root.findall('ns:sitemap', ns)
            if sitemap_elements:
                logger.info(f"Found sitemap index with {len(sitemap_elements)} sitemaps")
                
                # Process nested sitemaps
                for sitemap_elem in sitemap_elements:
                    loc_elem = sitemap_elem.find('ns:loc', ns)
                    lastmod_elem = sitemap_elem.find('ns:lastmod', ns)
                    
                    if loc_elem is not None and loc_elem.text:
                        nested_url = loc_elem.text.strip()
                        
                        # Check if nested sitemap should be processed
                        should_process, reason = self.should_process_sitemap(nested_url)
                        if not should_process:
                            logger.info(f"Skipping nested sitemap: {reason}")
                            continue
                        
                        # Check if nested sitemap is recent
                        if lastmod_elem is not None and lastmod_elem.text:
                            if not self.is_recent_content(lastmod_elem.text):
                                logger.info(f"Skipping old nested sitemap: {nested_url}")
                                continue
                        
                        # Recursively process nested sitemap
                        nested_urls = self.fetch_sitemap_urls(nested_url)
                        recent_urls.extend(nested_urls)
            else:
                # Regular sitemap - process URLs
                url_elements = root.findall('ns:url', ns)
                logger.info(f"Processing {len(url_elements)} URLs from sitemap")
                
                for url_elem in url_elements:
                    loc_elem = url_elem.find('ns:loc', ns)
                    lastmod_elem = url_elem.find('ns:lastmod', ns)
                    
                    if loc_elem is not None and loc_elem.text:
                        url = loc_elem.text.strip()
                        
                        # Check if URL is an article
                        if not self.is_article_url(url):
                            continue
                        
                        # Check if content is recent
                        lastmod = lastmod_elem.text if lastmod_elem is not None else None
                        if lastmod and not self.is_recent_content(lastmod):
                            continue
                        
                        # Check for news-specific metadata
                        news_elem = url_elem.find('news:news', ns)
                        publication_date = None
                        
                        if news_elem is not None:
                            pub_elem = news_elem.find('news:publication_date', ns)
                            if pub_elem is not None and pub_elem.text:
                                publication_date = pub_elem.text
                                
                                # Check if news article is recent
                                if not self.is_recent_content(publication_date):
                                    continue
                        
                        # Add URL with metadata
                        recent_urls.append({
                            'url': url,
                            'lastmod': lastmod,
                            'publication_date': publication_date,
                            'is_recent': True
                        })
            
            logger.info(f"Found {len(recent_urls)} recent article URLs in sitemap")
            
        except Exception as e:
            logger.error(f"Error fetching sitemap {sitemap_url}: {e}")
        
        return recent_urls
    
    def validate_article_content(self, url: str, content: str) -> bool:
        """
        Validate that fetched content is actually an article.
        
        Args:
            url: URL of the content
            content: HTML content
            
        Returns:
            True if content appears to be a valid article
        """
        if not content or len(content) < 500:
            return False
        
        try:
            soup = BeautifulSoup(content, 'html.parser')
            
            # Extract text
            text = soup.get_text(strip=True)
            word_count = len(text.split())
            
            # Minimum word count for articles
            if word_count < 200:
                return False
            
            # Check for article indicators
            has_title = bool(soup.find('h1')) or bool(soup.find('title'))
            has_paragraphs = len(soup.find_all('p')) > 3
            
            # Check for non-article indicators
            is_category_page = bool(soup.find_all('a', href=re.compile(r'/category/')))
            is_tag_page = bool(soup.find_all('a', href=re.compile(r'/tag/')))
            
            if is_category_page and not has_paragraphs:
                return False
            
            if is_tag_page and not has_paragraphs:
                return False
            
            return has_title and has_paragraphs
            
        except Exception as e:
            logger.error(f"Error validating content from {url}: {e}")
            return False
    
    def get_sitemap_priority(self, sitemap_url: str) -> int:
        """
        Get processing priority for a sitemap (higher = more important).
        
        Args:
            sitemap_url: URL of the sitemap
            
        Returns:
            Priority score (0-100)
        """
        score = 50  # Base score
        
        url_lower = sitemap_url.lower()
        
        # Boost for high-value patterns
        if self.is_high_value_sitemap(sitemap_url):
            score += 30
        
        # Boost for current year/month
        current_year = str(datetime.now().year)
        current_month = f"{datetime.now().month:02d}"
        
        if current_year in sitemap_url:
            score += 20
        if current_month in sitemap_url:
            score += 10
        
        # Penalty for old/archive
        if 'archive' in url_lower or 'old' in url_lower:
            score -= 30
        
        # Boost for news/recent
        if 'news' in url_lower or 'recent' in url_lower or 'latest' in url_lower:
            score += 15
        
        return max(0, min(100, score))  # Clamp to 0-100