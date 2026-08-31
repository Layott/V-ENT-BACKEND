# -*- coding: utf-8 -*-
"""Local seed for walking the club chat and the organisation console.

Run from the backend root:

    DB_ENGINE=sqlite ./venv/Scripts/python.exe scripts/seed_clubs_and_orgs.py

Idempotent: run it as many times as you like. It never touches production data
because it only ever creates rows named "Walk ..." plus the demo accounts that
the local seed already made.
"""
import io
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vent.settings')
os.environ.setdefault('DB_ENGINE', 'sqlite')
django.setup()

from django.core.files.base import ContentFile  # noqa: E402
from django.utils import timezone  # noqa: E402

from vent_auth.models import (  # noqa: E402
    Club, ClubMember, ClubMessage, ClubTopic, Games, OrgInvite, OrgMember,
    Organization, Users,
)

# A one-pixel PNG is enough to prove a picture round-trips: it is stored, it is
# served, and the page draws something rather than a broken frame.
PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08'
    b'\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01'
    b'\x01\x01\x00\x1b\xb6\xee\x56\x00\x00\x00\x00IEND\xaeB`\x82'
)


def who(username):
    user = Users.objects.filter(username=username).first()
    if user is None:
        raise SystemExit('missing demo account %s - run the local seed first' % username)
    return user


owner = who('demo_organizer')
admin = who('demo_temi')
mod = who('demo_chidi')
member = who('demo_amara')
outsider = who('demo_yusuf')

game = Games.objects.filter(game_title__icontains='free fire').first() or Games.objects.first()

# ---------------------------------------------------------------------------
# A club with a real conversation in it
# ---------------------------------------------------------------------------
club, made = Club.objects.get_or_create(
    name='Walk Free Fire Lounge',
    defaults={'owner': owner, 'game': game,
              'description': 'Weeknight squads, scrim callouts and clip sharing.'},
)
if made or not club.logo:
    club.logo.save('walk-club-logo.png', ContentFile(PNG), save=False)
    club.banner.save('walk-club-banner.png', ContentFile(PNG), save=False)
    club.save()

for person, role in ((owner, 'owner'), (admin, 'admin'), (mod, 'moderator'), (member, 'member')):
    ClubMember.objects.update_or_create(club=club, user=person, defaults={'role': role})

general, _ = ClubTopic.objects.get_or_create(club=club, name='General',
                                             defaults={'position': 0, 'created_by': owner})
scrims, _ = ClubTopic.objects.get_or_create(
    club=club, name='Scrims',
    defaults={'position': 1, 'created_by': admin, 'description': 'Find a squad for tonight'})
rules, _ = ClubTopic.objects.get_or_create(
    club=club, name='Rules',
    defaults={'position': 2, 'created_by': owner, 'is_locked': True,
              'description': 'Read before posting'})

SAID = [
    (general, owner, 'Lounge is open. Keep clips in Scrims so this stays readable.'),
    (general, member, 'Anyone running Bermuda tonight around 9?'),
    (general, mod, 'I am on from 8:30. Bring a fourth.'),
    (general, admin, 'Reminder: the weekly starts Friday, entry is 500 VENT COINS.'),
    (scrims, member, 'Need one more for a 4v4 at 21:00 WAT.'),
    (scrims, admin, 'Taking the slot. My tag is in my profile.'),
    (rules, owner, 'No account sharing. No arguing with a moderator in the thread.'),
]
if not ClubMessage.objects.filter(topic__club=club).exists():
    for topic, author, body in SAID:
        ClubMessage.objects.create(topic=topic, author=author, body=body)

# One removed message, so the "this message was removed" state is walkable.
gone = ClubMessage.objects.filter(topic=general, author=member).first()
if gone and not gone.deleted_at:
    ClubMessage.objects.create(topic=general, author=outsider,
                               body='buy cheap diamonds dm me')
    spam = ClubMessage.objects.filter(topic=general, author=outsider).first()
    spam.deleted_at = timezone.now()
    spam.deleted_by = mod
    spam.save(update_fields=['deleted_at', 'deleted_by'])

# ---------------------------------------------------------------------------
# An organisation with a picture, a manager with scopes, and an invite waiting
# ---------------------------------------------------------------------------
org, made = Organization.objects.get_or_create(
    org_name='Walk Test Org',
    defaults={'org_creator': owner, 'org_owner': owner},
)
if not org.logo:
    org.logo.save('walk-org-logo.png', ContentFile(PNG), save=False)
    org.banner.save('walk-org-banner.png', ContentFile(PNG), save=False)
org.tag = org.tag or 'WTO'
org.bio = org.bio or 'A test organisation for walking the console.'
org.focus = org.focus or 'Free Fire'
org.location = org.location or 'Lagos, Nigeria'
org.region = org.region or 'West Africa'
org.save()

OrgMember.objects.update_or_create(org=org, user=owner, defaults={'role': 'owner', 'scopes': []})
OrgMember.objects.update_or_create(org=org, user=admin, defaults={'role': 'admin', 'scopes': []})
OrgMember.objects.update_or_create(
    org=org, user=mod, defaults={'role': 'manager', 'scopes': ['tournaments', 'events']})
OrgMember.objects.update_or_create(org=org, user=member, defaults={'role': 'member', 'scopes': []})

OrgInvite.objects.get_or_create(
    org=org, user=outsider, status='pending',
    defaults={'invited_by': owner, 'role': 'manager', 'scopes': ['clubs'],
              'message': 'Come and run the community side.'},
)

# A club the outsider owns and nobody has taken, so the link picker has
# something in it when you sign in as them.
spare, _ = Club.objects.get_or_create(
    name='Walk Spare Club',
    defaults={'owner': outsider, 'game': game, 'description': 'Unattached, for the picker.'},
)
ClubMember.objects.get_or_create(club=spare, user=outsider, defaults={'role': 'owner'})
ClubTopic.objects.get_or_create(club=spare, name='General',
                                defaults={'position': 0, 'created_by': outsider})

print('club        /community/club/%s' % club.slug)
print('spare club  /community/club/%s' % spare.slug)
print('org         /organizations/%s' % org.slug)
print('org manage  /organizations/%s/manage' % org.slug)
print('invite for  @%s -> /organizations/invites' % outsider.username)
print('messages    %d in %d topics' % (
    ClubMessage.objects.filter(topic__club=club).count(),
    ClubTopic.objects.filter(club=club).count()))
