"""
Celery configuration for RSS project.
"""
import os
from celery import Celery
from celery.schedules import crontab
from kombu import Queue

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rss.settings')

app = Celery('rss')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Configure task routes and queues
app.conf.task_routes = {
    # UI-triggered tasks go to high-priority queue
    'feeds.tasks.analyze_article_async': {'queue': 'high_priority'},
    'feeds.tasks.generate_content_async': {'queue': 'high_priority'},
    
    # Background/scheduled tasks go to default queue
    'feeds.tasks.discover_feeds_for_website': {'queue': 'celery'},
    'feeds.tasks.fetch_feed_content': {'queue': 'celery'},
    'feeds.tasks.fetch_article_full_content': {'queue': 'celery'},
    'feeds.tasks.check_all_feeds': {'queue': 'celery'},
    'feeds.tasks.discover_new_feeds': {'queue': 'celery'},
    'feeds.tasks.fetch_all_website_content': {'queue': 'celery'},
    'feeds.tasks.fetch_selective_sitemap_content': {'queue': 'celery'},
    'feeds.tasks.check_scheduled_fetches': {'queue': 'celery'},
    'feeds.tasks.fetch_single_website_on_schedule': {'queue': 'celery'},
    'feeds.tasks.cleanup_old_logs': {'queue': 'celery'},
    'feeds.tasks.batch_analyze_recent_articles': {'queue': 'celery'},
}

# Define queues
app.conf.task_queues = (
    Queue('celery', routing_key='celery'),
    Queue('high_priority', routing_key='high_priority'),
)

# Set default queue
app.conf.task_default_queue = 'celery'
app.conf.task_default_exchange_type = 'direct'
app.conf.task_default_routing_key = 'celery'

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Import signal handlers to track task execution
try:
    from feeds import celery_signals
except ImportError:
    pass

# Configure Celery Beat schedule for periodic tasks
app.conf.beat_schedule = {
    'analyze-recent-articles-every-30-minutes': {
        'task': 'feeds.tasks.batch_analyze_recent_articles',
        'schedule': crontab(minute='*/30'),  # Every 30 minutes
        'kwargs': {
            'hours': 24,  # Look at articles from last 24 hours
            'limit': 100  # Analyze up to 100 articles per batch
        }
    },
    'check-scheduled-fetches-every-5-minutes': {
        'task': 'feeds.tasks.check_scheduled_fetches',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
    'cleanup-old-logs-daily': {
        'task': 'feeds.tasks.cleanup_old_logs',
        'schedule': crontab(hour=2, minute=0),  # Daily at 2 AM
    },
    'generate-clusters-every-6-hours': {
        'task': 'feeds.tasks.generate_article_clusters',
        'schedule': crontab(minute=0, hour='*/6'),  # Every 6 hours
        'kwargs': {
            'hours': 48,
            'min_cluster_size': 2,
            'force_regenerate': False
        }
    }
}

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')