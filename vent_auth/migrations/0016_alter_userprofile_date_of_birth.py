from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0015_teamprofile_add_country_social_links'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userprofile',
            name='date_of_birth',
            field=models.DateField(blank=True, null=True),
        ),
    ]
