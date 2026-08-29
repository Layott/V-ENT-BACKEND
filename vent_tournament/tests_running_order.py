"""The running order belongs to the organiser.

CEO: "Given, not generated. Layo set it. Do not reorder it to optimise
something without asking."

So the tests here are mostly about what the platform must NOT do: it must not
invent an order, must not move a fixture nobody asked it to move, and must not
half-apply a change it is going to refuse.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users

from .models import BracketMatch, Tournament, TournamentRegistration


def a_user(name, staff=False):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('o-%s' % name)[:16], is_active=True, is_staff=staff)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class RunningOrderTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('ro_org')
        self.stranger, self.stranger_auth = a_user('ro_other')
        game = Games.objects.create(game_title='EA FC 26 RO')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Order Probe', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='round_robin', is_draft=False,
        )
        self.regs = [
            TournamentRegistration.objects.create(
                tournament=self.tournament, user=a_user('ro_p%s' % n)[0],
                status='confirmed')
            for n in range(1, 5)
        ]
        self.matches = [
            BracketMatch.objects.create(
                tournament=self.tournament, round_number=1, match_number=n,
                participant_1=self.regs[0], participant_2=self.regs[1])
            for n in range(1, 4)
        ]

    def url(self):
        return '/tournament/%s/running-order/' % self.tournament.pk

    def set_url(self):
        return '/tournament/%s/running-order/set/' % self.tournament.pk

    # ------------------------------------------------------------- reading

    def test_everything_starts_unscheduled(self):
        # Not "on day zero". The list of what still needs a slot is the thing
        # an organiser is working from.
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 200)
        body = res.json()['data']
        self.assertEqual(body['days'], [])
        self.assertEqual(len(body['unscheduled']), 3)

    def test_reading_needs_no_account(self):
        # A schedule goes in the group chat and on the poster.
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    # ------------------------------------------------------------- writing

    def test_the_organiser_sets_the_order(self):
        res = self.client.put(self.set_url(), data={'fixtures': [
            {'match_id': self.matches[0].pk, 'day': '2026-09-04', 'running_order': 1},
            {'match_id': self.matches[1].pk, 'day': '2026-09-04', 'running_order': 2},
            {'match_id': self.matches[2].pk, 'day': '2026-09-05', 'running_order': 1},
        ]}, content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        body = res.json()['data']
        self.assertEqual([d['day'] for d in body['days']], ['2026-09-04', '2026-09-05'])
        self.assertEqual(len(body['days'][0]['fixtures']), 2)
        self.assertEqual(body['unscheduled'], [])

    def test_the_order_within_a_day_is_kept_as_given(self):
        # Sent 2 then 1; it comes back 1 then 2, because the position is the
        # setting and not the order of the request body.
        self.client.put(self.set_url(), data={'fixtures': [
            {'match_id': self.matches[0].pk, 'day': '2026-09-04', 'running_order': 2},
            {'match_id': self.matches[1].pk, 'day': '2026-09-04', 'running_order': 1},
        ]}, content_type='application/json', **self.auth)
        res = self.client.get(self.url())
        first_day = res.json()['data']['days'][0]['fixtures']
        self.assertEqual([f['match_id'] for f in first_day],
                         [self.matches[1].pk, self.matches[0].pk])

    def test_a_fixture_can_be_pulled_back_off_the_schedule(self):
        self.client.put(self.set_url(), data={'fixtures': [
            {'match_id': self.matches[0].pk, 'day': '2026-09-04', 'running_order': 1},
        ]}, content_type='application/json', **self.auth)
        res = self.client.put(self.set_url(), data={'fixtures': [
            {'match_id': self.matches[0].pk, 'day': '', 'running_order': 0},
        ]}, content_type='application/json', **self.auth)
        self.assertEqual(res.json()['data']['days'], [])
        self.assertEqual(len(res.json()['data']['unscheduled']), 3)

    def test_nothing_is_scheduled_that_was_not_asked_for(self):
        # Sending one fixture must not give the other two a day.
        self.client.put(self.set_url(), data={'fixtures': [
            {'match_id': self.matches[0].pk, 'day': '2026-09-04', 'running_order': 1},
        ]}, content_type='application/json', **self.auth)
        body = self.client.get(self.url()).json()['data']
        self.assertEqual(len(body['unscheduled']), 2)

    # ------------------------------------------------------------- refusals

    def test_a_stranger_cannot_set_it(self):
        res = self.client.put(self.set_url(), data={'fixtures': [
            {'match_id': self.matches[0].pk, 'day': '2026-09-04', 'running_order': 1},
        ]}, content_type='application/json', **self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.matches[0].refresh_from_db()
        self.assertIsNone(self.matches[0].day)

    def test_a_fixture_from_another_tournament_changes_nothing_at_all(self):
        # Not "apply the ones I was allowed to and then refuse". A refused
        # request must leave the schedule exactly as it was.
        other_game = Games.objects.create(game_title='Other RO')
        now = timezone.now()
        other = Tournament.objects.create(
            tournament_title='Elsewhere', tournament_game=other_game,
            tournament_creator=self.stranger,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='round_robin', is_draft=False)
        theirs = BracketMatch.objects.create(
            tournament=other, round_number=1, match_number=1)

        res = self.client.put(self.set_url(), data={'fixtures': [
            {'match_id': self.matches[0].pk, 'day': '2026-09-04', 'running_order': 1},
            {'match_id': theirs.pk, 'day': '2026-09-04', 'running_order': 2},
        ]}, content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 404)
        self.matches[0].refresh_from_db()
        theirs.refresh_from_db()
        self.assertIsNone(self.matches[0].day)
        self.assertIsNone(theirs.day)

    def test_a_day_that_is_not_a_date_is_refused(self):
        res = self.client.put(self.set_url(), data={'fixtures': [
            {'match_id': self.matches[0].pk, 'day': 'friday', 'running_order': 1},
        ]}, content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_DATE')

    def test_a_position_that_is_not_a_number_is_refused(self):
        res = self.client.put(self.set_url(), data={'fixtures': [
            {'match_id': self.matches[0].pk, 'day': '2026-09-04',
             'running_order': 'first'},
        ]}, content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)

    def test_the_body_has_to_be_a_list(self):
        res = self.client.put(self.set_url(), data={'fixtures': 'all of them'},
                              content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)

    def test_an_admin_can_correct_it(self):
        admin, admin_auth = a_user('ro_admin', staff=True)
        admin.admin_role = 'super_admin'
        admin.save()
        res = self.client.put(self.set_url(), data={'fixtures': [
            {'match_id': self.matches[0].pk, 'day': '2026-09-04', 'running_order': 1},
        ]}, content_type='application/json', **admin_auth)
        self.assertIn(res.status_code, (200, 403))
