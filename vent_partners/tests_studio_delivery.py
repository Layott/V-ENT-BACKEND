"""Events out: signed deliveries, retries with backoff, and publish.

Design section 14, item 5: "Events out ... Signed with HMAC SHA-256 over
method, path, timestamp, host and body hash; five minute replay window;
retries with backoff; a delivery log the studio can read." Section 14b: one
event list, the one P1e records, sent outside in P6.

HTTP is never made for real here: `requests.post` is replaced, so a test can
answer 200, 500, 410 or a timeout and watch what the engine does with it.
"""
import json
from datetime import timedelta
from io import StringIO
from unittest import mock

import requests
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_partners import studio_delivery, studio_events, studio_signing
from vent_partners.models import StudioDelivery, StudioGame, StudioTournamentLink
from vent_partners.tests_api import make_partner
from vent_tournament.models import Tournament

POST = 'vent_partners.studio_delivery.http.post'
PUBLIC = 'vent_partners.studio_delivery.studio_urls.resolves_public'


def answer(status=200, text='ok', payload=None):
    res = mock.Mock()
    res.status_code = status
    res.text = text if payload is None else json.dumps(payload)
    res.json = mock.Mock(return_value=payload if payload is not None else {})
    return res


def make_game(**extra):
    partner = make_partner()
    defaults = dict(partner=partner, name='Arena Clash %d' % StudioGame.objects.count(),
                    session_webhook_url='https://api.arena.test/vent/sessions',
                    events_webhook_url='https://api.arena.test/vent/events')
    defaults.update(extra)
    game = StudioGame(**defaults)
    secret = game.set_secret()
    game.save()
    return game, secret


def make_tournament(title='Open Cup'):
    title_game = Games.objects.get_or_create(game_title='Free Fire')[0]
    creator = Users.objects.get_or_create(username='cup_creator', defaults={'email': 'cc@vent.test'})[0]
    return Tournament.objects.create(
        tournament_title=title, tournament_game=title_game, tournament_creator=creator,
        start_date_and_time=timezone.now(), end_date_and_time=timezone.now(),
        tournament_visibility='public', is_draft=False)


