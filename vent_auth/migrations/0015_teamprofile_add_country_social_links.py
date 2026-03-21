from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0014_alter_users_email_unique'),
    ]

    operations = [
        migrations.AddField(
            model_name='teamprofile',
            name='country',
            field=models.CharField(blank=True, max_length=40, null=True),
        ),
        migrations.AddField(
            model_name='teamprofile',
            name='facebook_link',
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='teamprofile',
            name='twitter_link',
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='teamprofile',
            name='instagram_link',
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='teamprofile',
            name='youtube_link',
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='teamprofile',
            name='twitch_link',
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='teamprofile',
            name='kick_link',
            field=models.URLField(blank=True, null=True),
        ),
    ]
