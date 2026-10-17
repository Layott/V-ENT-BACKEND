"""A tournament helper holds permissions, not one role (P7, 17 October 2026).

Design section 16: "granular roles (structure, report, place entrants,
standings, caster)". `TournamentStaff.role` only ever held 'scorekeeper', and a
scorekeeper could enter results and nothing else, so every existing row becomes
`permissions = ['report']` and keeps exactly what it had. The column that held
the one role goes: `access.permissions_of` reads the list and nothing else.

Numbered after P2's 0071_feeds, which merged first.
"""
from django.db import migrations, models


def scorekeepers_report(apps, schema_editor):
    TournamentStaff = apps.get_model('vent_tournament', 'TournamentStaff')
    for row in TournamentStaff.objects.all():
        if not row.permissions:
            row.permissions = ['report']
            row.save(update_fields=['permissions'])


def nothing(apps, schema_editor):
    """Going back, the role column returns with its old default."""


class Migration(migrations.Migration):

    dependencies = [
        ('vent_tournament', '0071_feeds'),
    ]

    operations = [
        migrations.AddField(
            model_name='tournamentstaff',
            name='permissions',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(scorekeepers_report, nothing),
        migrations.RemoveField(
            model_name='tournamentstaff',
            name='role',
        ),
    ]
