"""
Article Analysis Module

This module provides functionality to analyze articles using Claude AI,
extracting summaries, topics, and identifying similar content.
"""

import anthropic
from django.conf import settings
from typing import List, Dict, Optional, Tuple
import hashlib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import re
from bs4 import BeautifulSoup
import json
import logging
import requests
import time
import random
from functools import wraps

logger = logging.getLogger(__name__)


def retry_on_overload(max_retries=10, initial_delay=3, backoff_factor=1.5, max_delay=60):
    """
    Decorator to retry function calls when encountering overload errors.
    
    Args:
        max_retries: Maximum number of retry attempts (default 10)
        initial_delay: Initial delay in seconds before first retry (default 3)
        backoff_factor: Factor to multiply delay by after each retry (default 1.5)
        max_delay: Maximum delay between retries in seconds (default 60)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    if attempt > 0:
                        logger.info(f"Attempt {attempt + 1}/{max_retries + 1} for {func.__name__}")
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    error_str = str(e).lower()
                    
                    # Check if it's an overload error (be more inclusive)
                    if any(err in error_str for err in ["overloaded", "529", "rate", "timeout", "503", "429"]):
                        if attempt < max_retries:
                            # Add some jitter to avoid thundering herd
                            jittered_delay = delay + random.uniform(0, delay * 0.3)
                            logger.warning(f"Overload/rate limit error on attempt {attempt + 1}/{max_retries + 1}. Retrying in {jittered_delay:.1f} seconds...")
                            time.sleep(jittered_delay)
                            # Increase delay but cap at max_delay
                            delay = min(delay * backoff_factor, max_delay)
                            continue
                    
                    # If it's not an overload error, raise immediately
                    raise e
            
            # If we've exhausted all retries, raise the last exception
            logger.error(f"Failed after {max_retries + 1} attempts")
            raise last_exception
        
        return wrapper
    return decorator


class ArticleAnalyzer:
    """Analyzes articles for summaries, topics, and similarity."""
    
    def __init__(self):
        """Initialize the analyzer with Claude client."""
        api_key = settings.ANTHROPIC_API_KEY
        if api_key:
            self.client = anthropic.Anthropic(api_key=api_key)
        else:
            self.client = None
            logger.warning("ANTHROPIC_API_KEY not configured - Claude features will be unavailable")
        self.vectorizer = TfidfVectorizer(
            max_features=1000,
            stop_words='english',
            ngram_range=(1, 2)
        )
    
    def clean_html(self, html_content: str) -> str:
        """Remove HTML tags and clean text."""
        if not html_content:
            return ""
        
        # Parse HTML
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
        
        # Get text
        text = soup.get_text()
        
        # Break into lines and remove leading/trailing space
        lines = (line.strip() for line in text.splitlines())
        
        # Break multi-headlines into a line each
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        
        # Drop blank lines
        text = ' '.join(chunk for chunk in chunks if chunk)
        
        return text
    
    @retry_on_overload(max_retries=15, initial_delay=5, backoff_factor=1.3, max_delay=30)
    def _call_claude_api_haiku(self, prompt: str) -> str:
        """Helper method to call Claude Haiku API with retry logic."""
        if not self.client:
            raise Exception("Claude API not configured - please set ANTHROPIC_API_KEY")
        response = self.client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=1000,
            temperature=0,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        return response.content[0].text
    
    def extract_summary_and_topics(self, article) -> Dict:
        """
        Extract summary and topics from an article using Claude.
        
        Args:
            article: Article model instance
            
        Returns:
            Dict containing summary, topics, and key entities
        """
        # Clean the content
        clean_content = self.clean_html(article.content)
        
        if not clean_content:
            clean_content = article.summary or article.title
        
        # Prepare the prompt for Claude
        prompt = f"""Analyze the following article and provide:
