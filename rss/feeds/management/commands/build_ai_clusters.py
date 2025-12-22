"""
Build AI-powered article clusters based on actual events and stories.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from feeds.models import Article, ArticleCluster
from django.conf import settings
from django.db.models import Q, Exists, OuterRef
import anthropic
import json
import logging
from typing import List, Dict, Set
from feeds.local_clustering import LocalClusterBuilder

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Build AI-powered article clusters based on events/stories'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=48,
            help='Look back period in hours (default: 48)'
        )
        parser.add_argument(
            '--min-articles',
            type=int,
            default=3,
            help='Minimum articles per cluster (default: 3)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Number of articles to process per batch (default: 50)'
        )
        parser.add_argument(
            '--max-batches',
            type=int,
            default=0,
            help='Maximum number of batches to process (0 for all)'
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing clusters before building new ones'
        )
        parser.add_argument(
            '--continuous',
            action='store_true',
            help='Process entire database continuously'
        )
        parser.add_argument(
            '--skip-clustered',
            action='store_true',
            default=True,
            help='Skip articles already in clusters (default: True)'
        )
        parser.add_argument(
            '--use-local',
            action='store_true',
            help='Use local clustering instead of Claude API'
        )
    
    def handle(self, *args, **options):
        hours = options['hours']
        min_articles = options['min_articles']
        batch_size = options['batch_size']
        max_batches = options['max_batches']
        skip_clustered = options['skip_clustered']
        use_local = options.get('use_local', False)
        
        if options['clear']:
            self.stdout.write("Clearing existing clusters...")
            ArticleCluster.objects.all().delete()
        
        # Use local clustering if requested
        if use_local:
            self.stdout.write("Using local clustering (no API calls)...")
            return self.handle_local_clustering(options)
        
        # Initialize Claude
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        
        # Track processed articles across batches
        all_processed_ids: Set[int] = set()
        total_clusters_created = 0
        batch_count = 0
        
        # Build base query
        base_query = Article.objects.select_related('feed__website')
        
        if not options['continuous']:
            # Time-based filtering
            since = timezone.now() - timedelta(hours=hours)
            base_query = base_query.filter(published_date__gte=since)
        
        if skip_clustered:
            # Exclude articles already in clusters
            base_query = base_query.filter(
                ~Exists(ArticleCluster.objects.filter(articles=OuterRef('pk')))
            )
        
        # Get total count
        total_articles = base_query.count()
        self.stdout.write(f"Total articles to process: {total_articles}")
        
        if total_articles == 0:
            self.stdout.write(self.style.WARNING("No unclustered articles found."))
            return
        
        # Process in batches
        offset = 0
        while offset < total_articles:
            if max_batches > 0 and batch_count >= max_batches:
                self.stdout.write(f"Reached maximum batch limit ({max_batches})")
                break
            
            batch_count += 1
            
            # Get batch of articles
            articles = base_query.order_by('-published_date')[offset:offset + batch_size]
            
            if not articles:
                break
            
            # Filter out already processed articles
            batch_articles = []
            for article in articles:
                if article.id not in all_processed_ids:
                    batch_articles.append(article)
                    all_processed_ids.add(article.id)
            
            if len(batch_articles) < min_articles:
                self.stdout.write(f"Batch {batch_count}: Skipping, only {len(batch_articles)} new articles")
                offset += batch_size
                continue
            
            self.stdout.write(
                f"\nBatch {batch_count}: Processing {len(batch_articles)} articles "
                f"(offset {offset}/{total_articles})"
            )
            
            # Process this batch
            clusters_created = self.process_article_batch(
                batch_articles, 
                client, 
                min_articles
            )
            
            total_clusters_created += clusters_created
            offset += batch_size
            
            # Show progress
            progress_pct = min(100, (offset / total_articles) * 100)
            self.stdout.write(
                f"Progress: {progress_pct:.1f}% | "
                f"Processed: {len(all_processed_ids)} articles | "
                f"Clusters created: {total_clusters_created}"
            )
        
        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Completed! Processed {len(all_processed_ids)} articles, "
                f"created {total_clusters_created} clusters"
            )
        )
    
    def process_article_batch(
        self, 
        articles: List[Article], 
        client: anthropic.Anthropic,
        min_articles: int
    ) -> int:
        """Process a batch of articles and create clusters."""
        
        # Prepare article data for AI
        article_data = []
        article_map = {}  # Map IDs to Article objects
        
        for article in articles:
            article_map[article.id] = article
            article_data.append({
                'id': article.id,
                'title': article.title,
                'summary': article.summary or article.content[:500] if article.content else '',
                'source': article.feed.website.name if article.feed and article.feed.website else 'Unknown',
                'date': article.published_date.isoformat() if article.published_date else '',
                'url': article.url
            })
        
        # Ask Claude to identify clusters
        prompt = f"""Analyze these {len(article_data)} news articles and identify clusters of articles covering the SAME specific event or story.

Articles to analyze:
{json.dumps(article_data, indent=2)}

Group articles that are about the EXACT SAME event/story/announcement. Be very specific - articles must be about the same specific incident, not just the same general topic.