@override_settings(STUDIO_DELIVERY_INLINE=True)
class DeliveryTests(TestCase):
    def setUp(self):
        self.game, self.secret = make_game()

    def send_now(self, delivery, **kwargs):
        return studio_delivery.attempt_ref(delivery.ref, **kwargs)

    def test_a_delivered_event_is_signed_with_the_game_s_secret(self):
        delivery = studio_delivery.queue(self.game, 'event', {'hello': 1}, event='ping')
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(200)) as post:
            self.send_now(delivery)
        args, kwargs = post.call_args
        url, body, headers = args[0], kwargs['data'], kwargs['headers']
        self.assertEqual(url, 'https://api.arena.test/vent/events')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['timeout'], studio_delivery.TIMEOUT)
        studio_signing.verify([self.secret], 'POST', '/vent/events', 'api.arena.test', headers, body,
                              now=int(headers['V-Ent-Timestamp']))
        sent = json.loads(body)
        self.assertEqual(sent['id'], delivery.ref)
        self.assertEqual(sent['type'], 'event')
        self.assertEqual(sent['event'], 'ping')
        self.assertEqual(sent['game'], self.game.slug)
        self.assertEqual(sent['environment'], 'sandbox')
        self.assertEqual(sent['data'], {'hello': 1})
        self.assertEqual(headers['V-Ent-Delivery'], delivery.ref)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, 'delivered')
        self.assertEqual(delivery.attempts, 1)
        self.assertEqual(delivery.last_status_code, 200)
        self.assertIsNotNone(delivery.delivered_at)

    def test_events_fall_back_to_the_session_address(self):
        game, _ = make_game(events_webhook_url='')
        delivery = studio_delivery.queue(game, 'event', {}, event='ping')
        self.assertEqual(delivery.url, 'https://api.arena.test/vent/sessions')

    def test_a_game_with_no_address_queues_nothing(self):
        game, _ = make_game(events_webhook_url='', session_webhook_url='')
        self.assertIsNone(studio_delivery.queue(game, 'event', {}, event='ping'))
        self.assertFalse(StudioDelivery.objects.filter(game=game).exists())

    def test_a_refs_is_opaque(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        self.assertRegex(delivery.ref, r'^dl_[0-9a-f]{16}$')

    def test_a_500_is_tried_again_thirty_seconds_later(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        now = timezone.now()
        with mock.patch(PUBLIC, return_value=True), \
                mock.patch(POST, return_value=answer(500, 'boom')):
            self.send_now(delivery, now=now)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, 'pending')
        self.assertEqual(delivery.attempts, 1)
        self.assertEqual(delivery.last_status_code, 500)
        self.assertEqual(delivery.last_error, 'HTTP_STATUS')
        self.assertEqual(delivery.response_excerpt, 'boom')
        self.assertEqual(delivery.next_attempt_at, now + timedelta(seconds=30))

    def test_the_schedule_runs_out_and_the_delivery_fails(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        schedule = studio_delivery.BACKOFF['event']
        now = timezone.now()
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(503)):
            for _ in range(len(schedule) + 1):
                self.send_now(delivery, now=now)
                delivery.refresh_from_db()
                now = delivery.next_attempt_at or now
        self.assertEqual(delivery.status, 'failed')
        self.assertEqual(delivery.attempts, len(schedule) + 1)
        self.assertEqual(delivery.last_error, 'GAVE_UP')

    def test_a_410_stops_at_once(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(410)):
            self.send_now(delivery)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, 'failed')
        self.assertEqual(delivery.last_error, 'GONE')

    def test_a_timeout_is_recorded_and_retried(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        with mock.patch(PUBLIC, return_value=True), \
                mock.patch(POST, side_effect=requests.Timeout('slow')):
            self.send_now(delivery)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, 'pending')
        self.assertEqual(delivery.last_error, 'TIMEOUT')
        self.assertIsNone(delivery.last_status_code)

    def test_an_unreachable_studio_is_recorded_and_retried(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        with mock.patch(PUBLIC, return_value=True), \
                mock.patch(POST, side_effect=requests.ConnectionError('refused')):
            self.send_now(delivery)
        delivery.refresh_from_db()
        self.assertEqual(delivery.last_error, 'UNREACHABLE')

    def test_send_time_dns_rebinding_is_refused(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        with mock.patch(PUBLIC, return_value=False), mock.patch(POST) as post:
            self.send_now(delivery)
        post.assert_not_called()
        delivery.refresh_from_db()
        self.assertEqual(delivery.last_error, 'URL_PRIVATE_HOST')
        self.assertEqual(delivery.status, 'pending')

    def test_an_unreadable_secret_sends_nothing(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        StudioGame.objects.filter(pk=self.game.pk).update(signing_secret_encrypted='broken')
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST) as post:
            self.send_now(delivery)
        post.assert_not_called()
        delivery.refresh_from_db()
        self.assertEqual(delivery.last_error, 'STUDIO_SECRET_UNREADABLE')

    def test_a_studio_answer_is_kept_short_and_readable(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        with mock.patch(PUBLIC, return_value=True), \
                mock.patch(POST, return_value=answer(500, 'x\x00y' + 'z' * 900)):
            self.send_now(delivery)
        delivery.refresh_from_db()
        self.assertEqual(len(delivery.response_excerpt), studio_delivery.EXCERPT)
        self.assertNotIn('\x00', delivery.response_excerpt)

    def test_a_delivery_that_is_not_due_is_not_sent(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        StudioDelivery.objects.filter(pk=delivery.pk).update(
            next_attempt_at=timezone.now() + timedelta(minutes=5))
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST) as post:
            self.assertIsNone(self.send_now(delivery))
        post.assert_not_called()

    def test_two_overlapping_runs_send_a_due_delivery_once(self):
        """A second cron run starting while the first is still waiting on the
        studio must not send the same delivery again: the lease holds it."""
        studio_delivery.queue(self.game, 'event', {}, event='ping')
        overlapped = []

        def slow_studio(*args, **kwargs):
            if not overlapped:
                overlapped.append(studio_delivery.run_due())
            return answer(200)

        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, side_effect=slow_studio) as post:
            studio_delivery.run_due()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(overlapped, [0])

    def test_resend_makes_a_new_delivery_with_the_same_body(self):
        first = studio_delivery.queue(self.game, 'event', {'a': 1}, event='ping')
        again = studio_delivery.resend(first)
        self.assertNotEqual(again.ref, first.ref)
        self.assertEqual(again.payload, {'a': 1})
        self.assertEqual(again.status, 'pending')

    def test_kick_sends_after_commit(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(204)) as post:
            with self.captureOnCommitCallbacks(execute=True):
                studio_delivery.kick([delivery.ref])
        post.assert_called_once()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, 'delivered')


@override_settings(STUDIO_DELIVERY_INLINE=True)
class PublishTests(TestCase):
    def setUp(self):
        self.game, self.secret = make_game()
        self.tournament = make_tournament()
        self.link = StudioTournamentLink.objects.create(tournament=self.tournament, game=self.game)

    def publish(self, kind='result_confirmed', data=None, **kwargs):
        with mock.patch(PUBLIC, return_value=True), \
                mock.patch(POST, return_value=answer(200)) as post:
            with self.captureOnCommitCallbacks(execute=True):
                count = studio_events.publish(kind, self.tournament.pk,
                                              data or {'score': '2-1'}, **kwargs)
        return count, post

    def test_a_happening_reaches_the_connected_game(self):
        count, post = self.publish()
        self.assertEqual(count, 1)
        sent = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(sent['event'], 'result_confirmed')
        self.assertEqual(sent['data']['score'], '2-1')
        self.assertEqual(sent['data']['tournament'],
                         {'slug': self.tournament.slug, 'title': 'Open Cup'})

    def test_a_tournament_with_no_studio_publishes_nothing(self):
        other = make_tournament('Lonely Cup')
        with mock.patch(POST) as post:
            self.assertEqual(studio_events.publish('result_confirmed', other.pk, {}), 0)
        post.assert_not_called()

    def test_a_switched_off_link_publishes_nothing(self):
        self.link.active = False
        self.link.save(update_fields=['active'])
        count, post = self.publish()
        self.assertEqual(count, 0)
        post.assert_not_called()

    def test_only_what_the_game_subscribed_to(self):
        self.game.subscribed_events = ['stage_closed']
        self.game.save(update_fields=['subscribed_events'])
        self.assertEqual(self.publish('result_confirmed')[0], 0)
        self.assertEqual(self.publish('stage_closed')[0], 1)

    def test_an_unknown_kind_is_not_sent(self):
        self.assertEqual(self.publish('match_teleported')[0], 0)

    def test_publish_never_raises(self):
        with mock.patch('vent_partners.studio_events.studio_delivery.queue',
                        side_effect=RuntimeError('database on fire')):
            self.assertEqual(studio_events.publish('result_confirmed', self.tournament.pk, {}), 0)

    def test_a_failure_inside_publish_leaves_the_caller_s_transaction_usable(self):
        from django.db import transaction
        with transaction.atomic():
            with mock.patch('vent_partners.studio_events.studio_delivery.queue',
                            side_effect=RuntimeError('boom')):
                studio_events.publish('result_confirmed', self.tournament.pk, {})
            # The result the caller was recording still saves.
            Users.objects.create(username='after_publish', email='ap@vent.test')
        self.assertTrue(Users.objects.filter(username='after_publish').exists())

    def test_account_events_go_to_the_game(self):
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(200)) as post:
            with self.captureOnCommitCallbacks(execute=True):
                delivery = studio_events.publish_to_game(self.game, 'account_linked', {'account_id': 'a1'})
        self.assertIsNotNone(delivery)
        self.assertEqual(json.loads(post.call_args.kwargs['data'])['event'], 'account_linked')

    def test_a_ping_arrives_whatever_the_subscriptions(self):
        self.game.subscribed_events = ['stage_closed']
        self.game.save(update_fields=['subscribed_events'])
        self.assertIsNotNone(studio_events.publish_to_game(self.game, 'ping', {}))
        self.assertIsNone(studio_events.publish_to_game(self.game, 'account_linked', {}))


