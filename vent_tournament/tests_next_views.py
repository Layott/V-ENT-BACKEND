"""The two doors onto next actions, and registration that closes when it says (P4a).

Design section 12. The envelope is `{status, data, message, code}`; the next
actions door answers everybody, the actions list door answers whoever runs
something and nobody else.
"""
import itertools
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import BracketMatch, TournamentDispute, TournamentStaff
from .tests import client_for, make_tournament, make_user, register

_N = itertools.count(4700000)


def person():
    return make_user(next(_N))


class NextActionsDoorTests(TestCase):
    def setUp(self):
        self.org = person()
        self.t = make_tournament(self.org)
        self.url = '/tournament/%s/next-actions/' % self.t.slug

    def test_a_stranger_with_no_account_is_told_to_register_and_that_it_needs_one(self):
        res = APIClient().get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], 'success')
        actions = res.json()['data']['actions']
        self.assertEqual([a['action'] for a in actions], ['register'])
        self.assertTrue(actions[0]['needs_account'])
        self.assertEqual(actions[0]['key'], 'next.register')
        self.assertIsNotNone(actions[0]['deadline'])

    def test_a_stale_token_is_a_stranger_not_an_error(self):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION='Bearer nonsense')
        res = c.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['data']['actions'][0]['needs_account'])

    def test_an_entrant_is_asked_to_report_their_match(self):
        a, b = person(), person()
        ra, rb = register(self.t, a), register(self.t, b)
        self.t.status = 'live'
        self.t.save(update_fields=['status'])
        m = BracketMatch.objects.create(tournament=self.t, round_number=1, match_number=1,
                                        participant_1=ra, participant_2=rb)
        actions = client_for(a).get(self.url).json()['data']['actions']
        self.assertEqual([x['action'] for x in actions], ['report'])
        self.assertEqual(actions[0]['match']['id'], m.id)
        self.assertEqual(actions[0]['match']['opponent'], b.username)

    def test_no_tournament_is_a_404_with_a_code(self):
        res = APIClient().get('/tournament/no-such-cup/next-actions/')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'TOURNAMENT_NOT_FOUND')

    def test_a_draft_is_a_404_for_everybody(self):
        self.t.is_draft = True
        self.t.save(update_fields=['is_draft'])
        self.assertEqual(client_for(self.org).get(self.url).status_code, 404)
        self.assertEqual(APIClient().get(self.url).status_code, 404)


class ActionsListDoorTests(TestCase):
    def setUp(self):
        self.org = person()
        self.t = make_tournament(self.org, status='live')
        a, b = person(), person()
        self.ra, self.rb = register(self.t, a), register(self.t, b)
        self.m = BracketMatch.objects.create(tournament=self.t, round_number=1, match_number=1,
                                             participant_1=self.ra, participant_2=self.rb)
        TournamentDispute.objects.create(tournament=self.t, match=self.m, raised_by=a,
                                         description='Wrong score')
        self.url = '/tournament/%s/actions-list/' % self.t.slug

    def test_anonymous_is_refused_with_401(self):
        res = APIClient().get(self.url)
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()['status'], 'error')

    def test_a_signed_in_stranger_is_refused_with_not_yours(self):
        res = client_for(person()).get(self.url)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NOT_YOURS')

    def test_an_entrant_is_refused_too(self):
        res = client_for(self.ra.user).get(self.url)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NOT_YOURS')

    def test_the_organiser_gets_the_items(self):
        res = client_for(self.org).get(self.url)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['status'], 'success')
        items = body['data']['items']
        self.assertEqual([i['kind'] for i in items], ['dispute'])
        self.assertEqual(items[0]['key'], 'todo.dispute')
        self.assertEqual(items[0]['match']['id'], self.m.id)

    def test_a_helper_gets_only_the_kinds_their_permission_reaches(self):
        helper = person()
        row = TournamentStaff.objects.create(tournament=self.t, user=helper, permissions=['place'])
        res = client_for(helper).get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['items'], [])
        row.permissions = ['report']
        row.save(update_fields=['permissions'])
        items = client_for(helper).get(self.url).json()['data']['items']
        self.assertEqual([i['kind'] for i in items], ['dispute'])

    def test_no_tournament_is_a_404(self):
        res = client_for(self.org).get('/tournament/no-such-cup/actions-list/')
        self.assertEqual(res.status_code, 404)


