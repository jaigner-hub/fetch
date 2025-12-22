"""
Project management views.
"""
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.db.models import Count, Q
from django.utils.text import slugify
from .models import Project, Website, Feed, Article


class ProjectListView(LoginRequiredMixin, ListView):
    """List all projects with statistics."""
    model = Project
    template_name = 'feeds/project_list.html'
    context_object_name = 'projects'
    
    def get_queryset(self):
        return Project.objects.annotate(
            website_count=Count('websites', distinct=True),
            feed_count=Count('websites__feeds', distinct=True),
            article_count=Count('websites__feeds__articles', distinct=True)
        ).order_by('name')


class ProjectCreateView(LoginRequiredMixin, CreateView):
    """Create a new project."""
    model = Project
    template_name = 'feeds/project_form.html'
    fields = ['name', 'description', 'active']
    success_url = reverse_lazy('feeds:project-list')
    
    def form_valid(self, form):
        # Auto-generate slug from name
        form.instance.slug = slugify(form.instance.name)
        
        response = super().form_valid(form)
        messages.success(self.request, f"Project '{self.object.name}' created successfully!")
        
        # Set as current project
        self.request.session['current_project_id'] = self.object.id
        
        return response


class ProjectUpdateView(LoginRequiredMixin, UpdateView):
    """Edit an existing project."""
    model = Project
    template_name = 'feeds/project_form.html'
    fields = ['name', 'description', 'active']
    success_url = reverse_lazy('feeds:project-list')
    
    def form_valid(self, form):
        # Update slug if name changed
        form.instance.slug = slugify(form.instance.name)
        
        messages.success(self.request, f"Project '{self.object.name}' updated successfully!")
        return super().form_valid(form)


class ProjectDeleteView(LoginRequiredMixin, DeleteView):
    """Delete a project and all its data."""
    model = Project
    template_name = 'feeds/project_confirm_delete.html'
    success_url = reverse_lazy('feeds:project-list')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add counts of what will be deleted
        context['website_count'] = self.object.websites.count()
        context['feed_count'] = Feed.objects.filter(website__project=self.object).count()
        context['article_count'] = Article.objects.filter(feed__website__project=self.object).count()
        return context
    
    def delete(self, request, *args, **kwargs):
        project_name = self.get_object().name
        
        # If deleting current project, switch to another
        if request.session.get('current_project_id') == self.get_object().id:
            other_project = Project.objects.exclude(id=self.get_object().id).first()
            if other_project:
                request.session['current_project_id'] = other_project.id
            else:
                request.session.pop('current_project_id', None)
        
        response = super().delete(request, *args, **kwargs)
        messages.success(request, f"Project '{project_name}' and all its data have been deleted.")
        return response


@login_required
def project_detail(request, pk):
    """Show detailed project statistics and management options."""
    project = get_object_or_404(Project, pk=pk)
    
    # Get statistics
    website_count = project.websites.count()
    active_websites = project.websites.filter(active=True).count()
    
    feeds = Feed.objects.filter(website__project=project)
    feed_count = feeds.count()
    active_feeds = feeds.filter(active=True).count()
    
    articles = Article.objects.filter(feed__website__project=project)
    article_count = articles.count()
    
    # Recent activity
    recent_articles = articles.select_related('feed', 'feed__website').order_by('-fetched_at')[:10]
    recent_websites = project.websites.order_by('-created_at')[:5]
    
    # Feed type breakdown
    feed_types = feeds.values('feed_type').annotate(count=Count('id'))
    feed_type_dict = {ft['feed_type']: ft['count'] for ft in feed_types}
    
    context = {
        'project': project,
        'website_count': website_count,
        'active_websites': active_websites,
        'feed_count': feed_count,
        'active_feeds': active_feeds,
        'article_count': article_count,
        'recent_articles': recent_articles,
        'recent_websites': recent_websites,
        'rss_feeds': feed_type_dict.get('RSS', 0),
        'atom_feeds': feed_type_dict.get('ATOM', 0),
        'sitemap_feeds': feed_type_dict.get('SITEMAP', 0),
    }
    
    return render(request, 'feeds/project_detail.html', context)


@login_required
def duplicate_project(request, pk):
    """Duplicate a project structure (websites and feeds) without articles."""
    source_project = get_object_or_404(Project, pk=pk)
    
    if request.method == 'POST':
        new_name = request.POST.get('name')
        if not new_name:
            messages.error(request, "Project name is required")
            return redirect('feeds:project-detail', pk=pk)
        
        # Create new project
        new_project = Project.objects.create(
            name=new_name,
            slug=slugify(new_name),
            description=f"Duplicated from {source_project.name}. {source_project.description}",
            active=True
        )
        
        # Duplicate websites and feeds
        website_mapping = {}
        for website in source_project.websites.all():
            new_website = Website.objects.create(
                project=new_project,
                url=website.url,
                name=website.name,
                active=website.active,
                auto_fetch_enabled=website.auto_fetch_enabled,
                fetch_interval_minutes=website.fetch_interval_minutes
            )
            website_mapping[website.id] = new_website
            
            # Duplicate feeds for this website
            for feed in website.feeds.all():
                Feed.objects.create(
                    website=new_website,
                    feed_url=feed.feed_url,
                    feed_type=feed.feed_type,
                    title=feed.title,
                    description=feed.description,
                    active=feed.active
                )
        
        messages.success(request, f"Project '{new_name}' created with {len(website_mapping)} websites duplicated")
        return redirect('feeds:project-detail', pk=new_project.pk)
    
    return render(request, 'feeds/project_duplicate.html', {'project': source_project})


@login_required
def clear_project_articles(request, pk):
    """Clear all articles from a project."""
    project = get_object_or_404(Project, pk=pk)
    
    if request.method == 'POST':
        # Delete all articles for this project
        deleted = Article.objects.filter(feed__website__project=project).delete()
        messages.success(request, f"Deleted {deleted[0]} articles from project '{project.name}'")
        return redirect('feeds:project-detail', pk=pk)
    
    article_count = Article.objects.filter(feed__website__project=project).count()
    return render(request, 'feeds/project_clear_articles.html', {
        'project': project,
        'article_count': article_count
    })