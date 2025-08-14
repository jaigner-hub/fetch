from django.urls import path
from . import views

app_name = 'feeds'

urlpatterns = [
    # Home/Dashboard
    path('', views.home_view, name='home'),
    
    # Website URLs
    path('websites/', views.WebsiteListView.as_view(), name='website-list'),
    path('websites/add/', views.WebsiteCreateView.as_view(), name='website-add'),
    path('websites/<int:pk>/', views.WebsiteDetailView.as_view(), name='website-detail'),
    path('websites/<int:pk>/edit/', views.WebsiteUpdateView.as_view(), name='website-edit'),
    path('websites/<int:pk>/delete/', views.WebsiteDeleteView.as_view(), name='website-delete'),
    path('websites/<int:pk>/discover/', views.discover_feeds, name='website-discover-feeds'),
    path('websites/<int:pk>/fetch-all/', views.fetch_all_content, name='website-fetch-all'),
    
    # Feed URLs
    path('feeds/', views.FeedListView.as_view(), name='feed-list'),
    path('feeds/<int:pk>/', views.FeedDetailView.as_view(), name='feed-detail'),
    path('feeds/<int:pk>/edit/', views.FeedUpdateView.as_view(), name='feed-edit'),
    path('feeds/<int:pk>/refresh/', views.refresh_feed, name='feed-refresh'),
    
    # Article URLs
    path('articles/', views.ArticleListView.as_view(), name='article-list'),
    path('articles/<int:pk>/', views.ArticleDetailView.as_view(), name='article-detail'),
    
    # API endpoints
    path('api/stats/', views.feed_stats_api, name='api-stats'),
]