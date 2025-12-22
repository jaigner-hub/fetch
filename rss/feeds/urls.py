from django.urls import path
from . import views
from . import api_views
from . import views_celery_monitor
from . import views_projects

app_name = 'feeds'

urlpatterns = [
    # Home/Dashboard
    path('', views.home_view, name='home'),
    
    # Project Management
    path('projects/', views_projects.ProjectListView.as_view(), name='project-list'),
    path('projects/create/', views_projects.ProjectCreateView.as_view(), name='project-create'),
    path('projects/<int:pk>/', views_projects.project_detail, name='project-detail'),
    path('projects/<int:pk>/edit/', views_projects.ProjectUpdateView.as_view(), name='project-edit'),
    path('projects/<int:pk>/delete/', views_projects.ProjectDeleteView.as_view(), name='project-delete'),
    path('projects/<int:pk>/duplicate/', views_projects.duplicate_project, name='project-duplicate'),
    path('projects/<int:pk>/clear-articles/', views_projects.clear_project_articles, name='project-clear-articles'),
    
    # Project switching
    path('switch-project/<int:project_id>/', views.switch_project, name='switch-project'),
    
    # Website URLs
    path('websites/', views.WebsiteListView.as_view(), name='website-list'),
    path('websites/add/', views.WebsiteCreateView.as_view(), name='website-add'),
    path('websites/<int:pk>/', views.WebsiteDetailView.as_view(), name='website-detail'),
    path('websites/<int:pk>/edit/', views.WebsiteUpdateView.as_view(), name='website-edit'),
    path('websites/<int:pk>/delete/', views.WebsiteDeleteView.as_view(), name='website-delete'),
    path('websites/<int:pk>/discover/', views.discover_feeds, name='website-discover-feeds'),
    path('websites/<int:pk>/fetch-all/', views.fetch_all_content, name='website-fetch-all'),
    path('websites/<int:website_id>/feeds/', views.manage_feeds, name='manage-feeds'),
    
    # Feed URLs
    path('feeds/', views.FeedListView.as_view(), name='feed-list'),
    path('feeds/<int:pk>/', views.FeedDetailView.as_view(), name='feed-detail'),
    path('feeds/<int:pk>/edit/', views.FeedUpdateView.as_view(), name='feed-edit'),
    path('feeds/<int:pk>/refresh/', views.refresh_feed, name='feed-refresh'),
    
    # Article URLs
    path('articles/', views.ArticleListView.as_view(), name='article-list'),
    path('articles/<int:pk>/', views.ArticleDetailView.as_view(), name='article-detail'),
    
    # Analysis URLs
    path('analysis/', views.AnalysisDashboardView.as_view(), name='analysis-dashboard'),
    path('analysis/article/<int:pk>/', views.ArticleAnalysisDetailView.as_view(), name='article-analysis-detail'),
    path('analysis/article/<int:pk>/analyze/', views.analyze_article_view, name='analyze-article'),
    path('analysis/batch/', views.batch_analyze_view, name='batch-analyze'),
    
    # Topic and Category filtering
    path('articles/topic/<path:topic>/', views.ArticlesByTopicView.as_view(), name='articles-by-topic'),
    path('articles/category/<path:category>/', views.ArticlesByCategoryView.as_view(), name='articles-by-category'),
    
    # Entity filtering
    path('articles/person/<path:person>/', views.ArticlesByPersonView.as_view(), name='articles-by-person'),
    path('articles/organization/<path:organization>/', views.ArticlesByOrganizationView.as_view(), name='articles-by-organization'),
    path('articles/location/<path:location>/', views.ArticlesByLocationView.as_view(), name='articles-by-location'),
    
    # Keyword filtering
    path('articles/keyword/<path:keyword>/', views.ArticlesByKeywordView.as_view(), name='articles-by-keyword'),
    
    # Article Clusters
    path('clusters/', views.article_clusters, name='article-clusters'),
    path('clusters/generate/', views.generate_clusters_view, name='generate-clusters'),
    
    # Celery Monitor
    path('monitor/', views_celery_monitor.celery_monitor, name='celery-monitor'),
    path('api/celery-status/', views_celery_monitor.celery_status_api, name='celery-status-api'),
    
    # Generated Content URLs
    path('content/', views.GeneratedContentListView.as_view(), name='generated-content-list'),
    path('content/generate/', views.GenerateContentView.as_view(), name='generate-content'),
    path('content/<int:pk>/', views.GeneratedContentDetailView.as_view(), name='generated-content-detail'),
    path('content/<int:pk>/status/', views.update_content_status, name='update-content-status'),
    
    # API endpoints
    path('api/stats/', views.feed_stats_api, name='api-stats'),
    path('api/websites/<int:pk>/fetch-progress/', views.fetch_progress_api, name='api-fetch-progress'),
    path('api/websites/<int:pk>/discover-progress/', views.discover_progress_api, name='api-discover-progress'),
    path('api/websites/<int:website_id>/fetch/', api_views.manual_fetch_website, name='api-manual-fetch'),
    
    # Article Analysis API endpoints
    path('api/articles/<int:article_id>/analyze/', api_views.analyze_article, name='api-analyze-article'),
    path('api/articles/<int:article_id>/analyze-async/', api_views.analyze_article_with_progress, name='api-analyze-article-async'),
    path('api/articles/<int:article_id>/analysis/', api_views.get_article_analysis, name='api-get-analysis'),
    path('api/articles/find-similar/', api_views.find_similar_articles, name='api-find-similar'),
    path('api/articles/batch-analyze/', api_views.batch_analyze_articles, name='api-batch-analyze'),
    
    # Content Generation API endpoints
    path('api/content/generate/', api_views.generate_content, name='api-generate-content'),
    path('api/content/generate-async/', api_views.generate_content_with_progress, name='api-generate-content-async'),
    path('api/content/<int:content_id>/', api_views.get_generated_content, name='api-get-generated-content'),
    path('api/content/', api_views.list_generated_content, name='api-list-generated-content'),
    
    # Task Progress API
    path('api/tasks/<str:task_id>/progress/', api_views.check_task_progress, name='api-task-progress'),
]