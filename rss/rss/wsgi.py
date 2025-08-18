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

application = get_wsgi_application()
