# Generated manually to remove SITEMAP from feed types

from django.db import migrations, models


def remove_sitemap_feeds(apps, schema_editor):
    """Remove all SITEMAP type feeds from the database."""
    Feed = apps.get_model('feeds', 'Feed')
    Feed.objects.filter(feed_type='SITEMAP').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('feeds', '0009_add_article_cluster_only'),
    ]

    operations = [
        # First remove all SITEMAP feeds
        migrations.RunPython(remove_sitemap_feeds, migrations.RunPython.noop),
        
        # Then alter the field to remove SITEMAP from choices
        migrations.AlterField(
            model_name='feed',
            name='feed_type',
            field=models.CharField(choices=[('RSS', 'RSS Feed'), ('ATOM', 'Atom Feed')], max_length=10),
        ),
        migrations.AlterField(
            model_name='feed',
            name='feed_url',
            field=models.URLField(help_text='URL of the RSS or Atom feed', max_length=2048, unique=True),
        ),
    ]