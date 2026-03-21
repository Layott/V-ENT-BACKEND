from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('vent_auth', '0015_teamprofile_add_country_social_links'),
    ]

    operations = [
        migrations.CreateModel(
            name='TeamInterests',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('interests', models.CharField(max_length=40)),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='vent_auth.teams')),
            ],
        ),
        migrations.CreateModel(
            name='TeamMembers',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(
                    choices=[
                        ('captain', 'Captain'),
                        ('vice_captain', 'Vice Captain'),
                        ('member', 'Member'),
                        ('coach', 'Coach'),
                        ('manager', 'Manager'),
                        ('analyst', 'Analyst'),
                    ],
                    default='member',
                    max_length=20,
                )),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='vent_auth.teams')),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='vent_auth.gameaccount')),
            ],
        ),
    ]
