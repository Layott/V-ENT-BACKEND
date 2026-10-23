#!/usr/bin/env python
"""Local fixtures for the game studio walk and its IDOR cases (P6, gates 74).

`security/idor-cases.json` signs in as walk_organiser (A) and walk_buyer_ada
(B), the accounts `tools/walk_event.py --setup` makes. The studio cases need A
to run a studio with a game whose address is fixed, so a case can name it:

    /partners/studios/walk-arena/...

Run after walk_event.py --setup, against the local sqlite database only:

    DB_ENGINE=sqlite DEBUG=True venv/Scripts/python.exe tools/walk_event.py --setup
    DB_ENGINE=sqlite DEBUG=True venv/Scripts/python.exe tools/walk_studios.py --setup

Safe to run twice: everything is found or made, never made twice. Prints the
signing secret only when it makes the game, because that is the only time it
exists in readable form.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vent.settings')

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from vent_auth.models import Games, Users  # noqa: E402
from vent_partners.models import Partner, StudioGame  # noqa: E402

GAME_SLUG = 'walk-arena'
DELIVERY_REF = 'dl_walkidorcase001'
LINK_REF = 'ln_walkidorcase'
CODE_REF = 'lc_walkidorcase'
SESSION_REF = 'gs_walkidorcase0001'
TOURNAMENT_SLUG = 'walk-cup-finished'


def setup():
    if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
        raise SystemExit('walk_studios.py writes fixtures: local sqlite only.')
    owner = Users.objects.filter(username='walk_organiser').first()
    if owner is None:
        raise SystemExit('Run tools/walk_event.py --setup first: walk_organiser is missing.')

    partner, _ = Partner.objects.get_or_create(
        owner=owner, slug='walk-studio',
        defaults=dict(name='Walk Studio', contact_name='Walk Organiser',
                      contact_email='walk_organiser@vent.test', status='approved',
                      approved_scopes=['tournaments:read']))
    if partner.status != 'approved':
        partner.status = 'approved'
        partner.save(update_fields=['status'])

    title = Games.objects.filter(game_title='Free Fire').first()
    game = StudioGame.objects.filter(slug=GAME_SLUG).first()
    if game is None:
        game = StudioGame(partner=partner, name='Walk Arena', game=title, created_by=owner,
                          session_webhook_url='http://localhost:9911/vent/sessions')
        secret = game.set_secret()
        game.save()
        print('made %s, signing secret %s' % (game.slug, secret))
    else:
        print('found %s' % game.slug)

    # A delivery with a known ref, for the resend case.
    from vent_partners.models import StudioDelivery
    StudioDelivery.objects.get_or_create(
        ref=DELIVERY_REF,
        defaults=dict(game=game, kind='event', event='ping', url=game.session_webhook_url,
                      payload={'test': True}, status='delivered'))
    print('delivery %s' % DELIVERY_REF)

    # A's own game account link and a code, for the linking cases.
    from datetime import timedelta
    from django.utils import timezone
    from vent_partners.models import StudioAccountLink, StudioLinkCode
    link, _ = StudioAccountLink.objects.get_or_create(
        ref=LINK_REF, defaults=dict(game=game, user=owner, external_id='walk-account',
                                    external_name='Walk Organiser', method='code'))
    StudioLinkCode.objects.get_or_create(
        ref=CODE_REF, defaults=dict(game=game, code_hash='0' * 64, external_id='walk-account',
                                    expires_at=timezone.now() + timedelta(days=3650)))
    print('link %s, code %s' % (LINK_REF, CODE_REF))

    # A game session on one of A's walk tournaments, for the session cases.
    from vent_partners.models import StudioSession, StudioTournamentLink
    from vent_tournament.models import BracketMatch, Tournament
    cup = Tournament.objects.filter(slug=TOURNAMENT_SLUG).first()
    match = BracketMatch.objects.filter(tournament=cup).first() if cup else None
    if match is None:
        print('no match on %s yet: the session case has no record behind it' % TOURNAMENT_SLUG)
        return
    tie, _ = StudioTournamentLink.objects.get_or_create(
        tournament=cup, defaults=dict(game=game, enabled_by=owner, active=False))
    StudioSession.objects.get_or_create(
        ref=SESSION_REF, defaults=dict(game=game, link=tie, match=match, state='cancelled'))
    print('session %s on %s' % (SESSION_REF, TOURNAMENT_SLUG))


if __name__ == '__main__':
    if '--setup' in sys.argv:
        setup()
    else:
        print(__doc__)
