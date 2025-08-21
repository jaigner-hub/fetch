# Manual migration to add selective sitemap support

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('feeds', '0010_remove_sitemap_feedtype'),
    ]

    operations = [
        # Only alter the feed_type field to add SITEMAP back
        migrations.AlterField(
            model_name='feed',
            name='feed_type',
            field=models.CharField(choices=[('RSS', 'RSS Feed'), ('ATOM', 'Atom Feed'), ('SITEMAP', 'Sitemap (Recent Content)')], max_length=10),
        ),
    ]