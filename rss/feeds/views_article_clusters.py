from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

@login_required
def article_clusters(request):
    """
    Display AI-powered article clusters.
    Shows clusters of related articles identified by AI as covering the same event/story.
    """
    # Get parameters
    hours = int(request.GET.get('hours', 48))
    min_cluster_size = int(request.GET.get('min_size', 2))
    page = request.GET.get('page', 1)
    
    # Get time window
    since = timezone.now() - timedelta(hours=hours)
    
    # Get AI-identified clusters from database
    ai_clusters = ArticleCluster.objects.filter(
        is_active=True,
        latest_article__gte=since
    ).prefetch_related(
        'articles',
        'articles__feed__website'
    ).order_by('-confidence_score', '-source_count', '-latest_article')
    
    # Convert to view format
    clusters = []
    for cluster in ai_clusters:
        if cluster.articles.count() >= min_cluster_size:
            cluster_articles = list(cluster.articles.all().order_by('-published_date'))
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
                'time_span': (cluster.latest_article - cluster.earliest_article).total_seconds() / 3600 if cluster.earliest_article and cluster.latest_article else 0,
                'is_ai_cluster': True
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
    
    # Paginate the clusters
    paginator = Paginator(clusters, 10)  # Show 10 clusters per page
    
    try:
        clusters_page = paginator.page(page)
    except PageNotAnInteger:
        # If page is not an integer, deliver first page
        clusters_page = paginator.page(1)
    except EmptyPage:
        # If page is out of range, deliver last page of results
        clusters_page = paginator.page(paginator.num_pages)
    
    context = {
        'clusters': clusters_page,
        'total_clusters': len(clusters),
        'hours': hours,
        'min_cluster_size': min_cluster_size,
        'unanalyzed_count': unanalyzed_count,
        'total_articles': total_articles,
        'time_options': [24, 48, 72, 168],
        'size_options': [2, 3, 4, 5, 10],
        'paginator': paginator,
        'page_obj': clusters_page
    }
    
    return render(request, 'feeds/article_clusters.html', context)