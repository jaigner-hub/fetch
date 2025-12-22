"""
Management command to set fetch intervals for websites.
"""
from django.core.management.base import BaseCommand
from feeds.models import Website


class Command(BaseCommand):
    help = 'Set fetch interval for websites'

    def add_arguments(self, parser):
        parser.add_argument(
            '--website',
            type=str,
            help='Name of the website (partial match supported)'
        )
        parser.add_argument(
            '--interval',
            type=int,
            choices=[15, 30, 60, 120, 240, 360, 720, 1440],
            help='Fetch interval in minutes'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Apply to all websites'
        )
        parser.add_argument(
            '--list',
            action='store_true',
            help='List current intervals'
        )
        parser.add_argument(
            '--enable',
            action='store_true',
            help='Enable auto-fetch for the website(s)'
        )
        parser.add_argument(
            '--disable',
            action='store_true',
            help='Disable auto-fetch for the website(s)'
        )

    def handle(self, *args, **options):
        if options['list']:
            self.list_intervals()
            return

        if options['interval']:
            if options['all']:
                self.set_all_intervals(options['interval'], options.get('enable'), options.get('disable'))
            elif options['website']:
                self.set_website_interval(options['website'], options['interval'], 
                                         options.get('enable'), options.get('disable'))
            else:
                self.stdout.write(self.style.ERROR('Specify --website NAME or --all'))
        elif options['enable'] or options['disable']:
            if options['all']:
                self.toggle_all_fetch(options.get('enable'))
            elif options['website']:
                self.toggle_website_fetch(options['website'], options.get('enable'))
            else:
                self.stdout.write(self.style.ERROR('Specify --website NAME or --all'))
        else:
            self.show_usage()

    def show_usage(self):
        """Show usage examples."""
        self.stdout.write('\nUsage examples:')
        self.stdout.write('  python manage.py set_fetch_interval --list')
        self.stdout.write('  python manage.py set_fetch_interval --website "Hollywood" --interval 30')
        self.stdout.write('  python manage.py set_fetch_interval --all --interval 60')
        self.stdout.write('  python manage.py set_fetch_interval --website "Variety" --enable')
        self.stdout.write('  python manage.py set_fetch_interval --all --disable')
        self.stdout.write('\nAvailable intervals: 15, 30, 60, 120, 240, 360, 720, 1440 minutes')

    def list_intervals(self):
        """List current fetch intervals for all websites."""
        websites = Website.objects.all().order_by('fetch_interval_minutes', 'name')
        
        self.stdout.write(self.style.SUCCESS(f'\nWebsite Fetch Intervals ({websites.count()} total):'))
        self.stdout.write('-' * 70)
        
        current_interval = None
        for website in websites:
            if website.fetch_interval_minutes != current_interval:
                current_interval = website.fetch_interval_minutes
                interval_label = dict(Website.FETCH_INTERVAL_CHOICES).get(
                    current_interval, f'{current_interval} minutes'
                )
                self.stdout.write(f'\n{self.style.WARNING(interval_label)}:')
            
            status = '✓' if website.auto_fetch_enabled else '✗'
            active = '(active)' if website.active else '(inactive)'
            self.stdout.write(f'  {status} {website.name:35} {active}')

    def set_website_interval(self, name_partial, interval, enable=None, disable=None):
        """Set fetch interval for a specific website."""
        websites = Website.objects.filter(name__icontains=name_partial)
        
        if not websites.exists():
            self.stdout.write(self.style.ERROR(f'No website found matching "{name_partial}"'))
            return
        
        if websites.count() > 1:
            self.stdout.write(self.style.WARNING(f'Found {websites.count()} websites:'))
            for w in websites:
                self.stdout.write(f'  - {w.name}')
            self.stdout.write('Please be more specific')
            return
        
        website = websites.first()
        old_interval = website.fetch_interval_minutes
        website.fetch_interval_minutes = interval
        
        if enable:
            website.auto_fetch_enabled = True
        elif disable:
            website.auto_fetch_enabled = False
        
        website.save()
        
        interval_label = dict(Website.FETCH_INTERVAL_CHOICES).get(interval, f'{interval} minutes')
        self.stdout.write(self.style.SUCCESS(
            f'Updated {website.name}:\n'
            f'  Interval: {old_interval} min → {interval} min ({interval_label})\n'
            f'  Auto-fetch: {"Enabled" if website.auto_fetch_enabled else "Disabled"}'
        ))

    def set_all_intervals(self, interval, enable=None, disable=None):
        """Set fetch interval for all websites."""
        websites = Website.objects.all()
        count = websites.count()
        
        updates = {'fetch_interval_minutes': interval}
        if enable:
            updates['auto_fetch_enabled'] = True
        elif disable:
            updates['auto_fetch_enabled'] = False
        
        websites.update(**updates)
        
        interval_label = dict(Website.FETCH_INTERVAL_CHOICES).get(interval, f'{interval} minutes')
        self.stdout.write(self.style.SUCCESS(
            f'Updated {count} websites to {interval_label}'
        ))
        
        if enable:
            self.stdout.write('Auto-fetch enabled for all websites')
        elif disable:
            self.stdout.write('Auto-fetch disabled for all websites')

    def toggle_website_fetch(self, name_partial, enable):
        """Enable or disable auto-fetch for a website."""
        websites = Website.objects.filter(name__icontains=name_partial)
        
        if not websites.exists():
            self.stdout.write(self.style.ERROR(f'No website found matching "{name_partial}"'))
            return
        
        if websites.count() > 1:
            self.stdout.write(self.style.WARNING(f'Found {websites.count()} websites:'))
            for w in websites:
                self.stdout.write(f'  - {w.name}')
            self.stdout.write('Please be more specific')
            return
        
        website = websites.first()
        website.auto_fetch_enabled = enable
        website.save()
        
        status = "Enabled" if enable else "Disabled"
        self.stdout.write(self.style.SUCCESS(
            f'{status} auto-fetch for {website.name}'
        ))

    def toggle_all_fetch(self, enable):
        """Enable or disable auto-fetch for all websites."""
        websites = Website.objects.all()
        count = websites.update(auto_fetch_enabled=enable)
        
        status = "Enabled" if enable else "Disabled"
        self.stdout.write(self.style.SUCCESS(
            f'{status} auto-fetch for {count} websites'
        ))