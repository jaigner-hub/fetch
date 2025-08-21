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
            
            # Extract images
            images = []
            featured_image = ''
            
            # Check for media content in feed
            if hasattr(entry, 'media_content'):
                for media in entry.media_content:
                    if media.get('type', '').startswith('image'):
                        images.append({
                            'url': media.get('url', ''),
                            'type': media.get('type', ''),
                            'width': media.get('width', ''),
                            'height': media.get('height', '')
                        })
            
            # Check for media thumbnail
            if hasattr(entry, 'media_thumbnail'):
                for thumb in entry.media_thumbnail:
                    featured_image = thumb.get('url', '')
                    break
            
            # Check for enclosures (common for images/media)
            if hasattr(entry, 'enclosures'):
                for enclosure in entry.enclosures:
                    if enclosure.get('type', '').startswith('image'):
                        img_url = enclosure.get('href', '')
                        if img_url and not featured_image:
                            featured_image = img_url
                        images.append({
                            'url': img_url,
                            'type': enclosure.get('type', ''),
                            'length': enclosure.get('length', '')
                        })
            
            # Extract images from content if we have it
            if content:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(content, 'html.parser')
                for img in soup.find_all('img'):
                    img_url = img.get('src', '')
                    if img_url:
                        # Make URL absolute if it's relative
                        if not img_url.startswith(('http://', 'https://')):
                            img_url = urljoin(article_url, img_url)
                        
                        images.append({
                            'url': img_url,
                            'alt': img.get('alt', ''),
                            'title': img.get('title', ''),
                            'width': img.get('width', ''),
                            'height': img.get('height', '')
                        })
                        
                        # Use first image as featured if not set
                        if not featured_image:
                            featured_image = img_url
            
            # Remove duplicate images
            seen_urls = set()
            unique_images = []
            for img in images:
                if img['url'] and img['url'] not in seen_urls:
                    seen_urls.add(img['url'])
                    unique_images.append(img)
            
            return {
                'url': article_url,
                'title': title,
                'content': content,
                'summary': summary,
                'author': author,
                'published_date': published_date,
                'content_hash': content_hash,
                'tags': tags,
                'images': unique_images,
                'featured_image': featured_image,
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
        Extract article content from parsed HTML, preserving links and media.
        
        Args:
            soup: BeautifulSoup parsed HTML
            
        Returns:
            Extracted content HTML or None
        """
        # Remove unwanted elements first
        for element in soup(['nav', 'header', 'footer', 'aside', 'form', 'button']):
            element.decompose()
        
        # Remove elements with specific classes/ids that typically contain non-content
        for selector in ['.sidebar', '.navigation', '.menu', '.advertisement', 
                        '.ads', '#header', '#footer', '#comments', '.social-share',
                        '.related-posts', '.recommended', '.newsletter', '.popup',
                        '.cookie-notice', '.gdpr', '.signup', '.promo', '.widget',
                        '.stream-info', '.show-info', '.availability']:
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
                for tag in content_elem(['script', 'style', 'noscript', 'iframe']):
                    # Keep iframe if it's a video embed
                    if tag.name == 'iframe' and any(domain in str(tag.get('src', '')) 
                                                   for domain in ['youtube', 'vimeo', 'dailymotion']):
                        continue
                    tag.decompose()
                
                # Extract and return HTML content
                # Just get the inner HTML as string - simpler and faster
                content_html = str(content_elem)
                
                # Require substantial content (at least 1500 chars of HTML)
                if content_html and len(content_html) > 1500:
                    # Also check text content length to ensure it's not just HTML tags
                    text_content = content_elem.get_text(strip=True)
                    word_count = len(text_content.split())
                    
                    # Check for link-heavy content (navigation pages)
                    link_count = len(content_elem.find_all('a'))
                    if word_count > 0:
                        links_per_100_words = (link_count / word_count) * 100
                        if links_per_100_words > 15:  # Too many links
                            logger.debug(f"Content is link-heavy: {links_per_100_words:.1f} links per 100 words")
                            continue
                    
                    # Require at least 200 words of actual text content
                    if word_count >= 200:
                        logger.debug(f"Found article content with {word_count} words")
                        return content_html
                    else:
                        logger.debug(f"Content too short: only {word_count} words")
        
        # Fallback: try to find the largest concentration of paragraphs
        all_paragraphs = soup.find_all('p')
        if all_paragraphs and len(all_paragraphs) > 5:  # Require at least 5 paragraphs
            # Build HTML from paragraphs
            html_parts = []
            total_text_length = 0
            for p in all_paragraphs:
                p_text = p.get_text(strip=True)
                if len(p_text) > 50:  # Ignore short paragraphs
                    html_parts.append(str(p))
                    total_text_length += len(p_text)
            
            # Require at least 200 words of actual text content
            word_count = len(' '.join([p.get_text(strip=True) for p in all_paragraphs]).split())
            
            # Check average paragraph length (navigation pages have short paragraphs)
            if all_paragraphs:
                avg_words_per_p = word_count / len(all_paragraphs)
                if avg_words_per_p < 15:  # Paragraphs too short
                    logger.debug(f"Paragraphs too short: avg {avg_words_per_p:.1f} words")
                    return None
            
            if html_parts and word_count >= 200:
                combined_html = '\n'.join(html_parts)
                if len(combined_html) > 1500:  # Increased minimum HTML length
                    logger.debug(f"Built article from {len(html_parts)} paragraphs with {word_count} words")
                    return f'<div>{combined_html}</div>'
            else:
                logger.debug(f"Not enough paragraph content: {word_count} words")
        
        # Last resort: get text content if no HTML can be extracted
        text = soup.get_text(separator='\n', strip=True)
        word_count = len(text.split())
        
        # Require at least 200 words for fallback text extraction
        if text and word_count >= 200:
            # Convert plain text to basic HTML
            paragraphs = [f'<p>{p}</p>' for p in text.split('\n\n') if len(p) > 50]
            # Only return if we have substantial content
            if paragraphs and len(paragraphs) >= 3:
                combined = '\n'.join(paragraphs)
                if len(combined) > 1500:
                    logger.debug(f"Fallback text extraction: {word_count} words")
                    return combined
        
        logger.warning(f"Content too short or not found: {word_count} words")
        return None
    
    def _clean_and_preserve_html(self, element) -> str:
        """
        Clean HTML while preserving links, images, and video embeds.
        
        Args:
            element: BeautifulSoup element
            
        Returns:
            Cleaned HTML string
        """
        import re
        
        # Allowed tags that we want to preserve
        allowed_tags = [
            'p', 'a', 'img', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'blockquote', 'ul', 'ol', 'li', 'strong', 'b', 'em', 'i',
            'br', 'hr', 'span', 'div', 'figure', 'figcaption',
            'video', 'source', 'iframe', 'picture', 'code', 'pre'
        ]
        
        # Allowed attributes for specific tags
        allowed_attrs = {
            'a': ['href', 'title', 'target', 'rel'],
            'img': ['src', 'alt', 'title', 'width', 'height', 'loading'],
            'video': ['src', 'poster', 'controls', 'width', 'height'],
            'source': ['src', 'type'],
            'iframe': ['src', 'width', 'height', 'frameborder', 'allowfullscreen'],
            'figure': ['class'],
            'div': ['class'],
            'span': ['class']
        }
        
        # First pass: remove completely unwanted tags
        for tag in element.find_all(['script', 'style', 'meta', 'link']):
            tag.decompose()
        
        # Second pass: process remaining tags
        for tag in list(element.find_all(True)):
            # Skip if tag was already removed
            if not tag.parent:
                continue
                
            # Remove tag if not allowed (keep its contents)
            if tag.name not in allowed_tags:
                tag.unwrap()
                continue
            
            # Clean attributes
            attrs_to_keep = allowed_attrs.get(tag.name, [])
            for attr in list(tag.attrs.keys()):
                if attr not in attrs_to_keep:
                    del tag.attrs[attr]
            
            # Make links open in new tab for safety
            if tag.name == 'a' and tag.get('href'):
                tag['target'] = '_blank'
                tag['rel'] = 'noopener noreferrer'
            
            # Ensure images have alt text
            if tag.name == 'img' and not tag.get('alt'):
                tag['alt'] = 'Article image'
            
            # Clean up empty tags (except self-closing ones)
            if tag.name not in ['img', 'br', 'hr', 'source'] and not tag.get_text(strip=True) and not tag.find_all(['img', 'video', 'iframe']):
                tag.decompose()
        
        # Convert to string and clean up whitespace
        html_str = str(element)
        
        # Remove excessive whitespace
        html_str = re.sub(r'\n\s*\n+', '\n\n', html_str)
        html_str = re.sub(r'  +', ' ', html_str)
        
        return html_str.strip()
    
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
    
    
    def extract_metadata(self, article_url: str) -> Dict:
        """
        Extract metadata from an article web page.
        
        Args:
            article_url: URL of the article
            
        Returns:
            Dictionary with metadata (title, description, author, published_date, etc.)
        """
        metadata = {
            'title': '',
            'description': '',
            'author': '',
            'published_date': None,
            'keywords': [],
            'image': ''
        }
        
        try:
            # Apply rate limiting
            self._apply_rate_limit(article_url)
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
            }
            
            response = self.session.get(article_url, timeout=self.timeout, headers=headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract title
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                metadata['title'] = og_title['content']
            elif soup.title:
                metadata['title'] = soup.title.string
            
            # Extract description
            og_desc = soup.find('meta', property='og:description')
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if og_desc and og_desc.get('content'):
                metadata['description'] = og_desc['content']
            elif meta_desc and meta_desc.get('content'):
                metadata['description'] = meta_desc['content']
            
            # Extract author
            author_meta = soup.find('meta', attrs={'name': 'author'})
            article_author = soup.find('meta', property='article:author')
            if author_meta and author_meta.get('content'):
                metadata['author'] = author_meta['content']
            elif article_author and article_author.get('content'):
                metadata['author'] = article_author['content']
            
            # Extract published date
            published_time = soup.find('meta', property='article:published_time')
            date_published = soup.find('meta', attrs={'name': 'publish_date'})
            if published_time and published_time.get('content'):
                try:
                    from dateutil import parser
                    metadata['published_date'] = parser.parse(published_time['content'])
                except:
                    pass
            elif date_published and date_published.get('content'):
                try:
                    from dateutil import parser
                    metadata['published_date'] = parser.parse(date_published['content'])
                except:
                    pass
            
            # Extract image
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                metadata['image'] = og_image['content']
            
            # Extract keywords
            keywords_meta = soup.find('meta', attrs={'name': 'keywords'})
            if keywords_meta and keywords_meta.get('content'):
                metadata['keywords'] = [k.strip() for k in keywords_meta['content'].split(',')]
            
            logger.info(f"Extracted metadata for {article_url}: {metadata['title']}")
            
        except Exception as e:
            logger.error(f"Error extracting metadata from {article_url}: {e}")
        
        return metadata