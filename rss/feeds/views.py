from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponseRedirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.http import JsonResponse
from django.db.models import Count, Q, Max, Exists, OuterRef
from django.utils import timezone
from datetime import timedelta
from urllib.parse import unquote
from .models import Website, Feed, Article, FetchLog, ArticleAnalysis, GeneratedContent, ArticleCluster, Project
from .tasks import fetch_feed_content, discover_feeds_for_website, fetch_all_website_content, analyze_article_async
from .article_analyzer import ArticleAnalyzer, ContentGenerator


@login_required
def switch_project(request, project_id):
    """Switch to a different project."""
    try:
        project = Project.objects.get(id=project_id, active=True)
        request.session['current_project_id'] = project.id
        messages.success(request, f"Switched to project: {project.name}")
    except Project.DoesNotExist:
        messages.error(request, "Project not found")
    
    # Redirect to the referring page or home
    return redirect(request.META.get('HTTP_REFERER', 'feeds:home'))


class WebsiteListView(LoginRequiredMixin, ListView):
    model = Website
    template_name = 'feeds/website_list.html'
    context_object_name = 'websites'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by current project
        project_id = self.request.session.get('current_project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        
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
        
        # Add scheduling info
        from datetime import timedelta
        if self.object.auto_fetch_enabled and self.object.last_auto_fetch:
            next_fetch = self.object.last_auto_fetch + timedelta(minutes=self.object.fetch_interval_minutes)
            context['next_scheduled_fetch'] = next_fetch
        
        return context


class WebsiteCreateView(LoginRequiredMixin, CreateView):
    model = Website
    template_name = 'feeds/website_form.html'
    fields = ['url', 'name', 'active', 'auto_fetch_enabled', 'fetch_interval_minutes']
    success_url = reverse_lazy('feeds:website-list')
    
    def form_valid(self, form):
        # Set the current project
        project_id = self.request.session.get('current_project_id')
        if project_id:
            form.instance.project_id = project_id
        else:
            # Default to first project if none selected
            project = Project.objects.filter(active=True).first()
            if project:
                form.instance.project = project
        
        response = super().form_valid(form)
        messages.success(self.request, f"Website '{self.object.name}' created successfully!")
        # Trigger feed discovery asynchronously
        discover_feeds_for_website.delay(self.object.id)
        return response


class WebsiteUpdateView(LoginRequiredMixin, UpdateView):
    model = Website
    template_name = 'feeds/website_form.html'
    fields = ['url', 'name', 'active', 'auto_fetch_enabled', 'fetch_interval_minutes']
    success_url = reverse_lazy('feeds:website-list')
    
    def form_valid(self, form):
        messages.success(self.request, f"Website '{self.object.name}' updated successfully!")
        return super().form_valid(form)


class WebsiteDeleteView(LoginRequiredMixin, DeleteView):
    model = Website
    template_name = 'feeds/website_confirm_delete.html'
    success_url = reverse_lazy('feeds:website-list')
    
    def get(self, request, *args, **kwargs):
        # For GET requests, show confirmation page
        return super().get(request, *args, **kwargs)
    
    def post(self, request, *args, **kwargs):
        # For POST requests, delete directly without showing confirmation
        self.object = self.get_object()
        website_name = self.object.name
        success_url = self.get_success_url()
        
        # Delete the website and all related data
        self.object.delete()
        
        messages.success(request, f"Website '{website_name}' and all its data have been deleted successfully!")
        return HttpResponseRedirect(success_url)


class FeedListView(LoginRequiredMixin, ListView):
    model = Feed
    template_name = 'feeds/feed_list.html'
    context_object_name = 'feeds'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = super().get_queryset().select_related('website')
        
        # Filter by current project
        project_id = self.request.session.get('current_project_id')
        if project_id:
            queryset = queryset.filter(website__project_id=project_id)
        
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
        
        # Filter by current project
        project_id = self.request.session.get('current_project_id')
        if project_id:
            queryset = queryset.filter(feed__website__project_id=project_id)
        
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
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get similar articles
        similar_articles_with_scores = self.object.get_similar_articles(max_results=8)
        
        # Extract just the articles for the template (scores are optional)
        context['similar_articles'] = [article for article, scores in similar_articles_with_scores]
        
        # Check if article has been analyzed
        context['has_analysis'] = hasattr(self.object, 'analysis')
        
        return context


class ArticlesByTopicView(LoginRequiredMixin, ListView):
    """View to show all articles with a specific topic."""
    model = Article
    template_name = 'feeds/articles_by_topic.html'
    context_object_name = 'articles'
    paginate_by = 25
    
    def get_queryset(self):
        # Decode the topic from URL
        topic = unquote(self.kwargs['topic'])
        
        # Get articles with this topic in their analysis
        queryset = Article.objects.filter(
            analysis__topics__icontains=topic
        ).select_related(
            'feed__website', 'analysis'
        ).order_by('-published_date')
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['topic'] = unquote(self.kwargs['topic'])
        context['total_count'] = self.get_queryset().count()
        return context


class ArticlesByCategoryView(LoginRequiredMixin, ListView):
    """View to show all articles with a specific feed category/tag."""
    model = Article
    template_name = 'feeds/articles_by_category.html'
    context_object_name = 'articles'
    paginate_by = 25
    
    def get_queryset(self):
        # Decode the category from URL
        category = unquote(self.kwargs['category'])
        
        # Get articles with this category in their tags
        # Check both the tags field and raw_data.tags
        queryset = Article.objects.filter(
            Q(tags__icontains=category) |
            Q(raw_data__tags__icontains=category)
        ).select_related(
            'feed__website'
        ).order_by('-published_date')
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['category'] = unquote(self.kwargs['category'])
        context['total_count'] = self.get_queryset().count()
        return context


class ArticlesByPersonView(LoginRequiredMixin, ListView):
    """View to show all articles mentioning a specific person."""
    model = Article
    template_name = 'feeds/articles_by_entity.html'
    context_object_name = 'articles'
    paginate_by = 25
    
    def get_queryset(self):
        # Decode the person name from URL
        person = unquote(self.kwargs['person'])
        
        # Get articles with this person in their entities
        queryset = Article.objects.filter(
            analysis__entities__people__icontains=person
        ).select_related(
            'feed__website', 'analysis'
        ).order_by('-published_date')
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['entity_type'] = 'Person'
        context['entity_name'] = unquote(self.kwargs['person'])
        context['total_count'] = self.get_queryset().count()
        return context


class ArticlesByOrganizationView(LoginRequiredMixin, ListView):
    """View to show all articles mentioning a specific organization."""
    model = Article
    template_name = 'feeds/articles_by_entity.html'
    context_object_name = 'articles'
    paginate_by = 25
    
    def get_queryset(self):
        # Decode the organization name from URL
        organization = unquote(self.kwargs['organization'])
        
        # Get articles with this organization in their entities
        queryset = Article.objects.filter(
            analysis__entities__organizations__icontains=organization
        ).select_related(
            'feed__website', 'analysis'
        ).order_by('-published_date')
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['entity_type'] = 'Organization'
        context['entity_name'] = unquote(self.kwargs['organization'])
        context['total_count'] = self.get_queryset().count()
        return context


class ArticlesByLocationView(LoginRequiredMixin, ListView):
    """View to show all articles mentioning a specific location."""
    model = Article
    template_name = 'feeds/articles_by_entity.html'
    context_object_name = 'articles'
    paginate_by = 25
    
    def get_queryset(self):
        # Decode the location name from URL
        location = unquote(self.kwargs['location'])
        
        # Get articles with this location in their entities
        queryset = Article.objects.filter(
            analysis__entities__locations__icontains=location
        ).select_related(
            'feed__website', 'analysis'
        ).order_by('-published_date')
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['entity_type'] = 'Location'
        context['entity_name'] = unquote(self.kwargs['location'])
        context['total_count'] = self.get_queryset().count()
        return context


class ArticlesByKeywordView(LoginRequiredMixin, ListView):
    """View to show all articles with a specific SEO keyword."""
    model = Article
    template_name = 'feeds/articles_by_keyword.html'
    context_object_name = 'articles'
    paginate_by = 25
    
    def get_queryset(self):
        # Decode the keyword from URL
        keyword = unquote(self.kwargs['keyword'])
        
        # Get articles with this keyword in their analysis
        queryset = Article.objects.filter(
            analysis__keywords__icontains=keyword
        ).select_related(
            'feed__website', 'analysis'
        ).order_by('-published_date')
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['keyword'] = unquote(self.kwargs['keyword'])
        context['total_count'] = self.get_queryset().count()
        return context


@login_required
def home_view(request):
    from django.conf import settings
    from datetime import timedelta
    
    # Get current project
    project_id = request.session.get('current_project_id')
    
    # Base querysets filtered by project
    website_qs = Website.objects.all()
    feed_qs = Feed.objects.all()
    article_qs = Article.objects.all()
    
    if project_id:
        website_qs = website_qs.filter(project_id=project_id)
        feed_qs = feed_qs.filter(website__project_id=project_id)
        article_qs = article_qs.filter(feed__website__project_id=project_id)
    
    # Get scheduled websites info
    scheduled_websites = website_qs.filter(
        active=True, 
        auto_fetch_enabled=True
    ).order_by('last_auto_fetch')
    
    websites_due_soon = []
    for website in scheduled_websites[:10]:
        if website.last_auto_fetch:
            next_fetch = website.last_auto_fetch + timedelta(minutes=website.fetch_interval_minutes)
            websites_due_soon.append({
                'website': website,
                'next_fetch': next_fetch,
                'is_overdue': timezone.now() > next_fetch
            })
        else:
            websites_due_soon.append({
                'website': website,
                'next_fetch': None,
                'is_overdue': True
            })
    
    context = {
        'website_count': website_qs.filter(active=True).count(),
        'feed_count': feed_qs.filter(active=True).count(),
        'article_count': article_qs.count(),
        'scheduled_website_count': scheduled_websites.count(),
        'websites_due_soon': websites_due_soon,
        'recent_articles': article_qs.select_related('feed', 'feed__website')[:10],
        'recent_fetch_logs': FetchLog.objects.filter(
            feed__website__project_id=project_id if project_id else None
        ).select_related('feed', 'feed__website')[:10] if project_id else FetchLog.objects.select_related('feed', 'feed__website')[:10],
        'feeds_with_errors': feed_qs.filter(error_count__gt=0, active=True).select_related('website')[:5],
        'claude_enabled': bool(getattr(settings, 'OPENROUTER_API_KEY', None)),
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
        task = discover_feeds_for_website.delay(website.id)
        # Store task ID in session for progress tracking
        request.session[f'discover_task_{pk}'] = task.id
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


@login_required
def discover_progress_api(request, pk):
    """
    API endpoint to get the current feed discovery progress for a website.
    Returns JSON with progress information.
    """
    from celery.result import AsyncResult
    
    website = get_object_or_404(Website, pk=pk)
    task_id = request.session.get(f'discover_task_{pk}')
    
    if not task_id:
        return JsonResponse({
            'in_progress': False,
            'status': 'No discovery task found',
            'feeds_found': website.feeds.count()
        })
    
    task = AsyncResult(task_id)
    
    response_data = {
        'in_progress': not task.ready(),
        'status': task.state,
        'feeds_found': website.feeds.count()
    }
    
    if task.successful():
        response_data['result'] = str(task.result)
        response_data['status'] = 'completed'
        # Clear the task from session
        del request.session[f'discover_task_{pk}']
    elif task.failed():
        response_data['error'] = str(task.info)
        response_data['status'] = 'failed'
        # Clear the task from session
        del request.session[f'discover_task_{pk}']
    elif task.state == 'PENDING':
        response_data['status'] = 'pending'
    elif task.state == 'PROGRESS':
        response_data['current'] = task.info.get('current', 0)
        response_data['total'] = task.info.get('total', 0)
        response_data['status'] = task.info.get('status', 'Processing...')
    
    return JsonResponse(response_data)


@login_required
def fetch_progress_api(request, pk):
    """
    API endpoint to get the current fetch progress for a website.
    Returns JSON with progress information.
    """
    website = get_object_or_404(Website, pk=pk)
    
    # Get total feeds for the website
    total_feeds = website.feeds.filter(active=True).count()
    
    if total_feeds == 0:
        return JsonResponse({
            'in_progress': False,
            'message': 'No active feeds'
        })
    
    # Check recent fetch logs (last 10 minutes)
    from datetime import timedelta
    recent_cutoff = timezone.now() - timedelta(minutes=10)
    
    recent_logs = FetchLog.objects.filter(
        feed__website=website,
        started_at__gte=recent_cutoff
    )
    
    # Count completed and in-progress fetches
    completed_count = recent_logs.filter(completed_at__isnull=False).count()
    in_progress_count = recent_logs.filter(completed_at__isnull=True).count()
    
    # Check if fetch is still active
    very_recent_cutoff = timezone.now() - timedelta(seconds=30)
    very_recent_logs = recent_logs.filter(started_at__gte=very_recent_cutoff)
    
    is_active = very_recent_logs.exists() or in_progress_count > 0
    
    # Calculate progress percentage
    if completed_count + in_progress_count > 0:
        # Use the actual progress based on recent logs
        progress_feeds = completed_count + in_progress_count
        # Don't exceed total feeds
        progress_feeds = min(progress_feeds, total_feeds)
        percentage = (completed_count / total_feeds) * 100
    else:
        progress_feeds = 0
        percentage = 0
    
    # Get recent article count
    recent_articles = Article.objects.filter(
        feed__website=website,
        fetched_at__gte=recent_cutoff
    ).count()
    
    return JsonResponse({
        'in_progress': is_active,
        'total_feeds': total_feeds,
        'completed_feeds': completed_count,
        'in_progress_feeds': in_progress_count,
        'percentage': round(percentage, 1),
        'recent_articles': recent_articles,
        'message': f'Processing {in_progress_count} feeds...' if is_active else 'Fetch complete'
    })


def logout_view(request):
    """Custom logout view that handles GET requests"""
    logout(request)
    messages.success(request, "You have been successfully logged out.")
    return redirect('login')


# Article Analysis Views
class AnalysisDashboardView(LoginRequiredMixin, ListView):
    """Dashboard showing article analysis overview."""
    model = Article
    template_name = 'feeds/analysis_dashboard.html'
    context_object_name = 'recent_articles'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = Article.objects.select_related(
            'feed', 'feed__website', 'analysis'
        ).annotate(
            has_analysis=Exists(
                ArticleAnalysis.objects.filter(article=OuterRef('pk'))
            )
        )
        
        # Filter options
        filter_type = self.request.GET.get('filter', 'all')
        if filter_type == 'analyzed':
            queryset = queryset.filter(has_analysis=True)
        elif filter_type == 'unanalyzed':
            queryset = queryset.filter(has_analysis=False)
        elif filter_type == 'duplicates':
            queryset = queryset.filter(
                analysis__duplicate_of__isnull=False
            )
        
        # Search
        search = self.request.GET.get('search', '')
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(feed__website__name__icontains=search)
            )
        
        # Date filter
        days = self.request.GET.get('days', '7')
        if days.isdigit():
            since_date = timezone.now() - timedelta(days=int(days))
            queryset = queryset.filter(fetched_at__gte=since_date)
        
        return queryset.order_by('-published_date')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Statistics
        total_articles = Article.objects.count()
        analyzed_count = ArticleAnalysis.objects.count()
        duplicate_count = ArticleAnalysis.objects.filter(
            duplicate_of__isnull=False
        ).count()
        generated_count = GeneratedContent.objects.count()
        
        context['stats'] = {
            'total_articles': total_articles,
            'analyzed_count': analyzed_count,
            'analysis_percentage': (analyzed_count / total_articles * 100) if total_articles > 0 else 0,
            'duplicate_count': duplicate_count,
            'generated_count': generated_count,
        }
        
        # Recent analyses
        context['recent_analyses'] = ArticleAnalysis.objects.select_related(
            'article', 'article__feed__website'
        ).order_by('-analyzed_at')[:5]
        
        # Recent generated content
        context['recent_generated'] = GeneratedContent.objects.order_by(
            '-generated_at'
        )[:5]
        
        # Get websites for batch analysis form
        context['websites'] = Website.objects.filter(active=True).order_by('name')
        
        return context


class ArticleAnalysisDetailView(LoginRequiredMixin, DetailView):
    """Detailed view of article analysis."""
    model = Article
    template_name = 'feeds/article_analysis_detail.html'
    context_object_name = 'article'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get or create analysis
        if hasattr(self.object, 'analysis'):
            context['analysis'] = self.object.analysis
            
            # First try to get similar articles from the analysis
            similar_from_analysis = self.object.analysis.similar_articles.select_related(
                'feed__website'
            ).all()[:5]
            
            # Also use the similarity detector for better matches
            from .similarity_detector import SimilarityDetector
            detector = SimilarityDetector()
            similar_detected = detector.find_similar_articles(
                self.object,
                threshold=0.5,
                max_results=10,
                days_back=30
            )
            
            # Combine both sources (avoiding duplicates)
            seen_ids = set()
            similar_articles = []
            
            # Add articles from analysis first
            for article in similar_from_analysis:
                if article.id not in seen_ids and article.id != self.object.id:
                    similar_articles.append(article)
                    seen_ids.add(article.id)
            
            # Add detected similar articles
            for article, scores in similar_detected:
                if article.id not in seen_ids and article.id != self.object.id:
                    similar_articles.append(article)
                    seen_ids.add(article.id)
                    if len(similar_articles) >= 10:
                        break
            
            context['similar_articles'] = similar_articles
        else:
            context['analysis'] = None
            context['similar_articles'] = []
        
        return context


@login_required
def analyze_article_view(request, pk):
    """Trigger article analysis."""
    article = get_object_or_404(Article, pk=pk)
    
    # Check if force re-analyze is requested
    force = request.GET.get('force') == 'true' or request.POST.get('force') == 'true'
    
    if hasattr(article, 'analysis') and not force:
        messages.info(request, "Article has already been analyzed. Add ?force=true to re-analyze.")
    else:
        if hasattr(article, 'analysis') and force:
            # Delete existing analysis to allow re-analysis
            article.analysis.delete()
            messages.info(request, "Re-analyzing article with updated extraction...")
        
        # Queue for analysis
        analyze_article_async.delay(article.id, find_similar=True)
        messages.success(request, "Article queued for analysis. This may take a moment.")
    
    return redirect('feeds:article-analysis-detail', pk=article.pk)


@login_required
def batch_analyze_view(request):
    """Batch analyze multiple articles."""
    if request.method == 'POST':
        website_id = request.POST.get('website_id')
        days = int(request.POST.get('days', 7))
        limit = int(request.POST.get('limit', 10))
        
        # Get articles to analyze
        queryset = Article.objects.filter(
            analysis__isnull=True,
            fetched_at__gte=timezone.now() - timedelta(days=days)
        )
        
        if website_id:
            queryset = queryset.filter(feed__website_id=website_id)
        
        articles = queryset[:limit]
        
        # Queue for analysis
        count = 0
        for article in articles:
            analyze_article_async.delay(article.id, find_similar=True)
            count += 1
        
        messages.success(request, f"Queued {count} articles for analysis.")
        
    return redirect('feeds:analysis-dashboard')


class GeneratedContentListView(LoginRequiredMixin, ListView):
    """List view for generated content."""
    model = GeneratedContent
    template_name = 'feeds/generated_content_list.html'
    context_object_name = 'content_list'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by status
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        # Filter by style
        style = self.request.GET.get('style')
        if style:
            queryset = queryset.filter(style=style)
        
        return queryset.order_by('-generated_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = GeneratedContent.STATUS_CHOICES
        context['style_choices'] = GeneratedContent.STYLE_CHOICES
        return context


class GeneratedContentDetailView(LoginRequiredMixin, DetailView):
    """Detail view for generated content."""
    model = GeneratedContent
    template_name = 'feeds/generated_content_detail.html'
    context_object_name = 'content'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['source_articles'] = self.object.source_articles.select_related(
            'feed__website'
        ).all()
        return context


class GenerateContentView(LoginRequiredMixin, CreateView):
    """View to generate new content from selected articles."""
    model = GeneratedContent
    template_name = 'feeds/generate_content.html'
    fields = []
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get article ID from URL parameters if provided
        source_article_id = self.request.GET.get('source')
        
        if source_article_id:
            # If a source article is specified, find similar articles
            try:
                source_article = Article.objects.get(id=source_article_id)
                context['source_article'] = source_article
                
                # Use similarity detector to find related articles
                from feeds.similarity_detector import SimilarityDetector
                detector = SimilarityDetector()
                
                similar = detector.find_similar_articles(
                    source_article,
                    threshold=0.4,  # Lower threshold to get more related articles
                    max_results=30,
                    days_back=30
                )
                
                # Extract just the articles
                related_articles = [article for article, scores in similar]
                
                # Also add the source article at the beginning
                context['available_articles'] = [source_article] + related_articles
                context['article_groups'] = [{
                    'topic': f"Articles related to: {source_article.title[:60]}",
                    'articles': context['available_articles']
                }]
                
            except Article.DoesNotExist:
                # Fallback to topic grouping
                context['article_groups'] = self.get_topic_grouped_articles()
        else:
            # Group articles by topics
            context['article_groups'] = self.get_topic_grouped_articles()
        
        context['style_choices'] = GeneratedContent.STYLE_CHOICES
        
        return context
    
    def get_topic_grouped_articles(self):
        """Group analyzed articles by their topics."""
        from collections import defaultdict
        from datetime import timedelta
        
        # Get recent analyzed articles
        recent_date = timezone.now() - timedelta(days=7)
        analyzed_articles = Article.objects.filter(
            analysis__isnull=False,
            published_date__gte=recent_date
        ).select_related(
            'feed__website', 'analysis'
        ).order_by('-published_date')[:200]
        
        # Group by topics
        topic_groups = defaultdict(list)
        articles_without_topics = []
        
        for article in analyzed_articles:
            if hasattr(article, 'analysis') and article.analysis.topics:
                # Use the first topic as the main grouping
                main_topic = article.analysis.topics[0] if article.analysis.topics else None
                if main_topic:
                    topic_groups[main_topic].append(article)
                else:
                    articles_without_topics.append(article)
            else:
                articles_without_topics.append(article)
        
        # Convert to list of dicts for template
        grouped_articles = []
        
        # Sort topics by number of articles (most articles first)
        sorted_topics = sorted(topic_groups.items(), key=lambda x: len(x[1]), reverse=True)
        
        for topic, articles in sorted_topics[:10]:  # Limit to top 10 topics
            if len(articles) >= 2:  # Only show topics with at least 2 articles
                grouped_articles.append({
                    'topic': topic.title() if topic else 'Uncategorized',
                    'articles': articles[:10]  # Limit articles per topic
                })
        
        # Add uncategorized articles if any
        if articles_without_topics and len(articles_without_topics) >= 2:
            grouped_articles.append({
                'topic': 'Other Recent Articles',
                'articles': articles_without_topics[:10]
            })
        
        # If no good grouping, just return recent articles as one group
        if not grouped_articles:
            recent_articles = Article.objects.filter(
                analysis__isnull=False
            ).select_related(
                'feed__website', 'analysis'
            ).order_by('-published_date')[:30]
            
            grouped_articles = [{
                'topic': 'Recent Analyzed Articles',
                'articles': list(recent_articles)
            }]
        
        return grouped_articles
    
    def post(self, request, *args, **kwargs):
        article_ids = request.POST.getlist('article_ids')
        style = request.POST.get('style', 'news')
        target_length = int(request.POST.get('target_length', 800))
        
        # Keygrip-specific parameters
        use_keygrip = request.POST.get('use_keygrip') == 'on'
        voice_prompt_id = request.POST.get('voice_prompt_id', 'product_writer')
        use_writing_samples = request.POST.get('use_writing_samples') == 'on'
        use_web_search = request.POST.get('use_web_search') == 'on'
        
        if not article_ids:
            messages.error(request, "Please select at least one article.")
            return redirect('feeds:generate-content')
        
        # Get articles
        articles = Article.objects.filter(id__in=article_ids)
        
        if not articles.exists():
            messages.error(request, "No valid articles selected.")
            return redirect('feeds:generate-content')
        
        # Generate content
        generator = ContentGenerator()
        
        try:
            if use_keygrip:
                # Use Keygrip for generation
                result = generator.generate_with_keygrip(
                    source_articles=list(articles),
                    voice_prompt_id=voice_prompt_id,
                    use_writing_samples=use_writing_samples,
                    use_web_search=use_web_search
                )
            else:
                # Use Claude for generation
                result = generator.generate_article(
                    source_articles=list(articles),
                    style=style,
                    target_length=target_length
                )
            
            # Save generated content
            generated = GeneratedContent.objects.create(
                title=result.get('title', 'Generated Article'),
                subtitle=result.get('subtitle', ''),
                content=result.get('content', ''),
                summary=result.get('summary', ''),
                style=style if not use_keygrip else voice_prompt_id,
                topics=result.get('topics', []),
                media_items=result.get('media_items', []),
                generation_params={
                    'style': style if not use_keygrip else voice_prompt_id,
                    'target_length': target_length,
                    'source_count': articles.count(),
                    'generation_method': result.get('generation_method', 'claude'),
                    'use_keygrip': use_keygrip,
                    'voice_prompt_id': voice_prompt_id if use_keygrip else None,
                    'use_writing_samples': use_writing_samples if use_keygrip else None,
                    'use_web_search': use_web_search if use_keygrip else None
                }
            )
            
            # Add source articles
            generated.source_articles.set(articles)
            
            # Add web sources if available
            if 'web_sources' in result and result['web_sources']:
                generated.web_sources = result['web_sources']
                generated.save()
            
            messages.success(request, "Content generated successfully!")
            return redirect('feeds:generated-content-detail', pk=generated.pk)
            
        except Exception as e:
            messages.error(request, f"Error generating content: {str(e)}")
            return redirect('feeds:generate-content')


@login_required
def update_content_status(request, pk):
    """Update the status of generated content."""
    content = get_object_or_404(GeneratedContent, pk=pk)
    
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in dict(GeneratedContent.STATUS_CHOICES):
            content.status = new_status
            if new_status == 'published':
                content.published_at = timezone.now()
            content.save()
            messages.success(request, f"Content status updated to {new_status}.")
        else:
            messages.error(request, "Invalid status.")
    
    return redirect('feeds:generated-content-detail', pk=content.pk)


@login_required
def article_clusters(request):
    """
    Display AI-powered article clusters.
    Shows clusters of related articles identified by AI as covering the same event/story.
    """
    # Get parameters
    hours = int(request.GET.get('hours', 48))
    min_cluster_size = int(request.GET.get('min_size', 2))
    
    # Get time window
    since = timezone.now() - timedelta(hours=hours)
    
    # Get AI-identified clusters from database
    ai_clusters = ArticleCluster.objects.filter(
        is_active=True,
        latest_article__gte=since
    ).prefetch_related(
        'articles',
        'articles__feed__website'
    ).order_by('-source_count', '-latest_article')
    
    # Convert to view format
    clusters = []
    for cluster in ai_clusters:
        if cluster.articles.count() >= min_cluster_size:
            cluster_articles = list(cluster.articles.all())
            clusters.append({
                'main_article': cluster_articles[0] if cluster_articles else None,
                'articles': cluster_articles,
                'topics': cluster.main_topics,
                'entities': cluster.key_entities,
                'size': cluster.articles.count(),
                'source_diversity': cluster.source_count,
                'title': cluster.title,
                'description': cluster.description,
                'confidence': cluster.confidence_score,
                'event_type': cluster.event_type,
                'earliest': cluster.earliest_article,
                'latest': cluster.latest_article,
                'time_span': (cluster.latest_article - cluster.earliest_article).total_seconds() / 3600 if cluster.earliest_article and cluster.latest_article else 0
            })
    
    # Count unanalyzed articles
    unanalyzed_count = Article.objects.filter(
        published_date__gte=since,
        analysis__isnull=True
    ).count()
    
    # Count total articles in time window
    total_articles = Article.objects.filter(
        published_date__gte=since
    ).count()
    
    context = {
        'clusters': clusters,
        'total_clusters': len(clusters),
        'hours': hours,
        'min_cluster_size': min_cluster_size,
        'unanalyzed_count': unanalyzed_count,
        'total_articles': total_articles,
        'time_options': [24, 48, 72, 168],
        'size_options': [2, 3, 4, 5, 10]
    }
    
    return render(request, 'feeds/article_clusters.html', context)
    

