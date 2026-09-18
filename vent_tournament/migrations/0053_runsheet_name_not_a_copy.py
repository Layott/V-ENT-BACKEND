# A run sheet's `name` was a copy of its owner's name taken the day the sheet
# was made, and no screen has ever offered to set it to anything else. After a
# rename the sheet page was headed with last week's name (18 September 2026).
# Readers head the page with the owner's live name now and show `name` only
# when it says something different, so every copy is blanked: a copy that
# still matched would be invisible, and one that no longer matched would show
# the stale name under the fresh one.
from django.db import migrations


def blank_copied_names(apps, schema_editor):
    RunSheet = apps.get_model('vent_tournament', 'RunSheet')
    RunSheet.objects.exclude(name='').update(name='')


class Migration(migrations.Migration):

    dependencies = [
        ('vent_tournament', '0052_tournament_money'),
    ]

    operations = [
        migrations.RunPython(blank_copied_names, migrations.RunPython.noop),
    ]
