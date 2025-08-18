"""
Management command to find and manage duplicate articles
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from feeds.models import Article
from feeds.similarity_detector import SimilarityDetector
import json


class Command(BaseCommand):
    help = 'Find duplicate and similar articles in the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Number of days back to search (default: 7)'
        )
        parser.add_argument(
            '--threshold',
            type=float,
            default=0.85,
            help='Similarity threshold 0-1 (default: 0.85)'
        )
        parser.add_argument(
            '--article-id',
            type=int,
            help='Find duplicates for a specific article ID'
        )
        parser.add_argument(
            '--action',
            choices=['list', 'merge', 'delete'],
            default='list',
            help='Action to take with duplicates'
        )
        parser.add_argument(
            '--output',
            choices=['terminal', 'json', 'csv'],
            default='terminal',
            help='Output format'
        )
        parser.add_argument(
            '--check-all',
            action='store_true',
            help='Check all articles (not just recent ones)'
        )

    def handle(self, *args, **options):
        detector = SimilarityDetector()
        
        if options['article_id']:
            # Find duplicates for specific article
            self.find_for_article(detector, options)
        else:
            # Find all duplicates in time range
            self.find_all_duplicates(detector, options)
    
    def find_for_article(self, detector, options):
        """Find duplicates for a specific article."""
        try:
            article = Article.objects.get(id=options['article_id'])
        except Article.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Article {options['article_id']} not found"))
            return
        
        self.stdout.write(f"\nAnalyzing article: {article.title[:80]}")
        self.stdout.write(f"Published: {article.published_date}")
        self.stdout.write(f"Website: {article.feed.website.name}")
        self.stdout.write("-" * 80)
        
        # Find exact duplicates
        exact_dupes = detector.find_exact_duplicates(article)
        if exact_dupes:
            self.stdout.write(self.style.WARNING(f"\n{len(exact_dupes)} EXACT DUPLICATES:"))
            for dupe in exact_dupes:
                self.stdout.write(f"  [{dupe.id}] {dupe.title[:60]} - {dupe.feed.website.name}")
        
        # Find near duplicates
        near_dupes = detector.find_near_duplicates(article, max_hamming_distance=5)
        if near_dupes:
            self.stdout.write(self.style.WARNING(f"\n{len(near_dupes)} NEAR DUPLICATES:"))
            for dupe, distance in near_dupes[:10]:
                self.stdout.write(f"  [{dupe.id}] Distance: {distance} - {dupe.title[:60]}")
        
        # Find similar articles
        similar = detector.find_similar_articles(
            article,
            threshold=options['threshold'],
            days_back=options['days'],
            check_all_time=options['check_all']
        )
        
        if similar:
            self.stdout.write(self.style.SUCCESS(f"\n{len(similar)} SIMILAR ARTICLES:"))
            for similar_article, scores in similar:
                self.stdout.write(f"\n  [{similar_article.id}] Overall: {scores['overall']:.2%}")
                self.stdout.write(f"    Title: {similar_article.title[:60]}")
                self.stdout.write(f"    Website: {similar_article.feed.website.name}")
                self.stdout.write(f"    Published: {similar_article.published_date}")
                self.stdout.write(f"    Scores: Title={scores['title']:.2%}, "
                                f"Content={scores['content']:.2%}, "
                                f"SimHash={scores['simhash']:.2%}")
        
        # Handle actions
        if options['action'] == 'merge' and (exact_dupes or similar):
            self.merge_duplicates(article, exact_dupes, similar)
        elif options['action'] == 'delete' and exact_dupes:
            self.delete_duplicates(exact_dupes)
    
    def find_all_duplicates(self, detector, options):
        """Find all duplicate groups in the database."""
        self.stdout.write(f"\nSearching for duplicates in last {options['days']} days...")
        self.stdout.write(f"Similarity threshold: {options['threshold']:.0%}")
        
        # Get duplicate groups
        duplicate_groups = detector.bulk_find_duplicates(
            days_back=options['days'],
            similarity_threshold=options['threshold']
        )
        
        if not duplicate_groups:
            self.stdout.write(self.style.SUCCESS("No duplicates found!"))
            return
        
        total_duplicates = sum(len(dupes) for dupes in duplicate_groups.values())
        self.stdout.write(self.style.WARNING(
            f"\nFound {len(duplicate_groups)} groups with {total_duplicates} duplicate articles"
        ))
        
        # Output based on format
        if options['output'] == 'json':
            self.output_json(duplicate_groups)
        elif options['output'] == 'csv':
            self.output_csv(duplicate_groups)
        else:
            self.output_terminal(duplicate_groups)
        
        # Handle actions
        if options['action'] == 'delete':
            self.bulk_delete_duplicates(duplicate_groups)
        elif options['action'] == 'merge':
            self.bulk_merge_duplicates(duplicate_groups)
    
    def output_terminal(self, duplicate_groups):
        """Output results to terminal."""
        for primary_id, duplicate_ids in duplicate_groups.items():
            try:
                primary = Article.objects.get(id=primary_id)
                self.stdout.write(f"\n{self.style.SUCCESS('Primary Article:')}")
                self.stdout.write(f"  [{primary.id}] {primary.title[:80]}")
                self.stdout.write(f"  Website: {primary.feed.website.name}")
                self.stdout.write(f"  Published: {primary.published_date}")
                
                self.stdout.write(f"  {self.style.WARNING('Duplicates:')}")
                for dup_id in duplicate_ids[:5]:  # Show max 5
                    try:
                        dup = Article.objects.get(id=dup_id)
                        self.stdout.write(f"    [{dup.id}] {dup.title[:60]} - {dup.feed.website.name}")
                    except Article.DoesNotExist:
                        pass
                
                if len(duplicate_ids) > 5:
                    self.stdout.write(f"    ... and {len(duplicate_ids) - 5} more")
                    
            except Article.DoesNotExist:
                pass
    
    def output_json(self, duplicate_groups):
        """Output results as JSON."""
        output = []
        for primary_id, duplicate_ids in duplicate_groups.items():
            try:
                primary = Article.objects.get(id=primary_id)
                duplicates = []
                for dup_id in duplicate_ids:
                    try:
                        dup = Article.objects.get(id=dup_id)
                        duplicates.append({
                            'id': dup.id,
                            'title': dup.title,
                            'url': dup.url,
                            'website': dup.feed.website.name,
                            'published': dup.published_date.isoformat() if dup.published_date else None
                        })
                    except Article.DoesNotExist:
                        pass
                
                output.append({
                    'primary': {
                        'id': primary.id,
                        'title': primary.title,
                        'url': primary.url,
                        'website': primary.feed.website.name,
                        'published': primary.published_date.isoformat() if primary.published_date else None
                    },
                    'duplicates': duplicates
                })
            except Article.DoesNotExist:
                pass
        
        print(json.dumps(output, indent=2))
    
    def output_csv(self, duplicate_groups):
        """Output results as CSV."""
        import csv
        import sys
        
        writer = csv.writer(sys.stdout)
        writer.writerow(['Primary ID', 'Primary Title', 'Primary Website', 
                        'Duplicate ID', 'Duplicate Title', 'Duplicate Website'])
        
        for primary_id, duplicate_ids in duplicate_groups.items():
            try:
                primary = Article.objects.get(id=primary_id)
                for dup_id in duplicate_ids:
                    try:
                        dup = Article.objects.get(id=dup_id)
                        writer.writerow([
                            primary.id, primary.title, primary.feed.website.name,
                            dup.id, dup.title, dup.feed.website.name
                        ])
                    except Article.DoesNotExist:
                        pass
            except Article.DoesNotExist:
                pass
    
    def merge_duplicates(self, primary, exact_dupes, similar):
        """Merge duplicate articles."""
        self.stdout.write(f"\nMerging duplicates into article {primary.id}...")
        
        merged_count = 0
        all_dupes = list(exact_dupes) if exact_dupes else []
        if similar:
            all_dupes.extend([s[0] for s in similar if s[1]['overall'] > 0.9])
        
        for dupe in all_dupes:
            # Add duplicate's feeds to primary's additional_feeds
            primary.additional_feeds.add(dupe.feed)
            
            # Merge tags
            if dupe.tags:
                if not primary.tags:
                    primary.tags = []
                for tag in dupe.tags:
                    if tag not in primary.tags:
                        primary.tags.append(tag)
            
            # Delete duplicate
            dupe.delete()
            merged_count += 1
        
        if merged_count > 0:
            primary.save()
            self.stdout.write(self.style.SUCCESS(f"Merged {merged_count} duplicates"))
    
    def delete_duplicates(self, duplicates):
        """Delete duplicate articles."""
        if not duplicates:
            return
        
        confirm = input(f"\nDelete {len(duplicates)} duplicate articles? (y/N): ")
        if confirm.lower() == 'y':
            for dupe in duplicates:
                dupe.delete()
            self.stdout.write(self.style.SUCCESS(f"Deleted {len(duplicates)} duplicates"))
        else:
            self.stdout.write("Deletion cancelled")
    
    def bulk_delete_duplicates(self, duplicate_groups):
        """Delete all duplicates, keeping primary articles."""
        total = sum(len(dupes) for dupes in duplicate_groups.values())
        confirm = input(f"\nDelete {total} duplicate articles? (y/N): ")
        
        if confirm.lower() == 'y':
            deleted = 0
            for primary_id, duplicate_ids in duplicate_groups.items():
                for dup_id in duplicate_ids:
                    try:
                        Article.objects.get(id=dup_id).delete()
                        deleted += 1
                    except Article.DoesNotExist:
                        pass
            
            self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} duplicate articles"))
        else:
            self.stdout.write("Deletion cancelled")
    
    def bulk_merge_duplicates(self, duplicate_groups):
        """Merge all duplicate groups."""
        total = sum(len(dupes) for dupes in duplicate_groups.values())
        confirm = input(f"\nMerge {total} duplicate articles? (y/N): ")
        
        if confirm.lower() == 'y':
            merged = 0
            for primary_id, duplicate_ids in duplicate_groups.items():
                try:
                    primary = Article.objects.get(id=primary_id)
                    for dup_id in duplicate_ids:
                        try:
                            dupe = Article.objects.get(id=dup_id)
                            primary.additional_feeds.add(dupe.feed)
                            dupe.delete()
                            merged += 1
                        except Article.DoesNotExist:
                            pass
                except Article.DoesNotExist:
                    pass
            
            self.stdout.write(self.style.SUCCESS(f"Merged {merged} duplicate articles"))
        else:
            self.stdout.write("Merge cancelled")