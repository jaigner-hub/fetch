"""
Django admin configuration for feeds app.
"""
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils import timezone
from .models import Website, Feed, Article, FetchLog


@admin.register(Website)
class WebsiteAdmin(admin.ModelAdmin):
    list_display = ['name', 'url', 'active', 'feed_count', 'created_at']
    list_filter = ['active', 'created_at']
    search_fields = ['name', 'url']
    date_hierarchy = 'created_at'
    actions = ['activate_websites', 'deactivate_websites', 'discover_feeds']
    
    def feed_count(self, obj):
        count = obj.feeds.count()
        return count
    feed_count.short_description = 'Feeds'
    
    def activate_websites(self, request, queryset):
        updated = queryset.update(active=True)
        self.message_user(request, f'{updated} websites activated.')
    activate_websites.short_description = 'Activate selected websites'
    
    def deactivate_websites(self, request, queryset):
        updated = queryset.update(active=False)
        self.message_user(request, f'{updated} websites deactivated.')
    deactivate_websites.short_description = 'Deactivate selected websites'
    
    def discover_feeds(self, request, queryset):
        from .tasks import discover_feeds_for_website
        
        count = 0
        for website in queryset:
            discover_feeds_for_website.delay(website.id)
            count += 1
        
        self.message_user(request, f'Queued feed discovery for {count} websites.')
    discover_feeds.short_description = 'Discover feeds for selected websites'


@admin.register(Feed)
class FeedAdmin(admin.ModelAdmin):
    list_display = ['title_display', 'website', 'feed_type', 'active', 
                   'last_checked', 'article_count', 'error_status']
    list_filter = ['feed_type', 'active', 'website', 'last_checked']
    search_fields = ['title', 'feed_url', 'website__name']
    date_hierarchy = 'last_checked'
    readonly_fields = ['last_checked', 'last_successful_fetch', 'error_count', 
                      'last_error', 'created_at', 'updated_at']
    actions = ['activate_feeds', 'deactivate_feeds', 'fetch_content', 'reset_errors']
    
    fieldsets = (
        (None, {
            'fields': ('website', 'feed_url', 'feed_type', 'title', 'description', 'active')
        }),
        ('Status', {
            'fields': ('last_checked', 'last_successful_fetch', 'error_count', 'last_error'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def title_display(self, obj):
        return obj.title or obj.feed_url[:50]
    title_display.short_description = 'Title/URL'
    
    def article_count(self, obj):
        count = obj.articles.count()
        if count > 0:
            url = reverse('admin:feeds_article_changelist') + f'?feed__id__exact={obj.id}'
            return format_html('<a href="{}">{} articles</a>', url, count)
        return count
    article_count.short_description = 'Articles'
    
    def error_status(self, obj):
        if obj.error_count > 0:
            return format_html(
                '<span style="color: red;">❌ {} errors</span>',
                obj.error_count
            )
        elif obj.last_successful_fetch:
            return format_html('<span style="color: green;">✓ OK</span>')
        else:
            return format_html('<span style="color: gray;">-</span>')
    error_status.short_description = 'Status'
    
    def activate_feeds(self, request, queryset):
        updated = queryset.update(active=True)
        self.message_user(request, f'{updated} feeds activated.')
    activate_feeds.short_description = 'Activate selected feeds'
    
    def deactivate_feeds(self, request, queryset):
        updated = queryset.update(active=False)
        self.message_user(request, f'{updated} feeds deactivated.')
    deactivate_feeds.short_description = 'Deactivate selected feeds'
    
    def fetch_content(self, request, queryset):
        from .tasks import fetch_feed_content
        
        count = 0
        for feed in queryset:
            fetch_feed_content.delay(feed.id)
            count += 1
        
        self.message_user(request, f'Queued content fetch for {count} feeds.')
    fetch_content.short_description = 'Fetch content from selected feeds'
    
    def reset_errors(self, request, queryset):
        updated = queryset.update(error_count=0, last_error='')
        self.message_user(request, f'Reset errors for {updated} feeds.')
    reset_errors.short_description = 'Reset error counters'


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ['title_short', 'feed', 'author', 'published_date', 'fetched_at']
    list_filter = ['feed__website', 'feed', 'published_date', 'fetched_at']
    search_fields = ['title', 'content', 'author', 'url']
    date_hierarchy = 'published_date'
    readonly_fields = ['url', 'content_hash', 'fetched_at', 'updated_at', 'raw_data']
    actions = ['fetch_full_content']
    
    fieldsets = (
        (None, {
            'fields': ('feed', 'title', 'url', 'author', 'published_date')
        }),
        ('Content', {
            'fields': ('summary', 'content'),
            'classes': ('wide',)
        }),
        ('Metadata', {
            'fields': ('content_hash', 'fetched_at', 'updated_at', 'raw_data'),
            'classes': ('collapse',)
        }),
    )
    
    def title_short(self, obj):
        if len(obj.title) > 60:
            return obj.title[:57] + '...'
        return obj.title
    title_short.short_description = 'Title'
    
    def fetch_full_content(self, request, queryset):
        from .tasks import fetch_article_full_content
        
        count = 0
        for article in queryset:
            if not article.content:
                fetch_article_full_content.delay(article.id)
                count += 1
        
        self.message_user(request, f'Queued full content fetch for {count} articles.')
    fetch_full_content.short_description = 'Fetch full content for selected articles'
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('feed', 'feed__website')


@admin.register(FetchLog)
class FetchLogAdmin(admin.ModelAdmin):
    list_display = ['feed', 'started_at', 'completed_at', 'success', 
                   'new_articles', 'updated_articles', 'duration']
    list_filter = ['success', 'feed__website', 'feed', 'started_at']
    search_fields = ['feed__title', 'feed__feed_url', 'error_message']
    date_hierarchy = 'started_at'
    readonly_fields = ['feed', 'started_at', 'completed_at', 'success', 
                      'new_articles', 'updated_articles', 'error_message']
    
    def duration(self, obj):
        if obj.completed_at and obj.started_at:
            delta = obj.completed_at - obj.started_at
            return f'{delta.total_seconds():.1f}s'
        return '-'
    duration.short_description = 'Duration'
    
    def has_add_permission(self, request):
        # Logs should only be created by the system
        return False
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('feed', 'feed__website')
