from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.http import JsonResponse
from django.db.models import Count, Q, Max
from django.utils import timezone
from datetime import timedelta
from .models import Website, Feed, Article, FetchLog
from .tasks import fetch_feed_content, discover_feeds_for_website, fetch_all_website_content


class WebsiteListView(LoginRequiredMixin, ListView):
    model = Website
    template_name = 'feeds/website_list.html'
    context_object_name = 'websites'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get('search', '')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(url__icontains=search)
            )
        return queryset.annotate(
            feed_count=Count('feeds'),
            article_count=Count('feeds__articles')
        )


class WebsiteDetailView(LoginRequiredMixin, DetailView):
    model = Website
    template_name = 'feeds/website_detail.html'
    context_object_name = 'website'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['feeds'] = self.object.feeds.annotate(
            article_count=Count('articles')
        ).order_by('-active', 'feed_type', 'title')
        context['recent_articles'] = Article.objects.filter(
            feed__website=self.object
        ).select_related('feed')[:10]
        return context


class WebsiteCreateView(LoginRequiredMixin, CreateView):
    model = Website
    template_name = 'feeds/website_form.html'
    fields = ['url', 'name', 'active']
    success_url = reverse_lazy('feeds:website-list')
    
    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Website '{self.object.name}' created successfully!")
        # Trigger feed discovery asynchronously
        discover_feeds_for_website.delay(self.object.id)
        return response


class WebsiteUpdateView(LoginRequiredMixin, UpdateView):
    model = Website
    template_name = 'feeds/website_form.html'
    fields = ['url', 'name', 'active']
    success_url = reverse_lazy('feeds:website-list')
    
    def form_valid(self, form):
        messages.success(self.request, f"Website '{self.object.name}' updated successfully!")
        return super().form_valid(form)


class WebsiteDeleteView(LoginRequiredMixin, DeleteView):
    model = Website
    template_name = 'feeds/website_confirm_delete.html'
    success_url = reverse_lazy('feeds:website-list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(request, f"Website deleted successfully!")
        return super().delete(request, *args, **kwargs)


class FeedListView(LoginRequiredMixin, ListView):
    model = Feed
    template_name = 'feeds/feed_list.html'
    context_object_name = 'feeds'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = super().get_queryset().select_related('website')
        
        # Filter by website if specified
        website_id = self.request.GET.get('website')
        if website_id:
            queryset = queryset.filter(website_id=website_id)
        
        # Filter by feed type
        feed_type = self.request.GET.get('type')
        if feed_type:
            queryset = queryset.filter(feed_type=feed_type)
        
        # Filter by active status
        active = self.request.GET.get('active')
        if active == 'true':
            queryset = queryset.filter(active=True)
        elif active == 'false':
            queryset = queryset.filter(active=False)
        
        # Search
        search = self.request.GET.get('search', '')
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | 
                Q(feed_url__icontains=search) |
                Q(website__name__icontains=search)
            )
        
        return queryset.annotate(
            article_count=Count('articles'),
            latest_article=Max('articles__published_date')
        )
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['websites'] = Website.objects.all()
        context['feed_types'] = Feed.FEED_TYPE_CHOICES
        return context


class FeedDetailView(LoginRequiredMixin, DetailView):
    model = Feed
    template_name = 'feeds/feed_detail.html'
    context_object_name = 'feed'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['recent_articles'] = self.object.articles.all()[:20]
        context['fetch_logs'] = self.object.fetch_logs.all()[:10]
        context['stats'] = {
            'total_articles': self.object.articles.count(),
            'articles_last_week': self.object.articles.filter(
                fetched_at__gte=timezone.now() - timedelta(days=7)
            ).count(),
            'success_rate': self._calculate_success_rate()
        }
        return context
    
    def _calculate_success_rate(self):
        recent_logs = self.object.fetch_logs.all()[:20]
        if not recent_logs:
            return 0
        success_count = sum(1 for log in recent_logs if log.success)
        return (success_count / len(recent_logs)) * 100


class FeedUpdateView(LoginRequiredMixin, UpdateView):
    model = Feed
    template_name = 'feeds/feed_form.html'
    fields = ['title', 'description', 'active']
    
    def get_success_url(self):
        return reverse_lazy('feeds:feed-detail', kwargs={'pk': self.object.pk})
    
    def form_valid(self, form):
        messages.success(self.request, f"Feed '{self.object.title}' updated successfully!")
        return super().form_valid(form)


