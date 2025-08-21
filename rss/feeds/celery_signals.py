"""
Celery signal handlers to track task execution.
"""
from celery import signals
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


@signals.task_prerun.connect
def task_prerun_handler(sender=None, task_id=None, task=None, args=None, kwargs=None, **extras):
    """Record when a task starts."""
    try:
        from feeds.models import TaskExecution
        TaskExecution.objects.update_or_create(
            task_id=task_id,
            defaults={
                'task_name': task.name,
                'status': 'STARTED',
                'started_at': timezone.now(),
            }
        )
    except Exception as e:
        logger.error(f"Error recording task start: {e}")


@signals.task_postrun.connect
def task_postrun_handler(sender=None, task_id=None, task=None, args=None, kwargs=None, 
                         retval=None, state=None, **extras):
    """Record when a task completes."""
    try:
        from feeds.models import TaskExecution
        execution, created = TaskExecution.objects.get_or_create(
            task_id=task_id,
            defaults={'task_name': task.name}
        )
        
        execution.completed_at = timezone.now()
        execution.status = 'SUCCESS' if state == 'SUCCESS' else state
        
        # Calculate runtime
        if execution.started_at:
            runtime = (execution.completed_at - execution.started_at).total_seconds()
            execution.runtime_seconds = runtime
        
        # Store result (truncate if too long)
        if retval:
            result_str = str(retval)[:500]
            execution.result = result_str
            
            # Try to extract article counts from result
            if 'article' in result_str.lower():
                import re
                # Look for patterns like "10 articles" or "Fetched 10 new"
                match = re.search(r'(\d+)\s*(new\s+)?article', result_str, re.IGNORECASE)
                if match:
                    execution.articles_fetched = int(match.group(1))
            
            # Extract feed counts
            if 'feed' in result_str.lower():
                import re
                match = re.search(r'(\d+)\s*feed', result_str, re.IGNORECASE)
                if match:
                    execution.feeds_processed = int(match.group(1))
        
        execution.save()
        
    except Exception as e:
        logger.error(f"Error recording task completion: {e}")


@signals.task_failure.connect
def task_failure_handler(sender=None, task_id=None, exception=None, args=None, kwargs=None,
                        traceback=None, einfo=None, **extras):
    """Record when a task fails."""
    try:
        from feeds.models import TaskExecution
        execution, created = TaskExecution.objects.get_or_create(
            task_id=task_id,
            defaults={'task_name': sender.name if sender else 'unknown'}
        )
        
        execution.completed_at = timezone.now()
        execution.status = 'FAILURE'
        execution.error = str(exception)[:500] if exception else 'Unknown error'
        
        # Calculate runtime
        if execution.started_at:
            runtime = (execution.completed_at - execution.started_at).total_seconds()
            execution.runtime_seconds = runtime
        
        execution.save()
        
    except Exception as e:
        logger.error(f"Error recording task failure: {e}")


@signals.task_retry.connect
def task_retry_handler(sender=None, task_id=None, reason=None, einfo=None, **extras):
    """Record when a task is retried."""
    try:
        from feeds.models import TaskExecution
        execution, created = TaskExecution.objects.get_or_create(
            task_id=task_id,
            defaults={'task_name': sender.name if sender else 'unknown'}
        )
        
        execution.status = 'RETRY'
        execution.error = str(reason)[:500] if reason else 'Retry requested'
        execution.save()
        
    except Exception as e:
        logger.error(f"Error recording task retry: {e}")