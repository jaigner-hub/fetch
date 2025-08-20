"""
Article Detection Module

This module provides intelligent detection of actual news/content articles
from sitemap URLs, filtering out non-article pages like category listings,
about pages, and other non-content URLs.
"""

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs
import requests
from bs4 import BeautifulSoup
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class ArticleDetector:
    """Detects whether a URL is likely to be an article."""
    
    # Positive indicators that URL is likely an article
    ARTICLE_PATTERNS = [
        # Date patterns in URL
        r'/\d{4}/\d{2}/\d{2}/',  # /2024/08/20/
        r'/\d{4}-\d{2}-\d{2}/',  # /2024-08-20/
        r'/\d{4}/\d{2}/',        # /2024/08/
        
        # Article-specific paths
        r'/article[s]?/',
        r'/story/',
        r'/news/',
        r'/post[s]?/',
        r'/blog/',
        r'/entry/',
        r'/content/',
        
        # ID patterns
        r'/p/\d+',              # /p/12345
        r'/-\d+',               # /-12345
        r'/\d{4,}',             # /12345 (4+ digits)
        r'/[a-z0-9]{8,}$',      # Long alphanumeric ID at end
        
        # Category/topic paths (often contain articles)
        r'/movies?/',
        r'/tv/',
        r'/television/',
        r'/entertainment/',
        r'/sports?/',
        r'/politics?/',
        r'/tech(?:nology)?/',
        r'/business/',
        r'/lifestyle/',
        r'/culture/',
        r'/music/',
        r'/gaming/',
        r'/reviews?/',
        r'/features?/',
        r'/interviews?/',
        r'/exclusive/',
        r'/breaking/',
        
        # Specific article indicators
        r'[?&]p=\d+',           # ?p=12345 (WordPress)
        r'[?&]id=\d+',          # ?id=12345
        r'\.html?$',            # Ends with .html or .htm
    ]
    
    # Negative indicators - likely NOT articles
    EXCLUSION_PATTERNS = [
        # Static pages
        r'/about(?:-us)?/?$',
        r'/contact(?:-us)?/?$',
        r'/privacy(?:-policy)?/?$',
        r'/terms(?:-of-service)?/?$',
        r'/disclaimer/?$',
        r'/legal/?$',
        r'/advertise/?$',
        r'/careers?/?$',
        r'/help/?$',
        r'/faq/?$',
        r'/sitemap/?$',
        
        # Navigation/listing pages
        r'/category/',
        r'/categories/',
        r'/tag[s]?/',
        r'/author[s]?/',
        r'/archive[s]?/',
        r'/page/\d+',           # Pagination
        r'/search/',
        r'/results/',
        
        # Media galleries (unless article)
        r'/gallery/',
        r'/photos?/',
        r'/videos?/',
        r'/podcasts?/',
        
        # User/account pages
        r'/login/?$',
        r'/register/?$',
        r'/signup/?$',
        r'/account/',
        r'/profile/',
        r'/settings/',
        
        # Feed/API endpoints
        r'/feed/?$',
        r'/rss/?$',
        r'/api/',
        r'\.xml$',
        r'\.json$',
        
        # File downloads
        r'\.pdf$',
        r'\.zip$',
        r'\.mp[34]$',
        r'\.docx?$',
        
        # Index pages
        r'^/?$',                # Root/homepage
        r'/index\.',
        r'/home/?$',
    ]
    
    # Domain-specific article patterns
    DOMAIN_PATTERNS = {
        'hollywoodreporter.com': [
            r'/news/',
            r'/movies/',
            r'/tv/',
            r'/features/',
            r'/reviews/',
            r'/heat-vision/',
            r'/live-feed/',
        ],
        'variety.com': [
            r'/\d{4}/',
            r'/film/',
            r'/tv/',
            r'/music/',
            r'/digital/',
            r'/dirt/',
        ],
        'deadline.com': [
            r'/\d{4}/\d{2}/',
            r'/breaking-news/',
            r'/film/',
            r'/tv/',
        ],
        'indiewire.com': [
            r'/\d{4}/\d{2}/',
            r'/news/',
            r'/features/',
            r'/reviews/',
            r'/awards/',
        ],
        'thewrap.com': [
            r'/-\d+/$',  # Articles end with -ID/
        ],
    }
    
    def __init__(self, timeout: int = 5):
        """Initialize the article detector."""
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; ArticleDetector/1.0)'
        })
    
    def is_article_url(self, url: str, domain: Optional[str] = None) -> Tuple[bool, float]:
        """
        Determine if a URL is likely to be an article.
        
        Args:
            url: The URL to check
            domain: Optional domain for domain-specific patterns
            
        Returns:
            Tuple of (is_article, confidence_score)
            confidence_score is between 0.0 and 1.0
        """
        url_lower = url.lower()
        score = 0.5  # Start with neutral score
        
        # Check exclusion patterns first (negative indicators)
        for pattern in self.EXCLUSION_PATTERNS:
            if re.search(pattern, url_lower):
                logger.debug(f"URL matched exclusion pattern {pattern}: {url}")
                score -= 0.3
                
        # Check positive article patterns
        matches = 0
        for pattern in self.ARTICLE_PATTERNS:
            if re.search(pattern, url_lower):
                matches += 1
                score += 0.2
                logger.debug(f"URL matched article pattern {pattern}: {url}")
        
        # Apply domain-specific patterns if available
        if domain:
            domain_lower = domain.lower()
            for domain_pattern, patterns in self.DOMAIN_PATTERNS.items():
                if domain_pattern in domain_lower:
                    for pattern in patterns:
                        if re.search(pattern, url_lower):
                            score += 0.3
                            logger.debug(f"URL matched domain pattern {pattern}: {url}")
                            break
        
        # Additional heuristics
        parsed = urlparse(url)
        path = parsed.path
        
        # Long, descriptive slugs are often articles
        slug = path.rstrip('/').split('/')[-1] if path else ''
        if slug and len(slug) > 20 and '-' in slug:
            score += 0.1
            
        # Multiple path segments often indicate articles
        segments = [s for s in path.split('/') if s]
        if 2 <= len(segments) <= 5:
            score += 0.1
        
        # Clamp score between 0 and 1
        score = max(0.0, min(1.0, score))
        
        # Threshold for considering it an article
        is_article = score >= 0.6
        
        return is_article, score
    
    def check_page_metadata(self, url: str) -> Dict[str, any]:
        """
        Fetch and check page metadata to verify if it's an article.
        
        Args:
            url: The URL to check
            
        Returns:
            Dictionary with metadata and article indicators
        """
        result = {
            'is_article': False,
            'confidence': 0.0,
            'og_type': None,
            'article_date': None,
            'has_article_body': False,
            'error': None
        }
        
        try:
            # Use HEAD request first for efficiency
            response = self.session.head(url, timeout=self.timeout, allow_redirects=True)
            
            # Check content type
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/html' not in content_type:
                result['error'] = f"Non-HTML content type: {content_type}"
                return result
            
            # Now fetch the full page for metadata
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'lxml')
            
            # Check OpenGraph metadata
            og_type = soup.find('meta', property='og:type')
            if og_type:
                og_type_value = og_type.get('content', '').lower()
                result['og_type'] = og_type_value
                if 'article' in og_type_value or 'news' in og_type_value:
                    result['confidence'] += 0.4
            
            # Check for article publish date
            date_selectors = [
                ('meta', {'property': 'article:published_time'}),
                ('meta', {'name': 'publish_date'}),
                ('meta', {'name': 'datePublished'}),
                ('time', {'itemprop': 'datePublished'}),
            ]
            
            for tag, attrs in date_selectors:
                date_elem = soup.find(tag, attrs)
                if date_elem:
                    date_content = date_elem.get('content') or date_elem.get('datetime')
                    if date_content:
                        result['article_date'] = date_content
                        result['confidence'] += 0.2
                        break
            
            # Check for JSON-LD article schema
            scripts = soup.find_all('script', type='application/ld+json')
            for script in scripts:
                try:
                    import json
                    data = json.loads(script.string)
                    if isinstance(data, dict):
                        schema_type = data.get('@type', '').lower()
                        if 'article' in schema_type or 'newsarticle' in schema_type:
                            result['confidence'] += 0.3
                            if 'datePublished' in data:
                                result['article_date'] = data['datePublished']
                except:
                    pass
            
            # Check for article body indicators
            article_selectors = [
                'article',
                '[itemprop="articleBody"]',
                '.article-content',
                '.entry-content',
                '.post-content',
                '#article-body',
            ]
            
            for selector in article_selectors:
                if soup.select_one(selector):
                    result['has_article_body'] = True
                    result['confidence'] += 0.2
                    break
            
            # Check for author information
            author_selectors = [
                ('meta', {'name': 'author'}),
                ('meta', {'property': 'article:author'}),
                ('[itemprop="author"]', {}),
                ('.byline', {}),
            ]
            
            for selector, attrs in author_selectors:
                if attrs:
                    author = soup.find(selector, attrs)
                else:
                    author = soup.select_one(selector)
                if author:
                    result['confidence'] += 0.1
                    break
            
            # Final determination
            result['confidence'] = min(1.0, result['confidence'])
            result['is_article'] = result['confidence'] >= 0.5
            
        except requests.RequestException as e:
            result['error'] = str(e)
            logger.error(f"Error checking page metadata for {url}: {e}")
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Unexpected error checking {url}: {e}")
        
        return result
    
    def filter_sitemap_urls(self, urls: List[str], domain: Optional[str] = None,
                          check_metadata: bool = False) -> List[Dict[str, any]]:
        """
        Filter a list of URLs to identify likely articles.
        
        Args:
            urls: List of URLs to filter
            domain: Optional domain for domain-specific patterns
            check_metadata: Whether to fetch and check page metadata (slower but more accurate)
            
        Returns:
            List of dictionaries with URL and article detection info
        """
        results = []
        
        for url in urls:
            # First pass: URL pattern check
            is_article, confidence = self.is_article_url(url, domain)
            
            result = {
                'url': url,
                'is_article': is_article,
                'confidence': confidence,
                'metadata_checked': False
            }
            
            # Second pass: Check metadata for uncertain cases or if requested
            if check_metadata and (0.4 <= confidence <= 0.7 or check_metadata):
                metadata = self.check_page_metadata(url)
                result['metadata_checked'] = True
                result['metadata'] = metadata
                
                # Update decision based on metadata
                if not metadata['error']:
                    combined_confidence = (confidence + metadata['confidence']) / 2
                    result['confidence'] = combined_confidence
                    result['is_article'] = combined_confidence >= 0.5
            
            results.append(result)
            
            # Log decision
            if result['is_article']:
                logger.info(f"Identified as article (confidence {result['confidence']:.2f}): {url}")
            else:
                logger.debug(f"Not an article (confidence {result['confidence']:.2f}): {url}")
        
        return results
    
    def get_article_urls_from_sitemap(self, sitemap_urls: List[str], 
                                     domain: Optional[str] = None) -> List[str]:
        """
        Get only article URLs from a list of sitemap URLs.
        
        Args:
            sitemap_urls: List of all URLs from sitemap
            domain: Optional domain for domain-specific patterns
            
        Returns:
            List of URLs identified as articles
        """
        filtered = self.filter_sitemap_urls(sitemap_urls, domain)
        return [item['url'] for item in filtered if item['is_article']]