class ArticleListView(LoginRequiredMixin, ListView):
    model = Article
    template_name = 'feeds/article_list.html'
    context_object_name = 'articles'
    paginate_by = 25
    
    def get_queryset(self):
        queryset = super().get_queryset().select_related('feed', 'feed__website')
        
        # Filter by feed
        feed_id = self.request.GET.get('feed')
        if feed_id:
            queryset = queryset.filter(feed_id=feed_id)
        
        # Filter by website
        website_id = self.request.GET.get('website')
        if website_id:
            queryset = queryset.filter(feed__website_id=website_id)
        
        # Date range filter
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if date_from:
            queryset = queryset.filter(published_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(published_date__lte=date_to)
        
        # Search
        search = self.request.GET.get('search', '')
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | 
                Q(summary__icontains=search) |
                Q(author__icontains=search)
            )
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['feeds'] = Feed.objects.select_related('website').filter(active=True)
        context['websites'] = Website.objects.filter(active=True)
        return context


class ArticleDetailView(LoginRequiredMixin, DetailView):
    model = Article
    template_name = 'feeds/article_detail.html'
    context_object_name = 'article'


@login_required
def home_view(request):
    context = {
        'website_count': Website.objects.filter(active=True).count(),
        'feed_count': Feed.objects.filter(active=True).count(),
        'article_count': Article.objects.count(),
        'recent_articles': Article.objects.select_related('feed', 'feed__website')[:10],
        'recent_fetch_logs': FetchLog.objects.select_related('feed', 'feed__website')[:10],
        'feeds_with_errors': Feed.objects.filter(error_count__gt=0, active=True).select_related('website')[:5],
    }
    return render(request, 'feeds/home.html', context)


@login_required
def refresh_feed(request, pk):
    feed = get_object_or_404(Feed, pk=pk)
    if request.method == 'POST':
        # Trigger async task to refresh feed
        fetch_feed_content.delay(feed.id)
        messages.success(request, f"Feed '{feed.title}' refresh initiated!")
        return redirect('feeds:feed-detail', pk=pk)
    return redirect('feeds:feed-detail', pk=pk)


@login_required
def discover_feeds(request, pk):
    website = get_object_or_404(Website, pk=pk)
    if request.method == 'POST':
        # Trigger async task to discover feeds
        discover_feeds_for_website.delay(website.id)
        messages.success(request, f"Feed discovery initiated for '{website.name}'!")
        return redirect('feeds:website-detail', pk=pk)
    return redirect('feeds:website-detail', pk=pk)


@login_required
def fetch_all_content(request, pk):
    website = get_object_or_404(Website, pk=pk)
    if request.method == 'POST':
        # Trigger async task to fetch all content for all feeds
        fetch_all_website_content.delay(website.id)
        messages.success(request, f"Content fetching initiated for all feeds of '{website.name}'. This may take a while.")
        return redirect('feeds:website-detail', pk=pk)
    return redirect('feeds:website-detail', pk=pk)


@login_required
def feed_stats_api(request):
    days = int(request.GET.get('days', 7))
    start_date = timezone.now() - timedelta(days=days)
    
    stats = {
        'new_articles_by_day': [],
        'feeds_by_type': {},
        'top_feeds': [],
        'error_feeds': []
    }
    
    # Articles per day
    for i in range(days):
        date = start_date + timedelta(days=i)
        count = Article.objects.filter(
            fetched_at__date=date.date()
        ).count()
        stats['new_articles_by_day'].append({
            'date': date.date().isoformat(),
            'count': count
        })
    
    # Feeds by type
    for feed_type, label in Feed.FEED_TYPE_CHOICES:
        stats['feeds_by_type'][label] = Feed.objects.filter(
            feed_type=feed_type, active=True
        ).count()
    
    # Top feeds by article count
    top_feeds = Feed.objects.filter(active=True).annotate(
        article_count=Count('articles')
    ).order_by('-article_count')[:5]
    
    for feed in top_feeds:
        stats['top_feeds'].append({
            'id': feed.id,
            'title': feed.title or feed.feed_url,
            'website': feed.website.name,
            'article_count': feed.article_count
        })
    
    # Feeds with errors
    error_feeds = Feed.objects.filter(
        error_count__gt=0, active=True
    ).select_related('website')[:5]
    
    for feed in error_feeds:
        stats['error_feeds'].append({
            'id': feed.id,
            'title': feed.title or feed.feed_url,
            'website': feed.website.name,
            'error_count': feed.error_count,
            'last_error': feed.last_error
        })
    
    return JsonResponse(stats)


def logout_view(request):
    """Custom logout view that handles GET requests"""
    logout(request)
    messages.success(request, "You have been successfully logged out.")
    return redirect('login')