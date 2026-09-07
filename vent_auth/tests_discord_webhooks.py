"""The Discord channels a tournament or an event announces into.

CEO, 7 September 2026: announcements by webhook, covering "tournament
starting, bracket updated, tickets live".

Two of these tests matter more than the rest:

`SecretTests` proves the URL never comes back out of the API. A webhook URL is
not a name, it is a capability: anybody holding it can post into that channel
as V-ENT. Returning it in a list response would hand that capability to
anything that can read one response.

`BothSidesTests` proves it works for an event as well as a tournament. Building
a capability on one of the two and forgetting the other is the fault with its
own hard rule in CLAUDE.md, and it has happened seven times on this platform.
"""
import uuid
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Users
from .models_discord import DiscordWebhook

HOOK = 'https://discord.com/api/webhooks/123456789/abcdefghijklmnop'


def a_user(name):
    u = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
        is_active=True)
    u.login_session_created_at = timezone.now()
    u.save()
    return u, {'HTTP_AUTHORIZATION': 'Bearer %s' % u.login_session_token}


def a_tournament(owner):
    from vent_auth.models import Games
    from vent_tournament.models import Tournament
    game = Games.objects.get_or_create(game_title='EA FC')[0]
    return Tournament.objects.create(
        tournament_title='Naija Cup %s' % uuid.uuid4().hex[:4],
        tournament_game=game, tournament_creator=owner,
        start_date_and_time=timezone.now(), end_date_and_time=timezone.now(),
        is_draft=False)


def an_event(owner):
    from vent_event.models import Event
    return Event.objects.create(
        name='Lagos Con %s' % uuid.uuid4().hex[:4], creator=owner,
        event_type='physical', desc='x', entry_fee=0,
        reg_start_date=timezone.now(), reg_end_date=timezone.now(),
        event_date=timezone.localdate(), start_time='10:00', end_time='18:00')


def delivered_ok():
    return mock.Mock(status_code=204)


class SecretTests(TestCase):
    """The URL goes in and never comes back out."""

    def setUp(self):
        self.client = APIClient()
        self.owner, self.auth = a_user('wh_owner')
        self.t = a_tournament(self.owner)

    def _add(self):
        with mock.patch('vent_auth.discord.http.post', return_value=delivered_ok()):
            return self.client.post(
                '/auth/discord/webhooks/tournament/%s/' % self.t.slug,
                {'url': HOOK, 'label': 'announcements'}, format='json', **self.auth)

    def test_the_url_is_never_returned_by_the_api(self):
        self._add()
        res = self.client.get(
            '/auth/discord/webhooks/tournament/%s/' % self.t.slug, **self.auth)
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()

        # Not the whole URL, and not the token half of it either.
        self.assertNotIn(HOOK, body)
        self.assertNotIn('abcdefghijklmnop', body)
        # But enough to tell two channels apart on a screen.
        self.assertIn('announcements', body)
        self.assertIn('mnop', body)

    def test_a_url_that_is_not_a_discord_webhook_is_refused(self):
        res = self.client.post(
            '/auth/discord/webhooks/tournament/%s/' % self.t.slug,
            {'url': 'https://evil.example.com/collect'}, format='json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(DiscordWebhook.objects.count(), 0)

    def test_adding_one_posts_a_test_message_so_a_dead_url_is_known_at_once(self):
        with mock.patch('vent_auth.discord.http.post',
                        return_value=mock.Mock(status_code=404, text='gone')) as post:
            res = self.client.post(
                '/auth/discord/webhooks/tournament/%s/' % self.t.slug,
                {'url': HOOK}, format='json', **self.auth)
        self.assertTrue(post.called)
        self.assertIs(res.json()['data']['delivered'], False)
        hook = DiscordWebhook.objects.get()
        self.assertIn('no longer exists', hook.last_error)


class PermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner, self.auth = a_user('wh_perm_owner')
        self.stranger, self.stranger_auth = a_user('wh_stranger')
        self.t = a_tournament(self.owner)

    def test_an_anonymous_caller_is_refused(self):
        res = self.client.get('/auth/discord/webhooks/tournament/%s/' % self.t.slug)
        self.assertIn(res.status_code, (400, 401, 403))

    def test_somebody_who_does_not_run_it_is_refused(self):
        res = self.client.get('/auth/discord/webhooks/tournament/%s/' % self.t.slug,
                              **self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_a_stranger_cannot_add_one(self):
        res = self.client.post(
            '/auth/discord/webhooks/tournament/%s/' % self.t.slug,
            {'url': HOOK}, format='json', **self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(DiscordWebhook.objects.count(), 0)


class BothSidesTests(TestCase):
    """Events get this too. Seven faults on this platform have been exactly
    'built for a tournament, forgotten for an event' or the reverse."""

    def setUp(self):
        self.client = APIClient()
        self.owner, self.auth = a_user('wh_both')

    def test_an_event_can_have_a_webhook_the_same_way(self):
        ev = an_event(self.owner)
        with mock.patch('vent_auth.discord.http.post', return_value=delivered_ok()):
            res = self.client.post('/auth/discord/webhooks/event/%s/' % ev.slug,
                                   {'url': HOOK}, format='json', **self.auth)
        self.assertEqual(res.status_code, 201)
        hook = DiscordWebhook.objects.get()
        self.assertEqual(hook.event_id, ev.event_id)
        self.assertIsNone(hook.tournament_id)


class AnnounceTests(TestCase):
    def setUp(self):
        self.owner, _ = a_user('wh_ann')
        self.t = a_tournament(self.owner)

    def test_only_channels_that_asked_for_that_kind_are_posted_to(self):
        from .views_discord_webhooks import announce
        DiscordWebhook.objects.create(tournament=self.t, url=HOOK,
                                      events=['tickets'], active=True)
        with mock.patch('vent_auth.discord.post_webhook') as post:
            announce(self.t, 'bracket', 'Result recorded')
        self.assertFalse(post.called)

        with mock.patch('vent_auth.discord.post_webhook') as post:
            announce(self.t, 'tickets', 'Tickets are live')
        self.assertTrue(post.called)

    def test_a_paused_channel_gets_nothing(self):
        from .views_discord_webhooks import announce
        DiscordWebhook.objects.create(tournament=self.t, url=HOOK,
                                      events=['bracket'], active=False)
        with mock.patch('vent_auth.discord.post_webhook') as post:
            announce(self.t, 'bracket', 'Result recorded')
        self.assertFalse(post.called)

    def test_discord_being_unreachable_does_not_raise(self):
        """The whole point of the transport. A tournament must not fail
        because a third party is down."""
        from .views_discord_webhooks import announce
        DiscordWebhook.objects.create(tournament=self.t, url=HOOK,
                                      events=['bracket'], active=True)
        with mock.patch('vent_auth.discord.http.post',
                        side_effect=OSError('network is unreachable')):
            announce(self.t, 'bracket', 'Result recorded')   # must not raise


class OwnerTests(TestCase):
    def test_a_webhook_belongs_to_exactly_one_thing(self):
        from django.core.exceptions import ValidationError
        owner, _ = a_user('wh_one')
        t = a_tournament(owner)
        ev = an_event(owner)

        both = DiscordWebhook(tournament=t, event=ev, url=HOOK)
        with self.assertRaises(ValidationError):
            both.clean()

        neither = DiscordWebhook(url=HOOK)
        with self.assertRaises(ValidationError):
            neither.clean()