1. A concise summary (2-3 sentences)
2. Main topics/categories (max 5)
3. Key named entities mentioned:
   - People: Full names of actual people mentioned (e.g., "John Smith", "Emma Watson")
   - Organizations: Company/organization names (e.g., "Netflix", "Marvel Studios", "FBI")
   - Locations: Place names (e.g., "New York", "Hollywood", "United States")
4. Sentiment (positive/negative/neutral)
5. Keywords for SEO (max 10)

Article Title: {article.title}
Article Content: {clean_content[:4000]}  # Limit content to avoid token limits

Please respond in JSON format. Extract actual entity names from the article:
{{
    "summary": "A 2-3 sentence summary",
    "topics": ["topic1", "topic2", ...],
    "entities": {{
        "people": ["Person Name 1", "Person Name 2", ...],
        "organizations": ["Company 1", "Organization 2", ...],
        "locations": ["Place 1", "City 2", ...]
    }},
    "sentiment": "positive/negative/neutral",
    "keywords": ["keyword1", "keyword2", ...]
}}"""
        
        try:
            # Use a helper with retry logic - using Haiku model for efficiency
            result_text = self._call_claude_api_haiku(prompt)
            
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if json_match:
                try:
                    # Clean the JSON string to remove control characters
                    json_str = json_match.group()
                    # Remove control characters except for newlines and tabs
                    json_str = ''.join(char for char in json_str if ord(char) >= 32 or char in '\n\t')
                    # Replace actual newlines in content with escaped newlines
                    json_str = json_str.replace('\n', '\\n').replace('\t', '\\t')
                    # Parse the cleaned JSON
                    result = json.loads(json_str)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse JSON in extract_summary: {e}")
                    # Try to extract values from malformed JSON using regex
                    summary_match = re.search(r'"summary"\s*:\s*"([^"]*)', result_text)
                    topics_match = re.findall(r'"topics"\s*:\s*\[([^\]]*)\]', result_text)
                    
                    extracted_summary = ""
                    extracted_topics = []
                    extracted_keywords = []
                    extracted_sentiment = "neutral"
                    extracted_entities = {"people": [], "organizations": [], "locations": []}
                    
                    if summary_match:
                        extracted_summary = summary_match.group(1)
                        # Unescape JSON string
                        extracted_summary = extracted_summary.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                    
                    if topics_match and topics_match[0]:
                        # Extract topics from the JSON array string
                        topics_str = topics_match[0]
                        topic_matches = re.findall(r'"([^"]+)"', topics_str)
                        extracted_topics = topic_matches[:5]  # Limit to 5 topics
                    
                    # Try to extract keywords
                    keywords_match = re.search(r'"keywords"\s*:\s*\[([^\]]*)\]', result_text)
                    if keywords_match and keywords_match.group(1):
                        keyword_matches = re.findall(r'"([^"]+)"', keywords_match.group(1))
                        extracted_keywords = keyword_matches[:10]  # Limit to 10 keywords
                    
                    # Try to extract sentiment
                    sentiment_match = re.search(r'"sentiment"\s*:\s*"([^"]*)"', result_text)
                    if sentiment_match:
                        extracted_sentiment = sentiment_match.group(1).lower()
                        if extracted_sentiment not in ['positive', 'negative', 'neutral']:
                            extracted_sentiment = 'neutral'
                    
                    # Try to extract entities
                    people_match = re.search(r'"people"\s*:\s*\[([^\]]*)\]', result_text)
                    if people_match and people_match.group(1):
                        people_matches = re.findall(r'"([^"]+)"', people_match.group(1))
                        extracted_entities["people"] = people_matches[:10]
                    
                    orgs_match = re.search(r'"organizations"\s*:\s*\[([^\]]*)\]', result_text)
                    if orgs_match and orgs_match.group(1):
                        org_matches = re.findall(r'"([^"]+)"', orgs_match.group(1))
                        extracted_entities["organizations"] = org_matches[:10]
                    
                    locations_match = re.search(r'"locations"\s*:\s*\[([^\]]*)\]', result_text)
                    if locations_match and locations_match.group(1):
                        loc_matches = re.findall(r'"([^"]+)"', locations_match.group(1))
                        extracted_entities["locations"] = loc_matches[:10]
                    
                    # If we couldn't extract a summary, use the article's existing summary or title
                    if not extracted_summary:
                        extracted_summary = article.summary or article.title
                    
                    result = {
                        "summary": extracted_summary,
                        "topics": extracted_topics,
                        "entities": extracted_entities,
                        "sentiment": extracted_sentiment,
                        "keywords": extracted_keywords
                    }
            else:
                # No JSON found at all - use article's existing data
                result = {
                    "summary": article.summary or article.title,
                    "topics": [],
                    "entities": {"people": [], "organizations": [], "locations": []},
                    "sentiment": "neutral",
                    "keywords": []
                }
            
            return result
            
        except Exception as e:
            print(f"Error analyzing article: {e}")
            return {
                "summary": article.summary or article.title,
                "topics": [],
                "entities": {"people": [], "organizations": [], "locations": []},
                "sentiment": "neutral",
                "keywords": [],
                "error": str(e)
            }
    
    def find_similar_articles(self, article, threshold: float = 0.7) -> List[Tuple['Article', float]]:
        """
        Find articles with similar content using TF-IDF and cosine similarity.
        
        Args:
            article: Article model instance to compare
            threshold: Similarity threshold (0-1)
            
        Returns:
            List of tuples (similar_article, similarity_score)
        """
        from .models import Article
        
        # Clean the content
        clean_content = self.clean_html(article.content)
        if not clean_content:
            clean_content = article.summary or article.title
        
        # Get other articles from the same time period (last 7 days)
        from datetime import timedelta
        from django.utils import timezone
        
        recent_date = timezone.now() - timedelta(days=7)
        other_articles = Article.objects.exclude(
            id=article.id
        ).filter(
            published_date__gte=recent_date
        ).order_by('-published_date')[:100]  # Limit to 100 most recent
        
        if not other_articles:
            return []
        
        # Prepare texts for comparison
        texts = [clean_content]
        article_map = {}
        
        for idx, other in enumerate(other_articles):
            other_content = self.clean_html(other.content)
            if not other_content:
                other_content = other.summary or other.title
            texts.append(other_content)
            article_map[idx + 1] = other
        
        # Vectorize texts
        try:
            tfidf_matrix = self.vectorizer.fit_transform(texts)
            
            # Calculate cosine similarity
            similarities = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])[0]
            
            # Find similar articles above threshold
            similar_articles = []
            for idx, similarity in enumerate(similarities):
                if similarity >= threshold:
                    similar_articles.append((article_map[idx + 1], float(similarity)))
            
            # Sort by similarity
            similar_articles.sort(key=lambda x: x[1], reverse=True)
            
            return similar_articles
            
        except Exception as e:
            print(f"Error finding similar articles: {e}")
            return []
    
    def check_duplicate_content(self, article) -> Optional['Article']:
        """
        Check if an article is a duplicate of existing content.
        
        Args:
            article: Article model instance
            
        Returns:
            The original article if duplicate found, None otherwise
        """
        # First check by URL
        from .models import Article
        
        existing = Article.objects.filter(url=article.url).exclude(id=article.id).first()
        if existing:
            return existing
        
        # Check by content hash
        if article.content_hash:
            existing = Article.objects.filter(
                content_hash=article.content_hash
            ).exclude(id=article.id).first()
            if existing:
                return existing
        
        # Check by high similarity (>0.95)
        similar = self.find_similar_articles(article, threshold=0.95)
        if similar:
            return similar[0][0]
        
        return None


class ContentGenerator:
    """Generates new content based on multiple source articles."""
    
    def __init__(self):
        """Initialize the content generator with Claude client."""
        api_key = settings.ANTHROPIC_API_KEY
        if api_key:
            self.client = anthropic.Anthropic(api_key=api_key)
        else:
            self.client = None
            logger.warning("ANTHROPIC_API_KEY not configured - Claude features will be unavailable")
        self.keygrip_api_key = getattr(settings, 'KEYGRIP_API_KEY', 'zrag_7zf1YKHrPqGdaAC8f5S52I9QxKwFzK9IbX5cJ-u7lSk')
        self.keygrip_api_url = getattr(settings, 'KEYGRIP_API_URL', 'https://stage.keygrip.ai/api/v1/query/')
    
    @retry_on_overload(max_retries=8, initial_delay=3, backoff_factor=1.5, max_delay=30)
    def _call_keygrip_api(self, payload: Dict, headers: Dict) -> Dict:
        """Helper method to call Keygrip API with retry logic."""
        response = requests.post(
            self.keygrip_api_url,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 429:  # Rate limit
            raise Exception(f"Keygrip rate limit: {response.status_code}")
        else:
            logger.error(f"Keygrip API error: {response.status_code} - {response.text}")
            raise Exception(f"Keygrip API returned status {response.status_code}")
    
    def generate_with_keygrip(
        self,
        source_articles: List['Article'],
        voice_prompt_id: str = "product_writer",
        use_writing_samples: bool = False,
        use_web_search: bool = True
    ) -> Dict:
        """
        Generate a new article using Keygrip AI based on source articles.
        
        Args:
            source_articles: List of Article instances to use as sources
            voice_prompt_id: The voice/style to use for generation
            use_writing_samples: Whether to use writing samples
            use_web_search: Whether to use web search for additional context
            
        Returns:
            Dict containing generated title, content, and metadata
        """
        if not source_articles:
            raise ValueError("At least one source article is required")
        
        # Prepare the query from source articles
        query_parts = []
        media_items = []
        
        for idx, article in enumerate(source_articles[:5]):  # Limit to 5 sources
            clean_content = ArticleAnalyzer().clean_html(article.content)
            if not clean_content:
                clean_content = article.summary or article.title
            
            query_parts.append(f"""
