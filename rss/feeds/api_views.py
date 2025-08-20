"""
API Views for article analysis and content generation.

Provides RESTful API endpoints for:
- Triggering article analysis
- Viewing analysis results
- Finding similar/duplicate articles
- Generating new content
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta
import json
from celery.result import AsyncResult

from .models import Article, ArticleAnalysis, GeneratedContent, Website
from .article_analyzer import ArticleAnalyzer, ContentGenerator
from .tasks import analyze_article_async, generate_content_async


@login_required
@require_http_methods(["POST"])
def analyze_article(request, article_id):
    """
    Analyze a single article for summary, topics, and duplicates.
    
    POST /api/articles/{article_id}/analyze/
    """
    article = get_object_or_404(Article, id=article_id)
    
    # Check if already analyzed
    if hasattr(article, 'analysis'):
        return JsonResponse({
            'status': 'already_analyzed',
            'analysis': {
                'id': article.analysis.id,
                'summary': article.analysis.ai_summary,
                'topics': article.analysis.topics,
                'entities': article.analysis.entities,
                'sentiment': article.analysis.sentiment,
                'keywords': article.analysis.keywords,
                'analyzed_at': article.analysis.analyzed_at.isoformat()
            }
        })
    
    # Parse request options
    data = json.loads(request.body) if request.body else {}
    find_similar = data.get('find_similar', True)
    async_mode = data.get('async', False)
    
    if async_mode:
        # Queue for async processing
        task = analyze_article_async.delay(article_id, find_similar=find_similar)
        return JsonResponse({
            'status': 'queued',
            'task_id': task.id,
            'message': 'Article queued for analysis'
        })
    
    # Perform synchronous analysis
    analyzer = ArticleAnalyzer()
    
    try:
        # Extract summary and topics
        result = analyzer.extract_summary_and_topics(article)
        
        # Create analysis record
        analysis = ArticleAnalysis.objects.create(
            article=article,
            ai_summary=result.get('summary', ''),
            topics=result.get('topics', []),
            entities=result.get('entities', {}),
            sentiment=result.get('sentiment', 'neutral'),
            keywords=result.get('keywords', [])
        )
        
        # Find similar articles if requested
        similar_articles = []
        if find_similar:
            similar = analyzer.find_similar_articles(article, threshold=0.7)
            for similar_article, score in similar[:5]:
                analysis.similar_articles.add(similar_article)
                similar_articles.append({
                    'id': similar_article.id,
                    'title': similar_article.title,
                    'url': similar_article.url,
                    'similarity': score
                })
            
            # Check for duplicates
            duplicate = analyzer.check_duplicate_content(article)
            if duplicate:
                analysis.duplicate_of = duplicate
                analysis.save()
        
        return JsonResponse({
            'status': 'success',
            'analysis': {
                'id': analysis.id,
                'summary': analysis.ai_summary,
                'topics': analysis.topics,
                'entities': analysis.entities,
                'sentiment': analysis.sentiment,
                'keywords': analysis.keywords,
                'similar_articles': similar_articles,
                'duplicate_of': {
                    'id': duplicate.id,
                    'title': duplicate.title,
                    'url': duplicate.url
                } if duplicate else None,
                'analyzed_at': analysis.analyzed_at.isoformat()
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)


@login_required
@require_http_methods(["GET"])
def get_article_analysis(request, article_id):
    """
    Get analysis results for an article.
    
    GET /api/articles/{article_id}/analysis/
    """
    article = get_object_or_404(Article, id=article_id)
    
    if not hasattr(article, 'analysis'):
        return JsonResponse({
            'status': 'not_analyzed',
            'message': 'Article has not been analyzed yet'
        }, status=404)
    
    analysis = article.analysis
    
    # Get similar articles
    similar_articles = [
        {
            'id': sa.id,
            'title': sa.title,
            'url': sa.url,
            'published_date': sa.published_date.isoformat() if sa.published_date else None
        }
        for sa in analysis.similar_articles.all()[:10]
    ]
    
    return JsonResponse({
        'status': 'success',
        'analysis': {
            'id': analysis.id,
            'summary': analysis.ai_summary,
            'topics': analysis.topics,
            'entities': analysis.entities,
            'sentiment': analysis.sentiment,
            'keywords': analysis.keywords,
            'similar_articles': similar_articles,
            'duplicate_of': {
                'id': analysis.duplicate_of.id,
                'title': analysis.duplicate_of.title,
                'url': analysis.duplicate_of.url
            } if analysis.duplicate_of else None,
            'analyzed_at': analysis.analyzed_at.isoformat(),
            'updated_at': analysis.updated_at.isoformat()
        }
    })


@login_required
@require_http_methods(["POST"])
def find_similar_articles(request):
    """
    Find articles similar to a given article or text.
    
    POST /api/articles/find-similar/
    Body: {
        "article_id": 123,  // OR
        "text": "Article content to compare",
        "threshold": 0.7,
        "limit": 10
    }
    """
    data = json.loads(request.body)
    
    analyzer = ArticleAnalyzer()
    
    if 'article_id' in data:
        article = get_object_or_404(Article, id=data['article_id'])
    elif 'text' in data:
        # Create a temporary article object for comparison
        article = Article(
            title="Temporary",
            content=data['text'],
            summary=""
        )
    else:
        return JsonResponse({
            'status': 'error',
            'message': 'Either article_id or text is required'
        }, status=400)
    
    threshold = data.get('threshold', 0.7)
    limit = data.get('limit', 10)
    
    try:
        similar = analyzer.find_similar_articles(article, threshold=threshold)
        
        results = []
        for similar_article, score in similar[:limit]:
            results.append({
                'id': similar_article.id,
                'title': similar_article.title,
                'url': similar_article.url,
                'published_date': similar_article.published_date.isoformat() if similar_article.published_date else None,
                'similarity': score,
                'website': similar_article.feed.website.name
            })
        
        return JsonResponse({
            'status': 'success',
            'similar_articles': results,
            'count': len(results)
        })
        
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)


@login_required
@require_http_methods(["POST"])
def generate_content(request):
    """
    Generate new content from source articles.
    
    POST /api/content/generate/
    Body: {
        "source_article_ids": [1, 2, 3],
        "style": "news|blog|analysis|summary",
        "target_length": 800,
        "async": false
    }
    """
    data = json.loads(request.body)
    
    source_ids = data.get('source_article_ids', [])
    if not source_ids:
        return JsonResponse({
            'status': 'error',
            'message': 'At least one source article is required'
        }, status=400)
    
    # Get source articles
    source_articles = Article.objects.filter(id__in=source_ids)
    if not source_articles.exists():
        return JsonResponse({
            'status': 'error',
            'message': 'No valid source articles found'
        }, status=404)
    
    style = data.get('style', 'news')
    target_length = data.get('target_length', 800)
    async_mode = data.get('async', False)
    
    if async_mode:
        # Queue for async processing
        task = generate_content_async.delay(
            source_ids, 
            style=style, 
            target_length=target_length
        )
        return JsonResponse({
            'status': 'queued',
            'task_id': task.id,
            'message': 'Content generation queued'
        })
    
    # Generate content synchronously
    generator = ContentGenerator()
    
    try:
        result = generator.generate_article(
            source_articles=list(source_articles),
            style=style,
            target_length=target_length
        )
        
        # Save generated content
        generated = GeneratedContent.objects.create(
            title=result.get('title', 'Generated Article'),
            subtitle=result.get('subtitle', ''),
            content=result.get('content', ''),
            summary=result.get('summary', ''),
            style=style,
            topics=result.get('topics', []),
            media_items=result.get('media_items', []),
            generation_params={
                'style': style,
                'target_length': target_length,
                'source_count': source_articles.count()
            }
        )
        
        # Add source articles
        generated.source_articles.set(source_articles)
        
        return JsonResponse({
            'status': 'success',
            'content': {
                'id': generated.id,
                'title': generated.title,
                'subtitle': generated.subtitle,
                'content': generated.content,
                'summary': generated.summary,
                'style': generated.style,
                'topics': generated.topics,
                'media_items': generated.media_items,
                'source_articles': [
                    {
                        'id': a.id,
                        'title': a.title,
                        'url': a.url
                    }
                    for a in source_articles
                ],
                'generated_at': generated.generated_at.isoformat()
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)


@login_required
@require_http_methods(["GET"])
def get_generated_content(request, content_id):
    """
    Get a generated content piece by ID.
    
    GET /api/content/{content_id}/
    """
    content = get_object_or_404(GeneratedContent, id=content_id)
    
    return JsonResponse({
        'status': 'success',
        'content': {
            'id': content.id,
            'title': content.title,
            'subtitle': content.subtitle,
            'content': content.content,
            'summary': content.summary,
            'style': content.style,
            'status': content.status,
            'topics': content.topics,
            'media_items': content.media_items,
            'source_attribution': content.get_source_attribution(),
            'generated_at': content.generated_at.isoformat(),
            'updated_at': content.updated_at.isoformat(),
            'published_at': content.published_at.isoformat() if content.published_at else None,
            'published_url': content.published_url
        }
    })


@login_required
@require_http_methods(["GET"])
def list_generated_content(request):
    """
    List generated content with filters.
    
    GET /api/content/?style=news&status=draft&days=7
    """
    queryset = GeneratedContent.objects.all()
    
    # Apply filters
    style = request.GET.get('style')
    if style:
        queryset = queryset.filter(style=style)
    
    status = request.GET.get('status')
    if status:
        queryset = queryset.filter(status=status)
    
    days = request.GET.get('days')
    if days:
        since_date = timezone.now() - timedelta(days=int(days))
        queryset = queryset.filter(generated_at__gte=since_date)
    
    # Pagination
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 20))
    offset = (page - 1) * per_page
    
    content_list = queryset[offset:offset + per_page]
    
    results = []
    for content in content_list:
        results.append({
            'id': content.id,
            'title': content.title,
            'style': content.style,
            'status': content.status,
            'topics': content.topics,
            'source_count': content.source_articles.count(),
            'generated_at': content.generated_at.isoformat()
        })
    
    return JsonResponse({
        'status': 'success',
        'content': results,
        'total': queryset.count(),
        'page': page,
        'per_page': per_page
    })


@login_required
@require_http_methods(["POST"])
def batch_analyze_articles(request):
    """
    Analyze multiple articles in batch.
    
    POST /api/articles/batch-analyze/
    Body: {
        "website_id": 1,  // Optional
        "days": 1,  // Optional, default 1
        "limit": 10,  // Optional
        "find_duplicates": true
    }
    """
    data = json.loads(request.body) if request.body else {}
    
    queryset = Article.objects.all()
    
    # Filter by website
    website_id = data.get('website_id')
    if website_id:
        website = get_object_or_404(Website, id=website_id)
        queryset = queryset.filter(feed__website=website)
    
    # Filter by date
    days = data.get('days', 1)
    since_date = timezone.now() - timedelta(days=days)
    queryset = queryset.filter(fetched_at__gte=since_date)
    
    # Exclude already analyzed
    queryset = queryset.filter(analysis__isnull=True)
    
    # Apply limit
    limit = data.get('limit', 10)
    queryset = queryset[:limit]
    
    # Queue for processing
    article_ids = list(queryset.values_list('id', flat=True))
    
    if not article_ids:
        return JsonResponse({
            'status': 'success',
            'message': 'No articles to analyze',
            'count': 0
        })
    
    # Queue each article for analysis
    for article_id in article_ids:
        analyze_article_async.delay(
            article_id, 
            find_similar=data.get('find_duplicates', True)
        )
    
    return JsonResponse({
        'status': 'success',
        'message': f'Queued {len(article_ids)} articles for analysis',
        'count': len(article_ids),
        'article_ids': article_ids
    })


@login_required
@require_http_methods(["GET"])
def check_task_progress(request, task_id):
    """
    Check the progress of a Celery task.
    
    GET /api/tasks/{task_id}/progress/
    """
    try:
        task = AsyncResult(task_id)
        
        if task.state == 'PENDING':
            response = {
                'state': task.state,
                'progress': 0,
                'status': 'Task is waiting to start...'
            }
        elif task.state == 'PROGRESS':
            response = {
                'state': task.state,
                'progress': task.info.get('current', 0),
                'total': task.info.get('total', 100),
                'status': task.info.get('status', 'Processing...')
            }
        elif task.state == 'SUCCESS':
            response = {
                'state': task.state,
                'progress': 100,
                'status': 'Task completed successfully',
                'result': str(task.result)
            }
        elif task.state == 'FAILURE':
            response = {
                'state': task.state,
                'progress': 0,
                'status': 'Task failed',
                'error': str(task.info)
            }
        else:
            response = {
                'state': task.state,
                'progress': 50,
                'status': f'Task is {task.state}'
            }
        
        return JsonResponse(response)
        
    except Exception as e:
        return JsonResponse({
            'state': 'ERROR',
            'status': 'Error checking task status',
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["POST"])
def analyze_article_with_progress(request, article_id):
    """
    Analyze article and return task ID for progress tracking.
    
    POST /api/articles/{article_id}/analyze-async/
    """
    article = get_object_or_404(Article, id=article_id)
    
    # Check if already analyzed
    if hasattr(article, 'analysis'):
        return JsonResponse({
            'status': 'already_analyzed',
            'message': 'Article has already been analyzed',
            'analysis_id': article.analysis.id
        })
    
    # Queue for analysis
    task = analyze_article_async.delay(article_id, find_similar=True)
    
    # Store task ID in session for tracking
    request.session[f'analysis_task_{article_id}'] = task.id
    
    return JsonResponse({
        'status': 'started',
        'task_id': task.id,
        'article_id': article_id,
        'message': 'Analysis started'
    })


@login_required
@require_http_methods(["POST"])
def generate_content_with_progress(request):
    """
    Generate content and return task ID for progress tracking.
    
    POST /api/content/generate-async/
    """
    data = json.loads(request.body)
    
    source_ids = data.get('source_article_ids', [])
    if not source_ids:
        return JsonResponse({
            'status': 'error',
            'message': 'At least one source article is required'
        }, status=400)
    
    style = data.get('style', 'news')
    target_length = data.get('target_length', 800)
    
    # Queue for generation
    task = generate_content_async.delay(
        source_ids,
        style=style,
        target_length=target_length
    )
    
    # Store task ID in session
    request.session['generation_task'] = task.id
    
    return JsonResponse({
        'status': 'started',
        'task_id': task.id,
        'message': 'Content generation started'
    })


@login_required
@require_http_methods(["POST"])
def manual_fetch_website(request, website_id):
    """
    Manually trigger content fetching for a website.
    
    POST /api/websites/{website_id}/fetch/
    """
    from .tasks import fetch_all_website_content
    
    website = get_object_or_404(Website, id=website_id)
    
    # Check if website is active
    if not website.active:
        return JsonResponse({
            'status': 'error',
            'message': 'Website is not active'
        }, status=400)
    
    try:
        # Queue the fetch task
        task = fetch_all_website_content.delay(website.id)
        
        # Update last fetch time
        website.last_auto_fetch = timezone.now()
        website.save()
        
        return JsonResponse({
            'status': 'success',
            'message': f'Fetching content from {website.name}',
            'task_id': task.id,
            'website': {
                'id': website.id,
                'name': website.name,
                'feed_count': website.feeds.filter(active=True).count()
            }
        })
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)