"""The production studio for an event.

The studio was tournament-only. An organiser streaming an event had the
upload-your-own overlays and none of the studio: no console, no now-and-next,
no doors count, no sponsor wall. The audit of 2 September recorded it as the
gap the parity checker had no row for; `tools/check-parity.py` now has one.

The same three routes a tournament has, under /event/<ref>/studio/, the same
feed by token, the same permission function. What differs is the kinds of
graphic and the data behind them, which is the whole point of one studio for
both: nothing else may differ.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, Users, UserWallet
from vent_event.models import Event, EventSession, Sponsor
from vent_tournament.models import BroadcastElement, BroadcastSession, Tournament


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('e-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('ew%s' % name)[:10], user=user, wallet_balance=0,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


@override_settings(FRONTEND_URL='https://v-ent.co')
class EventStudioTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('evsA')
        self.other, self.other_auth = a_user('evsB')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Lagos Anime Con', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=Decimal('0'),
            reg_start_date=now, reg_end_date=now,
            event_date=now.date(), start_time=now.time(), end_time=now.time(),
            start_date=now - timedelta(hours=1), end_date=now + timedelta(hours=8),
            venue_name='Landmark Centre')
        EventSession.objects.create(
            event=self.event, title='Cosplay finals', stage='Main hall',
            starts_at=now - timedelta(minutes=10), ends_at=now + timedelta(minutes=50))
        EventSession.objects.create(
            event=self.event, title='AMV screening', stage='Screen 2',
            starts_at=now + timedelta(hours=1), ends_at=now + timedelta(hours=2))
        Sponsor.objects.create(event=self.event, name='MTN')
        self.ref = self.event.slug or self.event.event_id
        self.url = '/event/%s/studio/sessions/' % self.ref

    def start(self, name='Day 1', auth=None):
        return self.client.post(
            self.url, data={'name': name}, content_type='application/json',
            **(auth if auth is not None else self.auth))

    def element(self, session_id, kind, body, auth=None):
        return self.client.post(
            '%s%d/element/%s/' % (self.url, session_id, kind), data=body,
            content_type='application/json',
            **(auth if auth is not None else self.auth))

    # ------------------------------------------------------------- sessions

    def test_starting_a_broadcast_hands_back_event_urls(self):
        res = self.start()
        self.assertEqual(res.status_code, 200, res.content[:400])
        s = res.json()['data']['session']
        self.assertEqual(s['kind'], 'event')
        self.assertTrue(s['is_live'])
        self.assertEqual(s['event']['name'], 'Lagos Anime Con')
        # The event's graphics, not the bracket's.
        self.assertIn('now_next', s['urls'])
        self.assertIn('programme', s['urls'])
        self.assertIn('doors', s['urls'])
        self.assertIn('sponsors', s['urls'])
        self.assertNotIn('bracket', s['urls'])
        self.assertNotIn('scorebar', s['urls'])
        # Element pages are frontend routes; the feed is an API route.
        self.assertTrue(s['urls']['now_next'].startswith('https://v-ent.co/studio/'))
        self.assertTrue(s['feed'].endswith('/feed/'))

    def test_the_session_belongs_to_the_event_and_nothing_else(self):
        self.start()
        session = BroadcastSession.objects.get()
        self.assertEqual(session.event_id, self.event.event_id)
        self.assertIsNone(session.tournament_id)
        self.assertEqual(session.kind, 'event')
        self.assertEqual(session.owner, self.event)

    def test_a_stranger_is_refused_with_the_event_code(self):
        res = self.start(auth=self.other_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NOT_ORGANIZER')
        res = self.client.get(self.url, **self.other_auth)
        self.assertEqual(res.status_code, 403)

    def test_signed_out_is_refused(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 403)

    def test_the_kinds_offered_are_the_events(self):
        res = self.client.get(self.url, **self.auth)
        kinds = [k['kind'] for k in res.json()['data']['kinds']]
        self.assertEqual(kinds, [k for k, _ in BroadcastElement.EVENT_KINDS])

    def test_starting_again_ends_the_previous_one(self):
        first = self.start('Morning').json()['data']['session']['id']
        second = self.start('Afternoon').json()['data']['session']['id']
        self.assertNotEqual(first, second)
        self.assertEqual(BroadcastSession.objects.get(pk=first).status, 'ended')
        self.assertEqual(BroadcastSession.objects.get(pk=second).status, 'live')

    # ------------------------------------------------------------- elements

    def test_a_tournament_graphic_is_refused_on_an_event(self):
        sid = self.start().json()['data']['session']['id']
        res = self.element(sid, 'bracket', {'active': True})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'UNKNOWN_ELEMENT')
        self.assertIn('an event', res.json()['message'])

    def test_putting_now_and_next_on_air(self):
        sid = self.start().json()['data']['session']['id']
        res = self.element(sid, 'now_next', {'active': True})
        self.assertEqual(res.status_code, 200, res.content[:300])
        el = res.json()['data']['session']['elements']['now_next']
        self.assertTrue(el['active'])

    # ----------------------------------------------------------------- feed

    def test_the_feed_carries_the_programme_the_doors_and_the_sponsors(self):
        s = self.start().json()['data']['session']
        self.element(s['id'], 'now_next', {'active': True})
        self.element(s['id'], 'sponsors', {'active': True, 'payload': {'title': 'Thanks'}})
        res = self.client.get('/studio/%s/feed/' % BroadcastSession.objects.get().token)
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        self.assertEqual(data['kind'], 'event')
        self.assertEqual(data['event']['name'], 'Lagos Anime Con')
        self.assertEqual(data['event']['now_on'], 'Cosplay finals')
        self.assertEqual(data['event']['room'], 'Main hall')
        self.assertEqual(data['event']['next_on'], 'AMV screening')
        self.assertEqual([p['title'] for p in data['programme']],
                         ['Cosplay finals', 'AMV screening'])
        self.assertEqual([x['name'] for x in data['sponsors']], ['MTN'])
        self.assertTrue(data['elements']['now_next']['active'])
        self.assertEqual(data['elements']['sponsors']['payload'], {'title': 'Thanks'})
        self.assertFalse(data['elements']['programme']['active'])
        # No bracket keys on an event feed, and no event keys on a tournament's.
        self.assertNotIn('teams', data)
        self.assertNotIn('now_next', data['elements'].keys() - set(dict(BroadcastElement.EVENT_KINDS)))

    def test_the_feed_is_public_and_the_version_moves(self):
        s = self.start().json()['data']['session']
        token = BroadcastSession.objects.get().token
        before = self.client.get('/studio/%s/feed/' % token).json()['data']['version']
        self.element(s['id'], 'doors', {'active': True})
        after = self.client.get('/studio/%s/feed/' % token).json()['data']['version']
        self.assertNotEqual(before, after)

    def test_ending_retires_the_feed_for_an_event_too(self):
        s = self.start().json()['data']['session']
        token = BroadcastSession.objects.get().token
        self.client.post('%s%d/' % (self.url, s['id']), data={'end': True},
                         content_type='application/json', **self.auth)
        data = self.client.get('/studio/%s/feed/' % token).json()['data']
        self.assertTrue(data['retired'])
        self.assertEqual(data['kind'], 'event')
        self.assertFalse(any(e['active'] for e in data['elements'].values()))


@override_settings(FRONTEND_URL='https://v-ent.co')
class TournamentFeedSponsorsTests(TestCase):
    """The tournament side of the same foundation: sponsors on its feed."""

    def setUp(self):
        self.organiser, self.auth = a_user('spnA')
        game = Games.objects.create(game_title='EA FC 26')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Rivalry Series', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=4),
            tournament_visibility='public', tournament_type='physical',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False)
        from vent_tournament.models import Sponsors
        self.tournament.sponsors.add(Sponsors.objects.create(name='MTN', website='https://mtn.ng'))
        self.ref = self.tournament.slug or self.tournament.tournament_id

    def test_the_overlay_feed_names_the_sponsors(self):
        res = self.client.get('/tournament/%s/overlay-feed/' % self.ref)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['sponsors'],
                         [{'name': 'MTN', 'logo': '', 'website': 'https://mtn.ng'}])

    def test_the_studio_offers_a_sponsor_wall_and_feeds_it(self):
        url = '/tournament/%s/studio/sessions/' % self.ref
        s = self.client.post(url, data={'name': 'Day 1'}, content_type='application/json',
                             **self.auth).json()['data']['session']
        self.assertEqual(s['kind'], 'tournament')
        self.assertIn('sponsors', s['urls'])
        token = BroadcastSession.objects.get().token
        data = self.client.get('/studio/%s/feed/' % token).json()['data']
        self.assertEqual(data['kind'], 'tournament')
        self.assertEqual([x['name'] for x in data['sponsors']], ['MTN'])
        self.assertNotIn('programme', data)
