"""A tournament invitation carries what the helper will hold (P7, 17 October 2026).

A tournament helper holds permissions now (vent_tournament 0071). An invitation
waiting for somebody with no account yet has to carry them too, so `scopes`
holds the list and the role is 'staff'. Invitations still pending from before
were for a scorekeeper, which means results only: they become 'staff' with
['report'].

Renumber if another branch adds vent_auth 0094 first.
"""
from django.db import migrations, models


def scorekeeper_invites_report(apps, schema_editor):
    RoleInvite = apps.get_model('vent_auth', 'RoleInvite')
    for invite in RoleInvite.objects.filter(kind='tournament', role='scorekeeper', status='pending'):
        invite.role = 'staff'
        invite.scopes = ['report']
        invite.save(update_fields=['role', 'scopes'])


def nothing(apps, schema_editor):
    """Going back, a 'staff' invitation keeps its role; the column goes."""


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0093_strip_usernames'),
    ]

    operations = [
        migrations.AddField(
            model_name='roleinvite',
            name='scopes',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name='roleinvite',
            name='role',
            field=models.CharField(choices=[('manager', 'Manager'), ('door', 'Door staff'),
                                            ('scorekeeper', 'Scorekeeper'),
                                            ('staff', 'Tournament staff')], max_length=12),
        ),
        migrations.RunPython(scorekeeper_invites_report, nothing),
    ]