@override_settings(STUDIO_DELIVERY_INLINE=True)
class RunCommandTests(TestCase):
    def setUp(self):
        self.game, _ = make_game()

    def test_the_command_sends_what_is_due(self):
        studio_delivery.queue(self.game, 'event', {}, event='ping')
        out = StringIO()
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(200)) as post:
            call_command('studio_run', stdout=out)
        post.assert_called_once()
        self.assertIn('1 delivered', out.getvalue())

    def test_a_dry_run_sends_and_writes_nothing(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        out = StringIO()
        with mock.patch(POST) as post:
            call_command('studio_run', '--dry-run', stdout=out)
        post.assert_not_called()
        delivery.refresh_from_db()
        self.assertEqual(delivery.attempts, 0)
        self.assertIn('1 due', out.getvalue())

    def test_old_finished_deliveries_are_cleared(self):
        old = studio_delivery.queue(self.game, 'event', {}, event='ping')
        StudioDelivery.objects.filter(pk=old.pk).update(
            status='delivered', created_at=timezone.now() - timedelta(days=31))
        fresh = studio_delivery.queue(self.game, 'event', {}, event='ping')
        StudioDelivery.objects.filter(pk=fresh.pk).update(status='delivered')
        with mock.patch(POST):
            call_command('studio_run', stdout=StringIO())
        self.assertFalse(StudioDelivery.objects.filter(pk=old.pk).exists())
        self.assertTrue(StudioDelivery.objects.filter(pk=fresh.pk).exists())
