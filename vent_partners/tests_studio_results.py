"""Game session updates, results into the match, and the sweep (P6d).

Design section 14, items 3 and 4: the studio answers with a session id and
join details, the result is posted back or polled, it is "validated against
the schema, scored by the expression ... fed into the stage" and "the
organiser keeps the final say". A crashed game is replayed in the same series.

A result reaches the match only through `vent_tournament.results.decide` and
`apply`, the doors every other result uses.
"""
import json
import time
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from vent_partners import studio_delivery, studio_sessions, studio_signing
from vent_partners.models import (
    PartnerApiKey, SANDBOX_SCOPES, StudioDelivery, StudioSession, StudioTournamentLink,
)
from vent_partners.tests_studio_delivery import POST, PUBLIC, answer
from vent_partners.tests_studio_sessions import Fixture
from vent_tournament.models import BracketMatch


def result(s1, s2):
    return {'lineups': [{'side': 1, 'score': s1}, {'side': 2, 'score': s2}]}


@override_settings(STUDIO_DELIVERY_INLINE=True)
class SessionCase(TestCase):
    def setUp(self):
        self.f = Fixture().build()
        self.game = self.f.game
        self.secret = None
        self.game.signing_secret_encrypted = ''
        self.secret = self.game.set_secret()
        self.game.save()
        self.link = StudioTournamentLink.objects.create(tournament=self.f.tournament, game=self.game)
        self.partner = self.game.partner
        self.partner.approved_scopes = list(self.partner.approved_scopes) + SANDBOX_SCOPES
        self.partner.save()
        _, self.key = PartnerApiKey.issue(self.partner, scopes=SANDBOX_SCOPES)

    def new_session(self, number=1, attempt=1):
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(202)):
            return studio_sessions.create(self.link, self.f.match, number, attempt)

    def post_update(self, session, body, *, now=None, delivery='dl_inbound_%d', signed=True, key=None):
        raw = json.dumps(body).encode()
        path = '/api/v1/studio/sessions/%s/' % session.ref
        headers = {'HTTP_AUTHORIZATION': 'Bearer %s' % (key or self.key)}
        if signed:
            sig = studio_signing.outbound_headers(
                [self.secret], 'POST', 'http://testserver' + path, raw,
                delivery_ref=delivery % int(time.time() * 1000000) if '%d' in delivery else delivery,
                now=now if now is not None else time.time())
            for name, value in sig.items():
                if name == 'Content-Type':
                    continue
                headers['HTTP_' + name.upper().replace('-', '_')] = value
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(200)):
            with self.captureOnCommitCallbacks(execute=True):
                return self.client.post(path, data=raw, content_type='application/json', **headers)

    def match(self):
        return BracketMatch.objects.get(pk=self.f.match.pk)


class AnswerTests(SessionCase):
    def test_the_create_answer_is_read(self):
        reply = {'session_id': 'lobby-42', 'state': 'created',
                 'join': {'room_code': 'ROOM42', 'room_password': 'pw'}}
        with mock.patch(PUBLIC, return_value=True), \
                mock.patch(POST, return_value=answer(200, payload=reply)):
            with self.captureOnCommitCallbacks(execute=True):
                session = studio_sessions.create(self.link, self.f.match, 1, 1)
        session.refresh_from_db()
        self.assertEqual((session.state, session.studio_session_id), ('created', 'lobby-42'))
        match = self.match()
        self.assertEqual((match.room_code, match.room_password), ('ROOM42', 'pw'))

    def test_a_room_code_the_players_set_is_not_overwritten(self):
        BracketMatch.objects.filter(pk=self.f.match.pk).update(room_code='TYPED')
        session = self.new_session()
        studio_sessions.update(session, {'state': 'created', 'session_id': 'x',
                                         'join': {'room_code': 'STUDIO'}})
        self.assertEqual(self.match().room_code, 'TYPED')

    def test_a_studio_still_creating_is_asked_again(self):
        with mock.patch(PUBLIC, return_value=True), \
                mock.patch(POST, return_value=answer(200, payload={'state': 'creating'})):
            with self.captureOnCommitCallbacks(execute=True):
                session = studio_sessions.create(self.link, self.f.match, 1, 1)
        pending = StudioDelivery.objects.filter(session=session, kind='create_game_session',
                                                status='pending')
        self.assertEqual(pending.count(), 1)


