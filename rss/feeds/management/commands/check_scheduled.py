"""
Management command to manually check and run scheduled content fetches.
"""
from django.core.management.base import BaseCommand
from feeds.tasks import check_scheduled_fetches


class Command(BaseCommand):
    help = 'Check for scheduled content fetches and execute them'

    def handle(self, *args, **options):
        self.stdout.write('Checking for scheduled content fetches...')
        
        # Run the scheduled check
        result = check_scheduled_fetches()
        
        self.stdout.write(self.style.SUCCESS(f'Result: {result}'))