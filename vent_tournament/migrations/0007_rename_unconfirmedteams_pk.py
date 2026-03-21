from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vent_tournament', '0006_sponsor_alter_tournament_options_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='unconfirmedteams',
            old_name='match_id',
            new_name='id',
        ),
        migrations.AlterModelOptions(
            name='unconfirmedteams',
            options={},
        ),
    ]