For each cluster, provide:
1. A descriptive title for the event/story
2. A brief description  
3. The article IDs that belong to this cluster
4. Key entities involved (people, organizations, locations)
5. Confidence score (0-1) for this cluster

Return ONLY valid JSON in this format:
{{
    "clusters": [
        {{
            "title": "Specific event title",
            "description": "What happened",
            "article_ids": [id1, id2, id3],
            "entities": {{
                "people": ["Name 1", "Name 2"],
                "organizations": ["Org 1"],
                "locations": ["Place 1"]
            }},
            "confidence": 0.9,
            "event_type": "breaking_news|ongoing_story|announcement|incident"
        }}
    ]
}}

Only create clusters with at least {min_articles} articles. Focus on HIGH CONFIDENCE clusters (>0.7) where you're sure the articles are about the same specific event."""

        try:
            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4000,
                temperature=0,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            result_text = response.content[0].text
            
            # Extract JSON from response
            import re
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if not json_match:
                self.stdout.write(self.style.ERROR("Failed to extract JSON from AI response"))
                return 0
            
            clusters_data = json.loads(json_match.group())
            created_count = 0
            
            for cluster_info in clusters_data.get('clusters', []):
                # Validate cluster
                article_ids = cluster_info.get('article_ids', [])
                confidence = cluster_info.get('confidence', 0)
                
                if len(article_ids) < min_articles:
                    continue
                
                if confidence < 0.7:
                    self.stdout.write(f"  Skipping low confidence cluster: {cluster_info.get('title')} ({confidence})")
                    continue
                
                # Check if any of these articles are already clustered
                existing_clusters = ArticleCluster.objects.filter(
                    articles__id__in=article_ids
                ).distinct()
                
                if existing_clusters.exists():
                    self.stdout.write(
                        f"  Skipping - articles already in cluster: {existing_clusters.first().title}"
                    )
                    continue
                
                # Create cluster
                try:
                    cluster = ArticleCluster.objects.create(
                        title=cluster_info['title'],
                        description=cluster_info['description'],
                        event_type=cluster_info.get('event_type', 'ongoing_story'),
                        main_topics=[],
                        key_entities=cluster_info.get('entities', {}),
                        confidence_score=confidence
                    )
                    
                    # Add articles
                    cluster_articles = Article.objects.filter(id__in=article_ids)
                    cluster.articles.set(cluster_articles)
                    
                    # Update metadata
                    cluster.update_metadata()
                    
                    created_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Created: {cluster.title[:60]}... "
                            f"({cluster.articles.count()} articles, "
                            f"{cluster.source_count} sources, "
                            f"confidence: {confidence:.2f})"
                        )
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"  Error creating cluster: {e}")
                    )
            
            return created_count
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"Error processing batch: {e}")
            )
            return 0
    
    def handle_local_clustering(self, options):
        """Handle clustering using local methods (no API)."""
        hours = options['hours']
        min_articles = options['min_articles']
        batch_size = options['batch_size']
        skip_clustered = options['skip_clustered']
        
        # Initialize local cluster builder
        use_transformers = options.get('use_transformers', False)
        builder = LocalClusterBuilder(use_sentence_transformers=use_transformers)
        
        # Build query
        since = timezone.now() - timedelta(hours=hours)
        base_query = Article.objects.select_related('feed__website').filter(
            published_date__gte=since
        )
        
        if skip_clustered:
            base_query = base_query.filter(
                ~Exists(ArticleCluster.objects.filter(articles=OuterRef('pk')))
            )
        
        # Get articles
        articles = list(base_query.order_by('-published_date')[:batch_size])
        
        if len(articles) < min_articles:
            self.stdout.write(
                self.style.WARNING(f"Only {len(articles)} articles found. Need at least {min_articles}.")
            )
            return
        
        self.stdout.write(f"Processing {len(articles)} articles with local clustering...")
        
        # Build clusters
        clusters_data = builder.build_clusters(
            articles,
            min_cluster_size=min_articles,
            similarity_threshold=0.65,
            time_window_hours=hours,
            method='hybrid'
        )
        
        # Create ArticleCluster objects
        created_count = 0
        for cluster_data in clusters_data:
            try:
                # Check if any articles are already clustered
                article_ids = [a.id for a in cluster_data['articles']]
                existing = ArticleCluster.objects.filter(articles__id__in=article_ids).exists()
                
                if existing:
                    continue
                
                # Create cluster
                cluster = ArticleCluster.objects.create(
                    title=cluster_data['title'][:200],
                    description=cluster_data['description'],
                    event_type=cluster_data['event_type'],
                    main_topics=[],
                    key_entities=cluster_data.get('entities', {}),
                    confidence_score=cluster_data['confidence']
                )
                
                # Add articles
                cluster.articles.set(cluster_data['articles'])
                
                # Update metadata
                cluster.update_metadata()
                
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Created: {cluster.title[:60]}... "
                        f"({cluster.articles.count()} articles, "
                        f"{cluster.source_count} sources)"
                    )
                )
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating cluster: {e}"))
        
        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Completed! Created {created_count} clusters using local methods (no API calls)"
            )
        )