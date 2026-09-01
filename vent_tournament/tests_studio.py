"""The production studio: sessions, elements, and what a browser source reads.

CEO, 1 September 2026: "it'll be like a production studio for any organizer who
can pay for it."

The rules these tests exist to hold:

* **State lives on the server, not in the browser source.** OBS restarting
  mid-broadcast must not lose a graphic. So the element page is a dumb renderer
  and the feed is the truth.
* **One request feeds every element.** Six elements polling separately on a
  venue connection is six chances for the failed one to be the one on air.
* **The token is the credential**, per session, so ending a broadcast retires
  its URLs.
* **The studio never does the arithmetic.** Seeding and standings are the
  tournament's answers. A second implementation here would eventually disagree
  with the page the players are reading.
"""
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users, UserWallet

from .models import BroadcastElement, BroadcastSession, Tournament


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('s-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('sw%s' % name)[:10], user=user, wallet_balance=0,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class StudioTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('stuA')
        self.other, self.other_auth = a_user('stuB')
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
        self.ref = self.tournament.slug or self.tournament.tournament_id
        self.url = '/tournament/%s/studio/sessions/' % self.ref

    def start(self, name='Day 1', auth=None):
        return self.client.post(
            self.url, data={'name': name}, content_type='application/json',
            **(auth if auth is not None else self.auth))

    # ------------------------------------------------------------- sessions

    def test_starting_a_broadcast_hands_back_urls_to_paste(self):
        """The whole reason the feature exists is a URL somebody can paste."""
        res = self.start()
        self.assertEqual(res.status_code, 200, res.content[:400])
        s = res.json()['data']['session']
        self.assertTrue(s['is_live'])
        self.assertIn('scorebar', s['urls'])
        self.assertIn('/studio/', s['urls']['scorebar'])
        self.assertTrue(s['feed'].endswith('/feed/'))

    def test_every_shipped_element_gets_a_url(self):
        s = self.start().json()['data']['session']
        for kind, _label in BroadcastElement.KINDS:
            self.assertIn(kind, s['urls'], kind)

    def test_starting_a_second_broadcast_ends_the_first(self):
        """Two live sessions means two sets of URLs and no way to tell which."""
        first = self.start('Day 1').json()['data']['session']
        self.start('Day 2')
        self.assertEqual(
            BroadcastSession.objects.get(pk=first['id']).status, 'ended')
        self.assertEqual(
            self.tournament.broadcast_sessions.filter(status='live').count(), 1)

    def test_only_the_organiser_may_run_a_broadcast(self):
        res = self.start(auth=self.other_auth)
        self.assertEqual(res.status_code, 403, res.content[:300])
        self.assertEqual(BroadcastSession.objects.count(), 0)

    def test_signed_out_cannot_run_a_broadcast(self):
        res = self.client.post(self.url, data={}, content_type='application/json')
        self.assertEqual(res.status_code, 403)

    # ------------------------------------------------------------- elements

    def element(self, session_id, kind, body):
        return self.client.post(
            '/tournament/%s/studio/sessions/%s/element/%s/'
            % (self.ref, session_id, kind),
            data=body, content_type='application/json', **self.auth)

    def test_putting_a_graphic_on_screen(self):
        s = self.start().json()['data']['session']
        res = self.element(s['id'], 'scorebar',
                           {'active': True, 'payload': {'home': 'Nigeria',
                                                        'away': 'Ghana'}})
        self.assertEqual(res.status_code, 200, res.content[:400])
        el = res.json()['data']['session']['elements']['scorebar']
        self.assertTrue(el['active'])
        self.assertEqual(el['payload']['home'], 'Nigeria')

    def test_the_payload_is_merged_so_one_field_can_be_nudged(self):
        """An operator correcting a score mid-show should not resend everything."""
        s = self.start().json()['data']['session']
        self.element(s['id'], 'scorebar',
                     {'active': True, 'payload': {'home': 'Nigeria',
                                                  'away': 'Ghana', 'score': '0-0'}})
        res = self.element(s['id'], 'scorebar', {'payload': {'score': '2-1'}})
        el = res.json()['data']['session']['elements']['scorebar']
        self.assertEqual(el['payload']['score'], '2-1')
        self.assertEqual(el['payload']['home'], 'Nigeria')

    def test_taking_a_graphic_off_keeps_what_it_said(self):
        """So it can be put back without retyping it."""
        s = self.start().json()['data']['session']
        self.element(s['id'], 'scorebar',
                     {'active': True, 'payload': {'home': 'Nigeria'}})
        res = self.element(s['id'], 'scorebar', {'active': False})
        el = res.json()['data']['session']['elements']['scorebar']
        self.assertFalse(el['active'])
        self.assertEqual(el['payload']['home'], 'Nigeria')

    def test_an_unknown_element_is_refused(self):
        s = self.start().json()['data']['session']
        res = self.element(s['id'], 'fireworks', {'active': True})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'UNKNOWN_ELEMENT')

    def test_ending_a_broadcast_clears_every_graphic(self):
        """Or one is left on screen after the show with nobody watching."""
        s = self.start().json()['data']['session']
        self.element(s['id'], 'scorebar', {'active': True})
        self.element(s['id'], 'standings', {'active': True})

        res = self.client.post(
            '/tournament/%s/studio/sessions/%s/' % (self.ref, s['id']),
            data={'end': True}, content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        elements = res.json()['data']['session']['elements']
        self.assertFalse(elements['scorebar']['active'])
        self.assertFalse(elements['standings']['active'])

    def test_an_ended_broadcast_refuses_new_triggers(self):
        s = self.start().json()['data']['session']
        self.client.post(
            '/tournament/%s/studio/sessions/%s/' % (self.ref, s['id']),
            data={'end': True}, content_type='application/json', **self.auth)
        res = self.element(s['id'], 'scorebar', {'active': True})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'BROADCAST_ENDED')

    # ---------------------------------------------------------------- feed

    def test_the_feed_needs_no_account(self):
        """A browser source cannot sign in. That is the whole point."""
        s = self.start().json()['data']['session']
        token = s['urls']['scorebar'].split('/studio/')[1].split('/')[0]
        self.client.cookies.clear()
        res = self.client.get('/studio/%s/feed/' % token)
        self.assertEqual(res.status_code, 200, res.content[:400])

    def test_the_feed_carries_every_element_in_one_request(self):
        s = self.start().json()['data']['session']
        token = s['urls']['scorebar'].split('/studio/')[1].split('/')[0]
        self.element(s['id'], 'scorebar',
                     {'active': True, 'payload': {'home': 'Nigeria'}})

        data = self.client.get('/studio/%s/feed/' % token).json()['data']
        self.assertTrue(data['elements']['scorebar']['active'])
        self.assertFalse(data['elements']['standings']['active'])
        self.assertIn('teams', data)
        self.assertIn('tournament', data)

    def test_the_version_moves_when_something_is_triggered(self):
        """So an overlay can ask "has anything changed" without diffing."""
        s = self.start().json()['data']['session']
        token = s['urls']['scorebar'].split('/studio/')[1].split('/')[0]
        before = self.client.get('/studio/%s/feed/' % token).json()['data']['version']
        self.element(s['id'], 'scorebar', {'active': True})
        after = self.client.get('/studio/%s/feed/' % token).json()['data']['version']
        self.assertNotEqual(before, after)

    def test_an_unknown_token_is_refused(self):
        res = self.client.get('/studio/not-a-real-token/feed/')
        self.assertEqual(res.status_code, 404)

    def test_the_feed_survives_a_restart_because_state_is_on_the_server(self):
        """OBS reopening the URL must get the graphic back exactly as it was."""
        s = self.start().json()['data']['session']
        token = s['urls']['scorebar'].split('/studio/')[1].split('/')[0]
        self.element(s['id'], 'scorebar',
                     {'active': True, 'payload': {'home': 'Nigeria', 'score': '3-1'}})

        # A completely fresh client, as if the machine had been swapped.
        from django.test import Client
        fresh = Client()
        data = fresh.get('/studio/%s/feed/' % token).json()['data']
        self.assertTrue(data['elements']['scorebar']['active'])
        self.assertEqual(data['elements']['scorebar']['payload']['score'], '3-1')
