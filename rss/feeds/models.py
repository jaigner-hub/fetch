from django.db import models
from django.utils import timezone
import hashlib


class Website(models.Model):
    """Model to store websites to crawl for RSS feeds and sitemaps."""
    url = models.URLField(max_length=2048, unique=True, help_text="Base URL of the website")
    name = models.CharField(max_length=255, help_text="Name of the website")
    active = models.BooleanField(default=True, help_text="Whether to actively crawl this website")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} ({self.url})"


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