class InboundTests(SessionCase):
    def test_a_finished_best_of_one_records_the_match(self):
        session = self.new_session()
        res = self.post_update(session, {'state': 'finished', 'session_id': 'lobby-1',
                                         'result': result(13, 7)})
        self.assertEqual(res.status_code, 200, res.content)
        session.refresh_from_db()
        self.assertEqual(session.scores, {'p1': 13, 'p2': 7})
        self.assertEqual(session.outcome, 'applied')
        match = self.match()
        self.assertEqual((match.status, match.score_p1, match.score_p2), ('completed', 13, 7))
        self.assertEqual(match.winner_id, self.f.reg1.pk)

    def test_finished_twice_is_idempotent(self):
        session = self.new_session()
        body = {'state': 'finished', 'result': result(13, 7)}
        first = self.post_update(session, body)
        again = self.post_update(session, body)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(self.match().score_p1, 13)

    def test_a_different_result_after_finishing_is_refused(self):
        session = self.new_session()
        self.post_update(session, {'state': 'finished', 'result': result(13, 7)})
        res = self.post_update(session, {'state': 'finished', 'result': result(1, 9)})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'SESSION_FINISHED')
        self.assertEqual(self.match().score_p1, 13)

    def test_moving_backwards_is_refused(self):
        session = self.new_session()
        self.post_update(session, {'state': 'started'})
        res = self.post_update(session, {'state': 'created'})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'STATE_NOT_ALLOWED')

    def test_a_finished_session_needs_a_result(self):
        session = self.new_session()
        res = self.post_update(session, {'state': 'finished'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'RESULT_REQUIRED')

    def test_a_result_that_breaks_the_schema_changes_nothing(self):
        self.game.result_schema = {'type': 'object', 'required': ['general']}
        self.game.save()
        session = self.new_session()
        res = self.post_update(session, {'state': 'finished', 'result': result(13, 7)})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()['code'], 'STUDIO_RESULT_INVALID')
        self.assertEqual(res.json()['data']['errors'], [{'path': '$', 'code': 'REQUIRED'}])
        session.refresh_from_db()
        self.assertNotEqual(session.state, 'finished')
        self.assertEqual(self.match().status, 'scheduled')

    def test_a_result_with_one_lineup_is_not_a_v_ent_result(self):
        session = self.new_session()
        res = self.post_update(session, {'state': 'finished', 'result': {'lineups': [{'side': 1}]}})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()['data']['errors'], [{'path': '$.lineups', 'code': 'TWO_LINEUPS'}])

    def test_a_replayed_request_is_refused(self):
        session = self.new_session()
        self.post_update(session, {'state': 'started'}, delivery='dl_same_one')
        res = self.post_update(session, {'state': 'started'}, delivery='dl_same_one')
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()['code'], 'REPLAYED')

    def test_a_stale_signature_is_refused(self):
        session = self.new_session()
        res = self.post_update(session, {'state': 'started'}, now=time.time() - 600)
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()['code'], 'SIGNATURE_STALE')

    def test_an_unsigned_post_is_refused(self):
        session = self.new_session()
        res = self.post_update(session, {'state': 'started'}, signed=False)
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()['code'], 'SIGNATURE_MISSING')

    def test_another_studio_s_key_gets_the_missing_answer(self):
        from vent_partners.tests_self_serve import a_user
        from vent_partners.tests_studio_sessions import a_studio
        owner, _ = a_user('rival_boss')
        rival = a_studio(owner, 'rival-arena', self.f.title, mode='live', live_status='approved')
        rival.partner.approved_scopes = ['tournaments:read'] + SANDBOX_SCOPES
        rival.partner.save()
        _, rival_key = PartnerApiKey.issue(rival.partner, scopes=SANDBOX_SCOPES)
        session = self.new_session()
        res = self.post_update(session, {'state': 'started'}, key=rival_key)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'SESSION_NOT_FOUND')

    def test_the_studio_reads_its_session(self):
        session = self.new_session()
        res = self.client.get('/api/v1/studio/sessions/%s/' % session.ref,
                              HTTP_AUTHORIZATION='Bearer %s' % self.key)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['session']['state'], 'creating')


class SeriesTests(SessionCase):
    def test_a_best_of_three_records_two_games_to_one(self):
        BracketMatch.objects.filter(pk=self.f.match.pk).update(best_of=3)
        first = self.new_session(1)
        self.post_update(first, {'state': 'finished', 'result': result(10, 3)})
        self.assertEqual(self.match().status, 'scheduled')
        second = self.new_session(2)
        self.post_update(second, {'state': 'finished', 'result': result(2, 10)})
        third = self.new_session(3)
        self.post_update(third, {'state': 'finished', 'result': result(10, 9)})
        match = self.match()
        self.assertEqual((match.status, match.score_p1, match.score_p2), ('completed', 2, 1))
        self.assertEqual(match.winner_id, self.f.reg1.pk)
        self.assertEqual(match.games, [{'p1': 10, 'p2': 3}, {'p1': 2, 'p2': 10}, {'p1': 10, 'p2': 9}])
        self.assertEqual(set(StudioSession.objects.filter(match=self.f.match).values_list('outcome', flat=True)),
                         {'applied'})

    def test_a_level_knockout_game_goes_to_the_organiser(self):
        session = self.new_session()
        self.post_update(session, {'state': 'finished', 'result': result(5, 5)})
        session.refresh_from_db()
        self.assertEqual(session.outcome, 'needs_organiser')
        self.assertEqual(session.error_code, 'LEVEL_NEEDS_A_DECIDER')
        self.assertEqual(self.match().status, 'scheduled')

    def test_an_expression_that_fails_goes_to_the_organiser(self):
        self.game.scoring_expression = 'team.score / team.zero'
        self.game.save()
        session = self.new_session()
        res = self.post_update(session, {'state': 'finished', 'result': result(5, 3)})
        self.assertEqual(res.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.outcome, 'needs_organiser')
        self.assertEqual(session.error_code, 'EXPRESSION_NOT_A_NUMBER')