Article {idx + 1}:
Title: {article.title}
Published: {article.published_date}
Summary: {clean_content[:1000]}
URL: {article.url}
""")
            
            # Extract media from raw_data if available
            if article.raw_data and isinstance(article.raw_data, dict):
                if 'enclosures' in article.raw_data:
                    for enclosure in article.raw_data['enclosures']:
                        if enclosure.get('type', '').startswith('image'):
                            media_items.append({
                                'type': 'image',
                                'url': enclosure.get('href', ''),
                                'caption': f"From: {article.title}"
                            })
        
        # Create the query
        query = f"""Based on the following source articles, create a comprehensive article that synthesizes the information and provides insights:

{' '.join(query_parts)}

Please create an engaging article that:
1. Synthesizes information from all sources
2. Provides fresh perspective and insights
3. Maintains factual accuracy
4. Includes proper attribution"""
        
        try:
            # Make the API call to Keygrip
            headers = {
                'X-API-Key': self.keygrip_api_key,
                'Content-Type': 'application/json'
            }
            
            payload = {
                'query': query,
                'voice_prompt_id': voice_prompt_id,
                'use_writing_samples': use_writing_samples,
                'use_web_search': use_web_search
            }
            
            keygrip_result = self._call_keygrip_api(payload, headers)
            
            # Extract the generated content from Keygrip response
            generated_content = keygrip_result.get('response', '')
            
            # Check if the response is a JSON string with structured content
            if isinstance(generated_content, str) and generated_content.strip().startswith('{'):
                try:
                    import json
                    parsed_content = json.loads(generated_content)
                    # Extract fields from the parsed JSON
                    title = parsed_content.get('title', 'Generated Article')
                    content = parsed_content.get('content', '')
                    subtitle = parsed_content.get('subtitle', '')
                    summary = parsed_content.get('summary', '')
                    
                    # If no summary provided, create one from content
                    if not summary and content:
                        # Strip HTML tags for summary
                        import re
                        clean_text = re.sub('<[^<]+?>', '', content)
                        summary = clean_text[:200] + '...' if len(clean_text) > 200 else clean_text
                except json.JSONDecodeError:
                    # Not valid JSON, treat as plain text
                    lines = generated_content.strip().split('\n')
                    title = lines[0] if lines else "Generated Article"
                    
                    # Remove title from content if it appears to be a heading
                    if lines and (lines[0].startswith('#') or len(lines[0]) < 100):
                        content = '\n'.join(lines[1:]).strip()
                    else:
                        content = generated_content
                    subtitle = ''
                    summary = content[:200] + '...' if len(content) > 200 else content
            else:
                # Plain text response
                lines = generated_content.strip().split('\n')
                title = lines[0] if lines else "Generated Article"
                
                # Remove title from content if it appears to be a heading
                if lines and (lines[0].startswith('#') or len(lines[0]) < 100):
                    content = '\n'.join(lines[1:]).strip()
                else:
                    content = generated_content
                subtitle = ''
                summary = content[:200] + '...' if len(content) > 200 else content
            
            result = {
                'title': title.replace('#', '').strip(),
                'subtitle': subtitle,
                'content': content,
                'summary': summary,
                'topics': [],
                'sources_used': [a.title for a in source_articles],
                'media_items': media_items,
                'source_urls': [
                    {"title": a.title, "url": a.url, "type": "article"} 
                    for a in source_articles
                ],
                'generation_method': 'keygrip',
                'voice_prompt_id': voice_prompt_id,
                'web_sources': keygrip_result.get('sources', []) if use_web_search else []
            }
            
            # Extract topics if available in response
            if 'topics' in keygrip_result:
                result['topics'] = keygrip_result['topics']
            
            return result
                
        except Exception as e:
            logger.error(f"Error with Keygrip after retries: {e}")
            # Fallback to Claude generation
            logger.info("Falling back to Claude generation...")
            try:
                return self.generate_article(source_articles, style="news", target_length=800)
            except Exception as claude_error:
                logger.error(f"Claude fallback also failed: {claude_error}")
                # Both services failed after retries
                return {
                    "title": "Error Generating Article",
                    "content": f"Unable to generate content. Both Keygrip and Claude services failed after multiple attempts.",
                    "summary": "",
                    "topics": [],
                    "sources_used": [a.title for a in source_articles],
                    "media_items": media_items,
                    "error": f"Keygrip error: {str(e)}, Claude error: {str(claude_error)}"
                }
    
    def search_web_for_context(self, topics: List[str], keywords: List[str]) -> List[Dict]:
        """
        Search the web for additional context and sources related to the content.
        
        Args:
            topics: Main topics to search for
            keywords: Additional keywords to include
            
        Returns:
            List of web search results with title, url, and snippet
        """
        import requests
        from urllib.parse import quote
        
        web_sources = []
        
        # Combine topics and keywords into search queries
        search_queries = []
        if topics:
            # Use the first 2-3 topics for search
            search_queries.append(' '.join(topics[:3]))
        if keywords:
            # Add a query with top keywords
            search_queries.append(' '.join(keywords[:5]))
        
        # If no topics/keywords from analysis, create generic queries from article titles
        if not search_queries:
            logger.info("No topics/keywords found, skipping web search")
            return []
        
        # Limit to 2 searches to avoid too many API calls
        for query in search_queries[:2]:
            if not query:
                continue
                
            try:
                # Use DuckDuckGo HTML search for better results
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                
                # Try to get search results using DuckDuckGo HTML
                search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
                
                response = requests.get(search_url, headers=headers, timeout=5)
                if response.status_code == 200:
                    # Parse HTML response
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Find search results
                    results = soup.find_all('div', class_='result', limit=3)
                    
                    for result in results:
                        title_elem = result.find('a', class_='result__a')
                        snippet_elem = result.find('a', class_='result__snippet')
                        
                        if title_elem and title_elem.get('href'):
                            # Extract clean URL
                            url = title_elem.get('href')
                            if url.startswith('//duckduckgo.com/l/?'):
                                # Extract actual URL from DDG redirect
                                import re
                                url_match = re.search(r'uddg=([^&]+)', url)
                                if url_match:
                                    from urllib.parse import unquote
                                    url = unquote(url_match.group(1))
                            
                            web_sources.append({
                                'title': title_elem.get_text(strip=True)[:200],
                                'url': url,
                                'snippet': snippet_elem.get_text(strip=True)[:300] if snippet_elem else '',
                                'source_type': 'web_search'
                            })
                    
                    logger.info(f"Found {len(results)} web results for query: {query[:50]}")
                        
            except Exception as e:
                logger.warning(f"Error searching web for context: {e}")
        
        # Add some static authoritative sources based on topics if available
        if 'technology' in str(topics).lower() or 'tech' in str(keywords).lower():
            web_sources.append({
                'title': 'Technology News and Trends',
                'url': 'https://www.techcrunch.com',
                'snippet': 'Latest technology news and startup trends',
                'source_type': 'reference'
            })
        
        if 'entertainment' in str(topics).lower() or 'movie' in str(keywords).lower() or 'tv' in str(keywords).lower():
            web_sources.append({
                'title': 'Entertainment Industry News',
                'url': 'https://variety.com',
                'snippet': 'Entertainment news, film reviews, and industry analysis',
                'source_type': 'reference'
            })
                
        return web_sources
    
    @retry_on_overload(max_retries=15, initial_delay=5, backoff_factor=1.3, max_delay=30)
    def _call_claude_api(self, prompt: str, model: str = "claude-3-5-sonnet-20241022", max_tokens: int = 2000, temperature: float = 0.7) -> str:
        """Helper method to call Claude API with retry logic."""
        if not self.client:
            raise Exception("Claude API not configured - please set ANTHROPIC_API_KEY")
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        return response.content[0].text
    
    def generate_article(
        self,
        source_articles: List['Article'],
        style: str = "news",
        target_length: int = 800
    ) -> Dict:
        """
        Generate a new article based on multiple source articles and web research.
        
        Args:
            source_articles: List of Article instances to use as sources
            style: Writing style (news, blog, analysis, summary)
            target_length: Target word count
            
        Returns:
            Dict containing generated title, content, and metadata
        """
        if not source_articles:
            raise ValueError("At least one source article is required")
        
        # First, extract topics and keywords from source articles for web search
        all_topics = []
        all_keywords = []
        
        for article in source_articles[:5]:
            if hasattr(article, 'analysis') and article.analysis:
                if article.analysis.topics:
                    all_topics.extend(article.analysis.topics)
                if article.analysis.keywords:
                    all_keywords.extend(article.analysis.keywords)
        
        # Remove duplicates while preserving order
        all_topics = list(dict.fromkeys(all_topics))
        all_keywords = list(dict.fromkeys(all_keywords))
        
        # Search web for additional context
        web_sources = self.search_web_for_context(all_topics, all_keywords)
        
        # Prepare source content
        sources_text = ""
        media_items = []
        
        for idx, article in enumerate(source_articles[:5]):  # Limit to 5 sources
            clean_content = ArticleAnalyzer().clean_html(article.content)
            if not clean_content:
                clean_content = article.summary or article.title
            
            sources_text += f"""
