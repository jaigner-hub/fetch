# Migration to add indexes for similarity detection

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('feeds', '0007_add_article_media_fields'),
    ]

    operations = [
        # Add simhash field for near-duplicate detection
        migrations.AddField(
            model_name='article',
            name='simhash',
            field=models.BigIntegerField(null=True, blank=True, db_index=True,
                                        help_text='SimHash for near-duplicate detection'),
        ),
        
        # Add title_normalized field for faster title matching
        migrations.AddField(
            model_name='article',
            name='title_normalized',
            field=models.CharField(max_length=500, blank=True, db_index=True,
                                  help_text='Normalized title for similarity matching'),
        ),
        
        # Add composite index for similarity queries
        migrations.AddIndex(
            model_name='article',
            index=models.Index(fields=['feed', 'published_date', '-id'], 
                              name='similarity_lookup_idx'),
        ),
        
        # Add index for content hash lookups
        migrations.AddIndex(
            model_name='article',
            index=models.Index(fields=['content_hash'], 
                              name='content_hash_idx'),
        ),
    ]