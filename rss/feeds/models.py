from django.db import models
from django.utils import timezone
import hashlib


class Website(models.Model):
    """Model to store websites to crawl for RSS feeds and sitemaps."""
    FETCH_INTERVAL_CHOICES = [
        (15, 'Every 15 minutes'),
        (30, 'Every 30 minutes'),
        (60, 'Every hour'),
        (120, 'Every 2 hours'),
        (240, 'Every 4 hours'),
        (360, 'Every 6 hours'),
        (720, 'Every 12 hours'),
        (1440, 'Daily'),
    ]
    
    url = models.URLField(max_length=2048, unique=True, help_text="Base URL of the website")
    name = models.CharField(max_length=255, help_text="Name of the website")
    active = models.BooleanField(default=True, help_text="Whether to actively crawl this website")
    
    # Scheduling settings
    auto_fetch_enabled = models.BooleanField(default=True, help_text="Enable automatic content fetching")
    fetch_interval_minutes = models.IntegerField(
        default=60,
        choices=FETCH_INTERVAL_CHOICES,
        help_text="How often to fetch new content (in minutes)"
    )
    last_auto_fetch = models.DateTimeField(null=True, blank=True, help_text="Last automatic fetch time")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} ({self.url})"
    
    def is_due_for_fetch(self):
        """Check if this website is due for content fetching."""
        if not self.auto_fetch_enabled or not self.active:
            return False
        
        if not self.last_auto_fetch:
            return True
        
        from datetime import timedelta
        next_fetch_time = self.last_auto_fetch + timedelta(minutes=self.fetch_interval_minutes)
        return timezone.now() >= next_fetch_time


class Feed(models.Model):
    """Model to store discovered RSS feeds and sitemaps."""
    FEED_TYPE_CHOICES = [
        ('RSS', 'RSS Feed'),
        ('ATOM', 'Atom Feed'),
        ('SITEMAP', 'Sitemap'),
    ]
    
    website = models.ForeignKey(Website, on_delete=models.CASCADE, related_name='feeds')
    feed_url = models.URLField(max_length=2048, unique=True, help_text="URL of the RSS feed or sitemap")
    feed_type = models.CharField(max_length=10, choices=FEED_TYPE_CHOICES)
    title = models.CharField(max_length=255, blank=True, help_text="Title of the feed")
    description = models.TextField(blank=True, help_text="Description of the feed")
    last_checked = models.DateTimeField(null=True, blank=True, help_text="Last time this feed was checked")
    last_successful_fetch = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True, help_text="Whether to actively check this feed")
    error_count = models.IntegerField(default=0, help_text="Number of consecutive fetch errors")
    last_error = models.TextField(blank=True, help_text="Last error message")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['website', 'feed_type', 'title']
    
    def __str__(self):
        return f"{self.website.name} - {self.feed_type}: {self.title or self.feed_url}"
    
    def mark_checked(self, success=True, error_message=''):
        """Update feed status after checking."""
        self.last_checked = timezone.now()
        if success:
            self.last_successful_fetch = timezone.now()
            self.error_count = 0
            self.last_error = ''
        else:
            self.error_count += 1
            self.last_error = error_message
            # Deactivate feed after 5 consecutive errors
            if self.error_count >= 5:
                self.active = False
        self.save()