Source {idx + 1} (Article):
Title: {article.title}
Published: {article.published_date}
Content: {clean_content[:2000]}
URL: {article.url}

"""
            
            # Extract media from raw_data if available
            if article.raw_data and isinstance(article.raw_data, dict):
                if 'enclosures' in article.raw_data:
                    for enclosure in article.raw_data['enclosures']:
                        if enclosure.get('type', '').startswith('image'):
                            media_items.append({
                                'type': 'image',
                                'url': enclosure.get('href', ''),
                                'caption': f"From: {article.title}"
                            })
        
        # Add web sources to the context
        if web_sources:
            sources_text += "\nAdditional Web Context:\n"
            for idx, web_source in enumerate(web_sources[:5]):  # Limit web sources
                sources_text += f"""
Web Source {idx + 1}:
Title: {web_source.get('title', 'Web Source')}
URL: {web_source.get('url', '')}
Context: {web_source.get('snippet', '')}

"""
        
        # Prepare the prompt
        style_instructions = {
            "news": "Write in a professional news style with clear facts and attribution",
            "blog": "Write in an engaging blog style with personality and insights",
            "analysis": "Write in an analytical style with deep insights and connections",
            "summary": "Write a comprehensive summary highlighting key points from all sources"
        }
        
        prompt = f"""Based on the following source articles, create a new {style} article that:
1. Synthesizes information from multiple sources
2. Provides a fresh perspective or angle
3. Is approximately {target_length} words
4. Includes proper attribution to sources
5. Maintains factual accuracy

Writing Style: {style_instructions.get(style, style_instructions['news'])}

Source Articles:
{sources_text}

Generate the article in JSON format:
{{
    "title": "Compelling title for the new article",
    "subtitle": "Optional subtitle or lead",
    "content": "The full article content with HTML formatting",
    "summary": "2-3 sentence summary",
    "topics": ["topic1", "topic2"],
    "sources_used": ["Source 1 title", "Source 2 title", ...],
    "suggested_media": ["Description of relevant images or media to include"]
}}"""
        
        try:
            # Use the helper method with retry logic
            result_text = self._call_claude_api(prompt)
            
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if json_match:
                try:
                    # Clean the JSON string to remove control characters
                    json_str = json_match.group()
                    # Remove control characters except for newlines and tabs
                    json_str = ''.join(char for char in json_str if ord(char) >= 32 or char in '\n\t')
                    # Replace actual newlines in content with escaped newlines
                    json_str = json_str.replace('\n', '\\n').replace('\t', '\\t')
                    # Parse the cleaned JSON
                    result = json.loads(json_str)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse JSON response: {e}")
                    # Try a more aggressive cleaning approach
                    try:
                        # Remove all newlines and extra spaces within strings
                        cleaned = re.sub(r':\s*"([^"]*)"', lambda m: f': "{m.group(1).replace(chr(10), " ").replace(chr(13), " ").replace(chr(9), " ")}"', json_match.group())
                        result = json.loads(cleaned)
                    except:
                        # Final fallback
                        result = {
                            "title": "Generated Article",
                            "content": result_text,
                            "summary": "",
                            "topics": [],
                            "sources_used": [a.title for a in source_articles]
                        }
            else:
                # Fallback if JSON parsing fails
                result = {
                    "title": "Generated Article",
                    "content": result_text,
                    "summary": "",
                    "topics": [],
                    "sources_used": [a.title for a in source_articles]
                }
            
            # Add media items
            result['media_items'] = media_items
            
            # Add source URLs for attribution (both articles and web sources)
            result['source_urls'] = [
                {"title": a.title, "url": a.url, "type": "article"} for a in source_articles
            ]
            
            # Add web sources to the attribution
            if web_sources:
                result['web_sources'] = web_sources
                for web_source in web_sources:
                    result['source_urls'].append({
                        "title": web_source.get('title', 'Web Source'),
                        "url": web_source.get('url', ''),
                        "type": "web"
                    })
            
            return result
            
        except Exception as e:
            logger.error(f"Error generating article after retries: {e}")
            # The retry decorator already handled overload retries
            # If we're here, it means all retries failed
            return {
                "title": "Error Generating Article",
                "content": f"Unable to generate content after multiple attempts. Error: {str(e)}",
                "summary": "",
                "topics": [],
                "sources_used": [a.title for a in source_articles],
                "media_items": media_items,
                "source_urls": [
                    {"title": a.title, "url": a.url, "type": "article"} for a in source_articles
                ],
                "error": str(e)
            }