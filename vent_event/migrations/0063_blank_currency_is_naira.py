"""An event with a blank currency is priced in naira (inbox 591).

The edit endpoint stored an empty currency when a picker sent "", so an event
could end up with no currency at all. The endpoint now stores NGN for an empty
value; this puts any event already left blank back on naira. Prices are not
touched: they are kept in naira whatever the currency says.
"""
from django.db import migrations


def blank_to_naira(apps, schema_editor):
    Event = apps.get_model('vent_event', 'Event')
    Event.objects.filter(currency='').update(currency='NGN')


class Migration(migrations.Migration):

    dependencies = [
        ('vent_event', '0062_event_polls_after_end'),
    ]

    operations = [
        migrations.RunPython(blank_to_naira, migrations.RunPython.noop),
    ]