class RegistrationClosesWhenItSaysTests(TestCase):
    def setUp(self):
        self.org = person()
        self.t = make_tournament(self.org)
        self.player = person()

    def join(self):
        return client_for(self.player).post(
            '/tournament/join-tournament/', {'tournament_id': self.t.slug}, format='json')

    def test_after_the_close_date_is_refused_with_the_date(self):
        closes = timezone.now() - timedelta(minutes=1)
        self.t.registration_closes_at = closes
        self.t.save(update_fields=['registration_closes_at'])
        res = self.join()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'REGISTRATION_CLOSED')
        self.assertEqual(res.json()['data']['closes_at'][:16], closes.isoformat()[:16])
        self.assertFalse(self.t.registrations.filter(user=self.player).exists())

    def test_before_the_close_date_is_accepted(self):
        self.t.registration_closes_at = timezone.now() + timedelta(hours=1)
        self.t.save(update_fields=['registration_closes_at'])
        self.assertEqual(self.join().status_code, 201)
        self.assertTrue(self.t.registrations.filter(user=self.player).exists())

    def test_no_close_date_is_accepted(self):
        self.assertEqual(self.join().status_code, 201)

    def test_a_closed_status_is_still_state_conflict(self):
        self.t.status = 'registration_closed'
        self.t.save(update_fields=['status'])
        res = self.join()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'STATE_CONFLICT')

    def test_the_screen_and_the_door_agree(self):
        from . import next_actions
        self.t.registration_closes_at = timezone.now() - timedelta(minutes=1)
        self.t.save(update_fields=['registration_closes_at'])
        self.assertEqual(next_actions.for_viewer(self.player, self.t), [])
        self.assertEqual(self.join().json()['code'], 'REGISTRATION_CLOSED')


class TheDoorsSettleWhatTimeSettledTests(TestCase):
    """A report past its confirmation window is final before either door answers."""

    def reported_long_ago(self):
        from . import options as tournament_options
        from .models import MatchScore
        from .tests_stage_engine import add_stage, draw, field

        t, creator, _regs = field(4)
        t.options = tournament_options.clean({'auto_confirm_minutes': 30})
        t.save(update_fields=['options'])
        stage = add_stage(t, 0, 'single_elimination')
        draw(stage, creator)
        m = stage.matches.filter(round_number=1, participant_2__isnull=False).first()
        res = client_for(m.participant_1.user).post(
            '/tournament/match/%s/report-score/' % m.pk,
            {'score_p1': 2, 'score_p2': 0}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        MatchScore.objects.filter(match=m).update(submitted_at=timezone.now() - timedelta(minutes=90))
        return t, creator, m

    def test_the_opponent_is_not_asked_to_confirm_a_final_score(self):
        t, _creator, m = self.reported_long_ago()
        res = client_for(m.participant_2.user).get('/tournament/%s/next-actions/' % t.slug)
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('confirm_score', [a['action'] for a in res.json()['data']['actions']])
        m.refresh_from_db()
        self.assertEqual((m.status, m.winner_id), ('completed', m.participant_1_id))

    def test_the_organiser_is_not_shown_it_as_unconfirmed(self):
        t, creator, m = self.reported_long_ago()
        res = client_for(creator).get('/tournament/%s/actions-list/' % t.slug)
        self.assertEqual(res.status_code, 200)
        kinds = [(i['kind'], (i.get('match') or {}).get('id')) for i in res.json()['data']['items']]
        self.assertNotIn(('unconfirmed', m.pk), kinds)
        m.refresh_from_db()
        self.assertEqual(m.status, 'completed')

    def test_inside_the_window_it_is_still_asked(self):
        t, _creator, m = self.reported_long_ago()
        from .models import MatchScore
        MatchScore.objects.filter(match=m).update(submitted_at=timezone.now() - timedelta(minutes=5))
        res = client_for(m.participant_2.user).get('/tournament/%s/next-actions/' % t.slug)
        self.assertIn('confirm_score', [a['action'] for a in res.json()['data']['actions']])
        m.refresh_from_db()
        self.assertEqual(m.status, 'pending_opponent_confirm')
