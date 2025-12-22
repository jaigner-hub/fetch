# Generated manually to handle foreign key constraints
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('feeds', '0006_generatedcontent_web_sources'),
    ]

    operations = [
        # Temporarily disable foreign key checks for this migration
        migrations.RunSQL(
            "SET FOREIGN_KEY_CHECKS=0;",
            reverse_sql="SET FOREIGN_KEY_CHECKS=1;",
        ),
        
        # Add the new fields
        migrations.AddField(
            model_name='article',
            name='tags',
            field=models.JSONField(blank=True, default=list, help_text='Categories/tags from the feed'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='article',
            name='images',
            field=models.JSONField(blank=True, default=list, help_text='Images found in the article'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='article',
            name='featured_image',
            field=models.URLField(blank=True, default='', help_text='Main/featured image URL', max_length=2048),
            preserve_default=True,
        ),
        
        # Re-enable foreign key checks
        migrations.RunSQL(
            "SET FOREIGN_KEY_CHECKS=1;",
            reverse_sql="SET FOREIGN_KEY_CHECKS=0;",
        ),
    ]