class Article(models.Model):
    """Model to store fetched articles/content from feeds."""
    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name='articles')
    title = models.CharField(max_length=500)
    url = models.URLField(max_length=2048, unique=True, db_index=True)
    content = models.TextField(blank=True, help_text="Full text content of the article")
    summary = models.TextField(blank=True, help_text="Summary or excerpt")
    author = models.CharField(max_length=255, blank=True)
    published_date = models.DateTimeField(null=True, blank=True, db_index=True)
    fetched_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True, 
                                  help_text="Hash of content to detect updates")
    raw_data = models.JSONField(default=dict, blank=True, 
                               help_text="Original raw data from feed")
    tags = models.JSONField(default=list, blank=True,
                          help_text="Categories/tags from the feed")
    images = models.JSONField(default=list, blank=True,
                           help_text="Images found in the article")
    featured_image = models.URLField(max_length=2048, blank=True,
                                   help_text="Main/featured image URL")
    additional_feeds = models.ManyToManyField(Feed, blank=True, 
                                             related_name='cross_posted_articles',
                                             help_text="Other feeds where this article appeared")
    
    class Meta:
        ordering = ['-published_date', '-fetched_at']
        indexes = [
            models.Index(fields=['-published_date']),
            models.Index(fields=['feed', '-published_date']),
        ]
    
    def __str__(self):
        return f"{self.title} ({self.feed.website.name})"
    
    def save(self, *args, **kwargs):
        """Calculate content hash before saving."""
        if self.content:
            content_to_hash = f"{self.title}{self.content}{self.summary}"
            self.content_hash = hashlib.sha256(content_to_hash.encode()).hexdigest()
        super().save(*args, **kwargs)
    
    @classmethod
    def exists_with_same_content(cls, url, title, content, summary=''):
        """Check if an article with the same content already exists."""
        content_to_hash = f"{title}{content}{summary}"
        content_hash = hashlib.sha256(content_to_hash.encode()).hexdigest()
        return cls.objects.filter(url=url, content_hash=content_hash).exists()
    
    def get_similar_articles(self, max_results=10, days_back=30):
        """Get similar articles using both analysis and similarity detection."""
        similar_articles = []
        seen_ids = {self.id}  # Track seen IDs to avoid duplicates
        
        # First, check if this article has been analyzed
        if hasattr(self, 'analysis') and self.analysis.similar_articles.exists():
            # Get similar articles from analysis
            for article in self.analysis.similar_articles.select_related('feed__website').all()[:max_results]:
                if article.id not in seen_ids:
                    similar_articles.append((article, None))  # No scores from analysis
                    seen_ids.add(article.id)
        
        # If we need more results, use similarity detector
        if len(similar_articles) < max_results:
            try:
                from .similarity_detector import SimilarityDetector
                detector = SimilarityDetector()
                detected_similar = detector.find_similar_articles(
                    self,
                    threshold=0.4,  # Lower threshold for more results
                    max_results=max_results * 2,  # Get more to filter
                    days_back=days_back
                )
                
                # Add detected similar articles
                for article, scores in detected_similar:
                    if article.id not in seen_ids:
                        similar_articles.append((article, scores))
                        seen_ids.add(article.id)
                        if len(similar_articles) >= max_results:
                            break
            except Exception as e:
                # If similarity detector fails, continue with what we have
                pass
        
        return similar_articles[:max_results]


class ArticleAnalysis(models.Model):
    """Model to store AI analysis of articles."""
    article = models.OneToOneField('Article', on_delete=models.CASCADE, related_name='analysis')
    ai_summary = models.TextField(help_text="AI-generated summary")
    topics = models.JSONField(default=list, help_text="List of topics/categories")
    entities = models.JSONField(default=dict, help_text="Named entities (people, orgs, locations)")
    sentiment = models.CharField(max_length=20, default='neutral', 
                                help_text="Sentiment: positive/negative/neutral")
    keywords = models.JSONField(default=list, help_text="SEO keywords")
    similar_articles = models.ManyToManyField('Article', blank=True,
                                             related_name='similar_to',
                                             help_text="Articles with similar content")
    duplicate_of = models.ForeignKey('Article', null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name='duplicates',
                                    help_text="Original article if this is a duplicate")
    analyzed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-analyzed_at']
        verbose_name_plural = "Article analyses"
    
    def __str__(self):
        return f"Analysis of: {self.article.title[:50]}"


class GeneratedContent(models.Model):
    """Model to store AI-generated content based on source articles."""
    STYLE_CHOICES = [
        ('news', 'News Article'),
        ('blog', 'Blog Post'),
        ('analysis', 'Analysis'),
        ('summary', 'Summary'),
    ]
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('review', 'Under Review'),
        ('published', 'Published'),
        ('archived', 'Archived'),
    ]
    
    title = models.CharField(max_length=500)
    subtitle = models.CharField(max_length=500, blank=True)
    content = models.TextField(help_text="Generated content in HTML format")
    summary = models.TextField(blank=True, help_text="Brief summary")
    style = models.CharField(max_length=20, choices=STYLE_CHOICES, default='news')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    
    # Source articles used for generation
    source_articles = models.ManyToManyField('Article', related_name='generated_content',
                                            help_text="Source articles used")
    
    # Metadata
    topics = models.JSONField(default=list, help_text="Topics covered")
    media_items = models.JSONField(default=list, help_text="Associated media URLs and captions")
    generation_params = models.JSONField(default=dict, help_text="Parameters used for generation")
    
    # Web sources used for additional context
    web_sources = models.JSONField(default=list, help_text="Web sources used for research")
    
    # Tracking
    generated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)
    
    # Optional URL if published
    published_url = models.URLField(max_length=2048, blank=True)
    
    class Meta:
        ordering = ['-generated_at']
        verbose_name = "Generated content"
        verbose_name_plural = "Generated content"
    
    def __str__(self):
        return f"{self.title} ({self.style} - {self.status})"
    
    def get_source_attribution(self):
        """Get formatted source attribution for the generated content."""
        return [
            {"title": article.title, "url": article.url, "date": article.published_date}
            for article in self.source_articles.all()
        ]


class FetchLog(models.Model):
    """Model to log feed fetching activities."""
    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name='fetch_logs')
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    success = models.BooleanField(default=False)
    new_articles = models.IntegerField(default=0)
    updated_articles = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-started_at']
    
    def __str__(self):
        status = "Success" if self.success else "Failed"
        return f"{self.feed} - {self.started_at} - {status}"