class SweepTests(SessionCase):
    def sweep(self, now=None):
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(202)):
            with self.captureOnCommitCallbacks(execute=True):
                return studio_sessions.sweep(now=now)

    def test_the_sweep_opens_a_session_for_a_ready_match_once(self):
        self.sweep()
        self.sweep()
        sessions = StudioSession.objects.filter(match=self.f.match)
        self.assertEqual(sessions.count(), 1)
        self.assertEqual((sessions[0].game_number, sessions[0].attempt), (1, 1))

    def test_nothing_for_a_switched_off_link(self):
        self.link.active = False
        self.link.save()
        self.sweep()
        self.assertFalse(StudioSession.objects.exists())

    def test_nothing_once_the_game_is_taken_off_live(self):
        self.game.mode, self.game.live_status = 'sandbox', 'rejected'
        self.game.save()
        self.sweep()
        self.assertFalse(StudioSession.objects.exists())

    def test_the_next_game_of_a_series_is_opened(self):
        BracketMatch.objects.filter(pk=self.f.match.pk).update(best_of=3)
        self.sweep()
        first = StudioSession.objects.get(match=self.f.match)
        self.post_update(first, {'state': 'finished', 'result': result(10, 3)})
        self.sweep()
        self.assertEqual(sorted(StudioSession.objects.values_list('game_number', flat=True)), [1, 2])

    def test_a_crashed_game_is_replayed_in_the_same_series(self):
        self.sweep()
        first = StudioSession.objects.get(match=self.f.match)
        self.post_update(first, {'state': 'cancelled'})
        self.sweep()
        replay = StudioSession.objects.filter(match=self.f.match).order_by('-attempt').first()
        self.assertEqual((replay.game_number, replay.attempt), (1, 2))

    def test_three_crashes_go_to_the_organiser(self):
        self.sweep()
        for _ in range(studio_sessions.MAX_ATTEMPTS):
            latest = StudioSession.objects.filter(match=self.f.match).order_by('-attempt').first()
            self.post_update(latest, {'state': 'cancelled'})
            self.sweep()
        self.assertEqual(StudioSession.objects.filter(match=self.f.match).count(),
                         studio_sessions.MAX_ATTEMPTS)
        latest = StudioSession.objects.filter(match=self.f.match).order_by('-attempt').first()
        self.assertEqual((latest.outcome, latest.error_code), ('needs_organiser', 'STUDIO_SESSION_GAVE_UP'))

    def test_result_by_hand_cancels_the_open_session(self):
        self.sweep()
        session = StudioSession.objects.get(match=self.f.match)
        studio_sessions.update(session, {'state': 'started', 'session_id': 'lobby-7'})
        BracketMatch.objects.filter(pk=self.f.match.pk).update(status='completed', score_p1=1,
                                                                winner=self.f.reg1)
        self.sweep()
        session.refresh_from_db()
        # The studio accepted the cancel (202), so it is over, not in flight.
        self.assertEqual(session.state, 'cancelled')
        cancel = StudioDelivery.objects.get(session=session, kind='cancel_game_session')
        self.assertEqual(cancel.payload['session_id'], 'lobby-7')
        self.assertEqual(self.match().score_p1, 1)

    def test_a_cancel_the_studio_accepts_ends_the_session(self):
        session = self.new_session()
        studio_sessions.update(session, {'state': 'started'})
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(200)):
            with self.captureOnCommitCallbacks(execute=True):
                studio_sessions.cancel(session, 'match_closed')
        session.refresh_from_db()
        self.assertEqual(session.state, 'cancelled')

    def test_a_silent_started_session_is_asked_for(self):
        self.sweep()
        session = StudioSession.objects.get(match=self.f.match)
        studio_sessions.update(session, {'state': 'started', 'session_id': 'lobby-3'})
        StudioSession.objects.filter(pk=session.pk).update(
            last_seen_at=timezone.now() - timedelta(minutes=6))
        reply = {'state': 'finished', 'result': result(4, 1)}
        with mock.patch(PUBLIC, return_value=True), \
                mock.patch(POST, return_value=answer(200, payload=reply)):
            with self.captureOnCommitCallbacks(execute=True):
                studio_sessions.sweep()
        self.assertTrue(StudioDelivery.objects.filter(session=session, kind='get_game_session').exists())
        self.assertEqual(self.match().status, 'completed')

    def test_a_studio_that_never_answers_goes_to_the_organiser(self):
        self.sweep()
        session = StudioSession.objects.get(match=self.f.match)
        StudioDelivery.objects.filter(session=session).update(status='failed', last_error='GAVE_UP')
        self.sweep()
        session.refresh_from_db()
        self.assertEqual((session.outcome, session.error_code), ('needs_organiser', 'STUDIO_UNREACHABLE'))
        self.assertEqual(StudioSession.objects.filter(match=self.f.match).count(), 1)
