# Generated by Django 4.2.6 on 2023-10-30 16:59

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comment', '0002_comment_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='comment',
            name='attachments',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.FileField(upload_to='comments/%Y/%m/%d/'), blank=True, null=True, size=10),
        ),
    ]
