# Generated migration for adding manual_exclude field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('feeds', '0013_add_project_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='feed',
            name='manual_exclude',
            field=models.BooleanField(default=False, help_text="Manually excluded, won't be re-added by auto-discovery"),
        ),
    ]