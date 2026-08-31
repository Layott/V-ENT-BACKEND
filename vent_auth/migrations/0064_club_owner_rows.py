"""Give every existing club an owner row and a topic to talk in.

Clubs already existed with members but no roles, so after 0063 every one of them
had a club whose owner was a plain member and no way to appoint anybody. And a
club with no topic has nowhere to put a message, so the first person to open one
would have found a working chat with no channel in it.
"""
from django.db import migrations


def seed(apps, schema_editor):
    Club = apps.get_model('vent_auth', 'Club')
    ClubMember = apps.get_model('vent_auth', 'ClubMember')
    ClubTopic = apps.get_model('vent_auth', 'ClubTopic')

    for club in Club.objects.all():
        if club.owner_id:
            row, made = ClubMember.objects.get_or_create(
                club=club, user_id=club.owner_id, defaults={'role': 'owner'})
            if not made and row.role != 'owner':
                row.role = 'owner'
                row.save(update_fields=['role'])

        if not ClubTopic.objects.filter(club=club).exists():
            ClubTopic.objects.create(
                club=club, name='General', position=0,
                created_by_id=club.owner_id,
                description='Anything about this club.')


def unseed(apps, schema_editor):
    # The rows are ordinary data; stepping back down leaves them alone rather
    # than deleting somebody's membership.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('vent_auth', '0063_clubmember_muted_until_clubmember_role_clubtopic_and_more'),
    ]
    operations = [migrations.RunPython(seed, unseed)]
