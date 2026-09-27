"""Tournaments that saved a 30 minute dispute window without anybody choosing it.

The create wizard wrote `dispute_window_minutes: 30` into options with no
control on screen, and the views ignored it and used 24 hours. Now the option is
read, so a 30 that nobody chose would cut those players' window to half an
hour. Every stored 30 predates the control, so it is moved to the 24 hours
players actually had (CEO, 27 September 2026).
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Tournament = apps.get_model('vent_tournament', 'Tournament')
    for t in Tournament.objects.all().only('pk', 'options'):
        opts = t.options if isinstance(t.options, dict) else {}
        if opts.get('dispute_window_minutes') == 30:
            opts['dispute_window_minutes'] = 1440
            Tournament.objects.filter(pk=t.pk).update(options=opts)


class Migration(migrations.Migration):
    dependencies = [('vent_tournament', '0054_stages_own_their_matches')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
