"""
Management command to analyze articles and generate new content.

This command provides a complete pipeline for:
1. Analyzing articles for summaries and topics
2. Finding similar/duplicate articles
3. Generating new content from multiple sources
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from datetime import timedelta
from feeds.models import Article, ArticleAnalysis, GeneratedContent
from feeds.article_analyzer import ArticleAnalyzer, ContentGenerator
import json


class Command(BaseCommand):
    help = 'Analyze articles for summaries, topics, and generate new content'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--article-id',
            type=int,
            help='Analyze a specific article by ID'
        )
        parser.add_argument(
            '--website',
            type=str,
            help='Analyze all articles from a specific website'
        )
        parser.add_argument(
            '--days',
            type=int,
            default=1,
            help='Analyze articles from the last N days (default: 1)'
        )
        parser.add_argument(
            '--generate',
            action='store_true',
            help='Generate new content from analyzed articles'
        )
        parser.add_argument(
            '--topic',
            type=str,
            help='Filter articles by topic for content generation'
        )
        parser.add_argument(
            '--style',
            type=str,
            choices=['news', 'blog', 'analysis', 'summary'],
            default='news',
            help='Style for generated content (default: news)'
        )
        parser.add_argument(
            '--find-duplicates',
            action='store_true',
            help='Find and mark duplicate articles'
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Limit number of articles to process'
        )
    
    def handle(self, *args, **options):
        analyzer = ArticleAnalyzer()
        generator = ContentGenerator()
        
        # Determine which articles to analyze
        articles = self.get_articles_to_analyze(options)
        
        if not articles:
            self.stdout.write(self.style.WARNING('No articles found to analyze'))
            return
        
        self.stdout.write(f'Found {articles.count()} articles to process')
        
        # Process articles
        analyzed_articles = []
        duplicate_count = 0
        
        for article in articles:
            self.stdout.write(f'\nAnalyzing: {article.title[:80]}...')
            
            # Check if already analyzed
            if hasattr(article, 'analysis'):
                self.stdout.write(self.style.WARNING('  Already analyzed, skipping...'))
                analyzed_articles.append(article)
                continue
            
            # Perform analysis
            try:
                # Extract summary and topics
                analysis_result = analyzer.extract_summary_and_topics(article)
                
                # Create or update analysis record
                analysis, created = ArticleAnalysis.objects.get_or_create(
                    article=article,
                    defaults={
                        'ai_summary': analysis_result.get('summary', ''),
                        'topics': analysis_result.get('topics', []),
                        'entities': analysis_result.get('entities', {}),
                        'sentiment': analysis_result.get('sentiment', 'neutral'),
                        'keywords': analysis_result.get('keywords', [])
                    }
                )
                
                if not created:
                    # Update existing analysis
                    analysis.ai_summary = analysis_result.get('summary', '')
                    analysis.topics = analysis_result.get('topics', [])
                    analysis.entities = analysis_result.get('entities', {})
                    analysis.sentiment = analysis_result.get('sentiment', 'neutral')
                    analysis.keywords = analysis_result.get('keywords', [])
                    analysis.save()
                
                self.stdout.write(self.style.SUCCESS(f'  ✓ Analysis complete'))
                self.stdout.write(f'    Topics: {", ".join(analysis.topics[:3])}')
                self.stdout.write(f'    Sentiment: {analysis.sentiment}')
                
                # Find similar articles
                if options['find_duplicates']:
                    similar = analyzer.find_similar_articles(article, threshold=0.7)
                    if similar:
                        self.stdout.write(f'  Found {len(similar)} similar articles:')
                        
                        # Add similar articles
                        for similar_article, score in similar[:5]:
                            analysis.similar_articles.add(similar_article)
                            self.stdout.write(f'    - {similar_article.title[:60]} (similarity: {score:.2f})')
                        
                        # Check for duplicates (>0.95 similarity)
                        duplicate = analyzer.check_duplicate_content(article)
                        if duplicate:
                            analysis.duplicate_of = duplicate
                            analysis.save()
                            duplicate_count += 1
                            self.stdout.write(self.style.WARNING(f'  ⚠ Duplicate of: {duplicate.title[:60]}'))
                
                analyzed_articles.append(article)
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Error: {str(e)}'))
                continue
        
        # Summary
        self.stdout.write(self.style.SUCCESS(f'\n\nAnalysis Complete:'))
        self.stdout.write(f'  - Articles analyzed: {len(analyzed_articles)}')
        if options['find_duplicates']:
            self.stdout.write(f'  - Duplicates found: {duplicate_count}')
        
        # Generate new content if requested
        if options['generate'] and analyzed_articles:
            self.generate_content(analyzed_articles, generator, options)
    
    def get_articles_to_analyze(self, options):
        """Get the articles to analyze based on command options."""
        queryset = Article.objects.all()
        
        # Filter by specific article ID
        if options['article_id']:
            return queryset.filter(id=options['article_id'])
        
        # Filter by website
        if options['website']:
            queryset = queryset.filter(
                Q(feed__website__name__icontains=options['website']) |
                Q(feed__website__url__icontains=options['website'])
            )
        
        # Filter by date range
        days = options['days']
        since_date = timezone.now() - timedelta(days=days)
        queryset = queryset.filter(fetched_at__gte=since_date)
        
        # Order before applying limit
        queryset = queryset.order_by('-published_date')
        
        # Apply limit if specified
        if options['limit']:
            queryset = queryset[:options['limit']]
        
        return queryset
    
    def generate_content(self, articles, generator, options):
        """Generate new content from analyzed articles."""
        self.stdout.write(self.style.SUCCESS('\n\nGenerating New Content:'))
        
        # Filter by topic if specified
        if options['topic']:
            # Filter articles that have the topic in their analysis
            filtered_articles = []
            for article in articles:
                if hasattr(article, 'analysis'):
                    topics = article.analysis.topics
                    if any(options['topic'].lower() in topic.lower() for topic in topics):
                        filtered_articles.append(article)
            
            if not filtered_articles:
                self.stdout.write(self.style.WARNING(f'No articles found with topic: {options["topic"]}'))
                return
            
            articles = filtered_articles
        
        # Group articles by similarity/topic clusters
        # For now, we'll take the top 5 most recent articles
        source_articles = articles[:5]
        
        self.stdout.write(f'Using {len(source_articles)} source articles')
        for article in source_articles:
            self.stdout.write(f'  - {article.title[:60]}')
        
        # Generate content
        try:
            result = generator.generate_article(
                source_articles=source_articles,
                style=options['style'],
                target_length=800
            )
            
            # Save generated content
            generated = GeneratedContent.objects.create(
                title=result.get('title', 'Generated Article'),
                subtitle=result.get('subtitle', ''),
                content=result.get('content', ''),
                summary=result.get('summary', ''),
                style=options['style'],
                topics=result.get('topics', []),
                media_items=result.get('media_items', []),
                generation_params={
                    'style': options['style'],
                    'source_count': len(source_articles),
                    'topic_filter': options.get('topic', '')
                }
            )
            
            # Add source articles
            generated.source_articles.set(source_articles)
            
            self.stdout.write(self.style.SUCCESS(f'\n✓ Content Generated Successfully!'))
            self.stdout.write(f'  Title: {generated.title}')
            self.stdout.write(f'  Style: {generated.style}')
            self.stdout.write(f'  Topics: {", ".join(generated.topics)}')
            self.stdout.write(f'  ID: {generated.id}')
            
            # Show a preview
            preview = result.get('content', '')[:500].replace('<p>', '\n  ').replace('</p>', '')
            self.stdout.write(f'\n  Preview:\n  {preview}...\n')
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error generating content: {str(e)}'))