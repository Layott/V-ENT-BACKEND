"""Game sessions: when a match is ready, which games may run it, and what the
studio is asked to create (P6d).

Design section 14, item 3: "when a match needs a lobby, V-ENT calls the
studio's create game session webhook with the players, teams, settings and an
idempotency key". And the organiser connects their tournament to a game.
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games
from vent_partners import studio_sessions, studio_usage
from vent_partners.models import (
    Partner, StudioAccountLink, StudioDelivery, StudioGame, StudioMonthlyPlayer, StudioSession,
    StudioTournamentLink,
)
from vent_partners.tests_self_serve import a_user
from vent_partners.tests_studio_delivery import POST, PUBLIC, answer
from vent_tournament.models import BracketMatch, Tournament, TournamentRegistration, TournamentStaff


def a_studio(owner, slug, title, **extra):
    partner = Partner.objects.create(
        name='Studio %s' % slug, slug=slug, owner=owner, contact_name='Ada',
        contact_email='%s@studio.test' % slug, status='approved', approved_scopes=['tournaments:read'])
    defaults = dict(partner=partner, name='Game %s' % slug, game=title, created_by=owner,
                    session_webhook_url='https://api.%s.test/vent/sessions' % slug,
                    scoring_expression='team.score')
    defaults.update(extra)
    game = StudioGame(**defaults)
    game.set_secret()
    game.save()
    return game


class Fixture:
    """A published knockout with two players in round one."""

    def build(self, title_name='Free Fire'):
        self.title = Games.objects.get_or_create(game_title=title_name)[0]
        self.organiser, self.org_auth = a_user('org_%s' % title_name[:4].lower().replace(' ', ''))
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Studio Cup', tournament_game=self.title,
            tournament_creator=self.organiser, start_date_and_time=now,
            end_date_and_time=now + timedelta(days=1), tournament_visibility='public',
            is_draft=False, bracket_type='Single Elimination')
        self.p1, _ = a_user('side_one')
        self.p2, _ = a_user('side_two')
        self.reg1 = TournamentRegistration.objects.create(tournament=self.tournament, user=self.p1,
                                                          status='confirmed')
        self.reg2 = TournamentRegistration.objects.create(tournament=self.tournament, user=self.p2,
                                                          status='confirmed')
        self.match = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=self.reg1, participant_2=self.reg2, status='scheduled')
        self.studio_owner, _ = a_user('studio_boss')
        self.game = a_studio(self.studio_owner, 'live-arena', self.title,
                             mode='live', live_status='approved')
        return self


@override_settings(STUDIO_DELIVERY_INLINE=True)
class ReadinessTests(TestCase):
    def setUp(self):
        self.f = Fixture().build()
        self.now = timezone.now()

    def ready(self):
        self.f.match.refresh_from_db()
        return studio_sessions.is_ready(self.f.match, self.now)

    def test_a_match_with_both_sides_is_ready(self):
        self.assertTrue(self.ready())

    def test_a_match_missing_a_side_is_not(self):
        BracketMatch.objects.filter(pk=self.f.match.pk).update(participant_2=None)
        self.assertFalse(self.ready())

    def test_a_finished_match_is_not(self):
        BracketMatch.objects.filter(pk=self.f.match.pk).update(status='completed')
        self.assertFalse(self.ready())

    def test_check_in_must_be_done_when_the_match_uses_it(self):
        BracketMatch.objects.filter(pk=self.f.match.pk).update(
            check_in_deadline=self.now + timedelta(minutes=10), checked_in_p1_at=self.now)
        self.assertFalse(self.ready())
        BracketMatch.objects.filter(pk=self.f.match.pk).update(checked_in_p2_at=self.now)
        self.assertTrue(self.ready())

    def test_a_match_hours_away_is_not_ready_but_one_minutes_away_is(self):
        BracketMatch.objects.filter(pk=self.f.match.pk).update(scheduled_at=self.now + timedelta(hours=2))
        self.assertFalse(self.ready())
        BracketMatch.objects.filter(pk=self.f.match.pk).update(scheduled_at=self.now + timedelta(minutes=4))
        self.assertTrue(self.ready())

    def test_a_draft_tournament_is_not_ready(self):
        Tournament.objects.filter(pk=self.f.tournament.pk).update(is_draft=True)
        self.assertFalse(self.ready())


@override_settings(STUDIO_DELIVERY_INLINE=True)
class AvailableGameTests(TestCase):
    def setUp(self):
        self.f = Fixture().build()

    def test_live_games_of_the_same_title_are_offered(self):
        self.assertEqual(studio_sessions.available_games(self.f.organiser, self.f.tournament),
                         [self.f.game])

    def test_another_title_is_not(self):
        other = Games.objects.get_or_create(game_title='PUBG Mobile')[0]
        a_studio(self.f.studio_owner, 'pubg-arena', other, mode='live', live_status='approved')
        self.assertEqual(studio_sessions.available_games(self.f.organiser, self.f.tournament),
                         [self.f.game])

    def test_a_sandbox_game_is_offered_only_to_its_own_studio(self):
        sandbox = a_studio(self.f.organiser, 'own-sandbox', self.f.title)
        mine = studio_sessions.available_games(self.f.organiser, self.f.tournament)
        self.assertIn(sandbox, mine)
        stranger, _ = a_user('another_organiser')
        self.assertNotIn(sandbox, studio_sessions.available_games(stranger, self.f.tournament))


@override_settings(STUDIO_DELIVERY_INLINE=True)
class CreateSessionTests(TestCase):
    def setUp(self):
        self.f = Fixture().build()
        self.link = StudioTournamentLink.objects.create(tournament=self.f.tournament, game=self.f.game,
                                                        settings={'mode': 'Classic'})
        StudioAccountLink.objects.create(game=self.f.game, user=self.f.p1, external_id='acct-one',
                                         external_name='One#1', method='code')

    def test_create_asks_the_studio_for_a_lobby(self):
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(202)) as post:
            with self.captureOnCommitCallbacks(execute=True):
                session = studio_sessions.create(self.link, self.f.match, 1, 1)
        self.assertRegex(session.ref, r'^gs_[0-9a-f]{16}$')
        self.assertEqual(session.idempotency_key, 'vent-%s' % session.ref)
        self.assertEqual(session.state, 'creating')
        sent = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(sent['type'], 'create_game_session')
        data = sent['data']
        self.assertEqual(data['session'], session.ref)
        self.assertEqual(data['idempotency_key'], session.idempotency_key)
        self.assertEqual(data['game_number'], 1)
        self.assertEqual(data['settings'], {'mode': 'Classic'})
        self.assertEqual(data['match']['best_of'], 1)
        self.assertTrue(data['callback'].endswith('/api/v1/studio/sessions/%s/' % session.ref))
        one, two = data['lineups']
        self.assertEqual(one['side'], 1)
        self.assertEqual(one['players'], [{'username': 'side_one', 'account_id': 'acct-one',
                                           'display_name': 'One#1', 'linked': True}])
        self.assertEqual(two['players'], [{'username': 'side_two', 'account_id': None,
                                           'display_name': None, 'linked': False}])

    def test_a_side_is_named_by_the_full_name_not_the_username(self):
        # Every screen shows a person by match_shape.names: the full name,
        # never the bare username (display names catcher, 17 October 2026).
        self.f.p1.full_name = 'Side One Person'
        self.f.p1.save(update_fields=['full_name'])
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(202)) as post:
            with self.captureOnCommitCallbacks(execute=True):
                studio_sessions.create(self.link, self.f.match, 1, 1)
        one, _ = json.loads(post.call_args.kwargs['data'])['data']['lineups']
        self.assertEqual(one['name'], 'Side One Person')

    def test_linked_players_are_counted_once_a_month(self):
        studio_sessions.create(self.link, self.f.match, 1, 1)
        studio_sessions.create(self.link, self.f.match, 1, 2)
        self.assertEqual(StudioMonthlyPlayer.objects.filter(game=self.f.game).count(), 1)
        rows = studio_usage.monthly(self.f.game)
        self.assertEqual(rows[0]['active_players'], 1)
        self.assertEqual(rows[0]['sessions'], 2)


@override_settings(STUDIO_DELIVERY_INLINE=True)
class ConnectTests(TestCase):
    def setUp(self):
        self.f = Fixture().build()
        self.base = '/partners/studios/tournament/%s/' % self.f.tournament.slug

    def post(self, path, body=None, auth=None):
        return self.client.post(self.base + path, data=body or {}, content_type='application/json',
                                **(auth or self.f.org_auth))

    def test_the_organiser_sees_what_may_run_the_tournament(self):
        res = self.client.get(self.base, **self.f.org_auth)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        self.assertIsNone(data['link'])
        self.assertEqual([g['slug'] for g in data['available']], [self.f.game.slug])
        self.assertEqual(data['sessions'], [])

    def test_the_organiser_connects_a_live_game(self):
        res = self.post('connect/', {'game': self.f.game.slug, 'settings': {}})
        self.assertEqual(res.status_code, 200, res.content)
        link = StudioTournamentLink.objects.get(tournament=self.f.tournament)
        self.assertEqual((link.game, link.active, link.enabled_by), (self.f.game, True, self.f.organiser))

    def test_a_game_of_another_title_is_refused(self):
        other = Games.objects.get_or_create(game_title='PUBG Mobile')[0]
        pubg = a_studio(self.f.studio_owner, 'pubg-two', other, mode='live', live_status='approved')
        res = self.post('connect/', {'game': pubg.slug})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'STUDIO_NOT_AVAILABLE')

    def test_settings_are_checked_against_the_game_s_settings_schema(self):
        self.f.game.settings_schema = {'type': 'object', 'required': ['mode'],
                                       'properties': {'mode': {'enum': ['Classic', 'Clash']}}}
        self.f.game.save()
        bad = self.post('connect/', {'game': self.f.game.slug, 'settings': {'mode': 'Chaos'}})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()['code'], 'STUDIO_SETTINGS_INVALID')
        self.assertEqual(bad.json()['data']['errors'], [{'path': '$.mode', 'code': 'ENUM'}])
        good = self.post('connect/', {'game': self.f.game.slug, 'settings': {'mode': 'Clash'}})
        self.assertEqual(good.status_code, 200)

    def test_a_scorekeeper_or_a_stranger_gets_the_missing_answer(self):
        keeper, keeper_auth = a_user('the_keeper')
        TournamentStaff.objects.create(tournament=self.f.tournament, user=keeper)
        _, stranger_auth = a_user('the_stranger')
        for auth in (keeper_auth, stranger_auth):
            self.assertEqual(self.client.get(self.base, **auth).status_code, 404)
            res = self.post('connect/', {'game': self.f.game.slug}, auth=auth)
            self.assertEqual(res.status_code, 404)
            self.assertEqual(res.json()['code'], 'TOURNAMENT_NOT_FOUND')

    def test_signed_out_is_refused(self):
        self.assertEqual(self.client.get(self.base).status_code, 401)

    def test_disconnecting_cancels_open_sessions(self):
        self.post('connect/', {'game': self.f.game.slug})
        link = StudioTournamentLink.objects.get(tournament=self.f.tournament)
        session = studio_sessions.create(link, self.f.match, 1, 1)
        StudioSession.objects.filter(pk=session.pk).update(state='created', studio_session_id='lobby-9')
        res = self.post('disconnect/')
        self.assertEqual(res.status_code, 200, res.content)
        link.refresh_from_db()
        self.assertFalse(link.active)
        session.refresh_from_db()
        self.assertEqual(session.state, 'cancelling')
        self.assertTrue(StudioDelivery.objects.filter(session=session, kind='cancel_game_session').exists())
