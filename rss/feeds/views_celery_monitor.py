"""
Celery monitoring views for task status and scheduling.
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum, Count, Avg, Q
from datetime import timedelta, datetime
from celery import current_app
from celery.schedules import crontab
import pytz
from .models import TaskExecution, FetchLog, Article, Feed

def get_next_schedule_time(schedule, last_run=None):
    """Calculate next run time for a Celery beat schedule."""
    # Always use timezone-aware datetime
    now = timezone.now()
    if not timezone.is_aware(now):
        now = timezone.make_aware(now)
    
    # Convert schedule to string to parse it
    schedule_str = str(schedule)
    
    # Check for crontab patterns like "*/5" or "*/30"
    if '*/5 *' in schedule_str:
        # Every 5 minutes
        next_minute = ((now.minute // 5) + 1) * 5
        if next_minute >= 60:
            next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_run = now.replace(minute=next_minute, second=0, microsecond=0)
        # Ensure it's timezone-aware
        if not timezone.is_aware(next_run):
            next_run = timezone.make_aware(next_run)
    
    elif '*/30 *' in schedule_str:
        # Every 30 minutes
        if now.minute < 30:
            next_run = now.replace(minute=30, second=0, microsecond=0)
        else:
            next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        # Ensure it's timezone-aware
        if not timezone.is_aware(next_run):
            next_run = timezone.make_aware(next_run)
    
    elif 'crontab' in schedule_str:
        # Try to parse crontab from string
        if isinstance(schedule, crontab):
            # For crontab with specific hour/minute
            if hasattr(schedule, 'hour') and schedule.hour != '*':
                hour_val = next(iter(schedule.hour)) if isinstance(schedule.hour, set) else schedule.hour
                minute_val = next(iter(schedule.minute)) if isinstance(schedule.minute, set) else schedule.minute
                
                target_time = now.replace(hour=hour_val, minute=minute_val, second=0, microsecond=0)
                
                # Check for weekly schedule
                if hasattr(schedule, 'day_of_week') and schedule.day_of_week != '*':
                    dow = next(iter(schedule.day_of_week)) if isinstance(schedule.day_of_week, set) else schedule.day_of_week
                    # Celery uses 0=Sunday, Python uses 0=Monday
                    target_weekday = 6 if dow == 0 else dow - 1
                    current_weekday = now.weekday()
                    
                    days_ahead = (target_weekday - current_weekday) % 7
                    if days_ahead == 0 and now >= target_time:
                        days_ahead = 7
                    
                    next_run = target_time + timedelta(days=days_ahead)
                else:
                    # Daily schedule
                    if now >= target_time:
                        next_run = target_time + timedelta(days=1)
                    else:
                        next_run = target_time
            else:
                # Default for other crontab patterns
                next_run = now + timedelta(minutes=5)
        else:
            next_run = now + timedelta(minutes=5)
    else:
        # Default fallback
        next_run = now + timedelta(minutes=5)
    
    return next_run


@login_required
def celery_monitor(request):
    """Main Celery monitoring dashboard."""
    context = {}
    
    # Get Celery beat schedule from settings
    from django.conf import settings
    beat_schedule = getattr(settings, 'CELERY_BEAT_SCHEDULE', {})
    
    # Process scheduled tasks
    scheduled_tasks = []
    for task_name, task_config in beat_schedule.items():
        task_info = {
            'name': task_name,
            'task': task_config['task'],
            'schedule': str(task_config['schedule']),
            'kwargs': task_config.get('kwargs', {}),
        }
        
        # Get last execution
        last_exec = TaskExecution.objects.filter(
            task_name=task_config['task']
        ).first()
        
        if last_exec:
            task_info['last_run'] = last_exec.started_at
            task_info['last_status'] = last_exec.status
            task_info['last_result'] = last_exec.result
        else:
            task_info['last_run'] = None
            task_info['last_status'] = 'Never run'
            task_info['last_result'] = ''
        
        # Calculate next run time
        task_info['next_run'] = get_next_schedule_time(
            task_config['schedule'],
            task_info['last_run']
        )
        
        # Calculate time until next run
        if task_info['next_run']:
            # Get fresh current time for accurate comparison
            current_time = timezone.now()
            next_run = task_info['next_run']
            
            # Ensure both times are timezone-aware for comparison
            if not timezone.is_aware(next_run):
                next_run = timezone.make_aware(next_run)
            
            
            time_until = next_run - current_time
            if time_until.total_seconds() > 0:
                # Format the time more nicely
                total_seconds = int(time_until.total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                
                if hours > 0:
                    task_info['time_until'] = f"{hours}h {minutes}m"
                elif minutes > 0:
                    task_info['time_until'] = f"{minutes}m {seconds}s"
                else:
                    task_info['time_until'] = f"{seconds}s"
            else:
                task_info['time_until'] = 'Overdue'
        else:
            task_info['time_until'] = 'Unknown'
        
        scheduled_tasks.append(task_info)
    
    context['scheduled_tasks'] = scheduled_tasks
    
    # Get recent task executions
    recent_tasks = TaskExecution.objects.all()[:20]
    context['recent_tasks'] = recent_tasks
    
    # Get statistics for last 24 hours
    now = timezone.now()
    since_24h = now - timedelta(hours=24)
    
    # Task statistics
    task_stats = TaskExecution.objects.filter(
        started_at__gte=since_24h
    ).aggregate(
        total=Count('id'),
        success=Count('id', filter=Q(status='SUCCESS')),
        failure=Count('id', filter=Q(status='FAILURE')),
        avg_runtime=Avg('runtime_seconds'),
        total_articles=Sum('articles_fetched'),
        total_feeds=Sum('feeds_processed')
    )
    context['task_stats'] = task_stats
    
    # Article fetch statistics from FetchLog
    fetch_stats = FetchLog.objects.filter(
        started_at__gte=since_24h
    ).aggregate(
        total_fetches=Count('id'),
        successful=Count('id', filter=Q(success=True)),
        failed=Count('id', filter=Q(success=False)),
        articles_fetched=Sum('new_articles'),
        articles_updated=Sum('updated_articles')
    )
    context['fetch_stats'] = fetch_stats
    
    # Get all Celery workers (not just ones with active tasks)
    try:
        inspector = current_app.control.inspect()
        # Get all registered workers
        stats = inspector.stats()
        active_tasks = inspector.active()
        
        if stats:
            context['workers'] = []
            total_processes = 0
            for worker_name, worker_stats in stats.items():
                # Get active tasks for this worker
                worker_active_tasks = active_tasks.get(worker_name, []) if active_tasks else []
                # Get pool info
                pool_info = worker_stats.get('pool', {})
                concurrency = pool_info.get('max-concurrency', 1)
                total_processes += concurrency
                
                context['workers'].append({
                    'name': worker_name,
                    'concurrency': concurrency,
                    'active_tasks': len(worker_active_tasks),
                    'tasks': worker_active_tasks[:5]  # Show first 5 tasks
                })
            context['total_processes'] = total_processes
        else:
            context['workers'] = []
            context['total_processes'] = 0
    except Exception as e:
        context['workers'] = []
        context['worker_error'] = str(e)
        context['total_processes'] = 0
    
    # Get queue sizes
    try:
        inspector = current_app.control.inspect()
        reserved = inspector.reserved()
        if reserved:
            total_reserved = sum(len(tasks) for tasks in reserved.values())
            context['queue_size'] = total_reserved
        else:
            context['queue_size'] = 0
    except:
        context['queue_size'] = 0
    
    # Recent articles fetched
    recent_articles = Article.objects.filter(
        fetched_at__gte=since_24h
    ).select_related('feed__website').order_by('-fetched_at')[:10]
    context['recent_articles'] = recent_articles
    
    # Feeds with recent activity
    active_feeds = Feed.objects.filter(
        last_successful_fetch__gte=since_24h
    ).select_related('website').order_by('-last_successful_fetch')[:10]
    context['active_feeds'] = active_feeds
    
    return render(request, 'feeds/celery_monitor.html', context)


@login_required
def celery_status_api(request):
    """API endpoint for real-time Celery status updates."""
    data = {}
    
    # Get current time
    now = timezone.now()
    since_5m = now - timedelta(minutes=5)
    
    # Recent task count
    recent_tasks = TaskExecution.objects.filter(
        started_at__gte=since_5m
    ).values('status').annotate(count=Count('id'))
    
    data['recent_tasks'] = {
        task['status']: task['count'] 
        for task in recent_tasks
    }
    
    # Articles fetched in last 5 minutes
    recent_articles = Article.objects.filter(
        fetched_at__gte=since_5m
    ).count()
    data['recent_articles'] = recent_articles
    
    # Active workers and total processes
    try:
        inspector = current_app.control.inspect()
        stats = inspector.stats()
        active = inspector.active()
        
        if stats:
            total_processes = 0
            for worker_stats in stats.values():
                pool_info = worker_stats.get('pool', {})
                concurrency = pool_info.get('max-concurrency', 1)
                total_processes += concurrency
            data['active_workers'] = len(stats)
            data['total_processes'] = total_processes
        else:
            data['active_workers'] = 0
            data['total_processes'] = 0
            
        if active:
            data['active_tasks'] = sum(len(tasks) for tasks in active.values())
        else:
            data['active_tasks'] = 0
    except:
        data['active_workers'] = 0
        data['active_tasks'] = 0
        data['total_processes'] = 0
    
    # Next scheduled task
    from django.conf import settings
    beat_schedule = getattr(settings, 'CELERY_BEAT_SCHEDULE', {})
    
    next_tasks = []
    for task_name, task_config in beat_schedule.items():
        next_run = get_next_schedule_time(task_config['schedule'])
        next_tasks.append({
            'name': task_name,
            'next_run': next_run.isoformat(),
            'seconds_until': int((next_run - now).total_seconds())
        })
    
    # Sort by next run time
    next_tasks.sort(key=lambda x: x['seconds_until'])
    data['next_task'] = next_tasks[0] if next_tasks else None
    
    return JsonResponse(data)