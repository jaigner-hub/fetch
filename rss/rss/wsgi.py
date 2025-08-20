"""
WSGI config for rss project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rss.settings')

# Set the Anthropic API key from environment variable
# To use Claude features, set ANTHROPIC_API_KEY in your environment
# If not set in environment, set it here for Apache/WSGI
if not os.environ.get('ANTHROPIC_API_KEY'):
    os.environ['ANTHROPIC_API_KEY'] = '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'

application = get_wsgi_application()
