#!/bin/bash

# Start Celery worker for RSS feed processing
cd /var/www/rss/rss
source ../venv/bin/activate
celery -A rss worker -l info