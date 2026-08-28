"""Tournaments that run in stages.

"stages compose - group into playoff, Swiss into top cut - because that is what
real events do"

`Format.can_feed_into` recorded which combinations are possible and nothing read
it. Most of these tests are about the plan being refused BEFORE anybody plays,
because finding out halfway through a group phase that it cannot feed what comes
next is the version of this that ends up on a screenshot.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users

from . import stages
from .models import Tournament, TournamentStage


def a_user(name, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('s-%s' % name)[:16], is_active=True, **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


GROUPS_THEN_PLAYOFF = [
    {'format': 'round_robin', 'label': 'Group phase', 'advances': 2, 'groups': 4},
    {'format': 'single_elimination', 'label': 'Playoff'},
]

SWISS_THEN_CUT = [
    {'format': 'swiss', 'label': 'Swiss', 'advances': 8},
    {'format': 'single_elimination', 'label': 'Top cut'},
]


class PlanTests(TestCase):
    def test_the_two_shapes_every_major_actually_runs(self):
        self.assertEqual(len(stages.plan(GROUPS_THEN_PLAYOFF)), 2)
        self.assertEqual(len(stages.plan(SWISS_THEN_CUT)), 2)

    def test_no_stages_at_all_is_fine(self):
        """Almost every tournament is one format start to finish."""
        self.assertEqual(stages.plan([]), [])

    def test_a_format_that_cannot_feed_the_next_is_refused_and_says_what_can(self):
        with self.assertRaises(stages.StageError) as caught:
            stages.plan([
                {'format': 'round_robin', 'label': 'Groups', 'advances': 4},
                {'format': 'battle_royale', 'label': 'Finals'},
            ])
        self.assertIn('can feed into', str(caught.exception))
        self.assertEqual(caught.exception.index, 1)

    def test_a_format_that_decides_on_its_own_cannot_be_followed(self):
        with self.assertRaises(stages.StageError) as caught:
            stages.plan([
                {'format': 'battle_royale', 'label': 'Lobbies', 'advances': 8},
                {'format': 'single_elimination', 'label': 'Final'},
            ])
        self.assertIn('on its own', str(caught.exception))

    def test_a_stage_that_is_not_last_must_say_how_many_advance(self):
        with self.assertRaises(stages.StageError) as caught:
            stages.plan([
                {'format': 'round_robin', 'label': 'Groups'},
                {'format': 'single_elimination', 'label': 'Playoff'},
            ])
        self.assertEqual(caught.exception.field, 'advances')
        self.assertEqual(caught.exception.index, 0)

    def test_the_last_stage_cannot_advance_anybody(self):
        """Setting a number there is a misunderstanding, and ignoring it
        silently is how the organiser finds out at the wrong moment."""
        with self.assertRaises(stages.StageError) as caught:
            stages.plan([
                {'format': 'swiss', 'label': 'Swiss', 'advances': 8},
                {'format': 'single_elimination', 'label': 'Cut', 'advances': 4},
            ])
        self.assertEqual(caught.exception.index, 1)

    def test_advancing_fewer_than_the_next_stage_can_run_on_is_refused(self):
        with self.assertRaises(stages.StageError) as caught:
            stages.plan([
                {'format': 'swiss', 'label': 'Swiss', 'advances': 1},
                {'format': 'single_elimination', 'label': 'Cut'},
            ])
        self.assertIn('at least', str(caught.exception))

    def test_an_odd_number_into_a_format_that_needs_pairs_is_refused(self):
        """Single elimination pairs everybody in round one, so an odd field
        leaves somebody with no opponent."""
        with self.assertRaises(stages.StageError) as caught:
            stages.plan([
                {'format': 'swiss', 'label': 'Swiss', 'advances': 5},
                {'format': 'single_elimination', 'label': 'Top cut'},
            ])
        self.assertIn('even', str(caught.exception))

    def test_an_unknown_format_is_refused(self):
        with self.assertRaises(stages.StageError) as caught:
            stages.plan([{'format': 'freestyle', 'label': 'Whatever'}])
        self.assertEqual(caught.exception.field, 'format')

    def test_a_field_too_small_for_the_first_stage_is_refused(self):
        with self.assertRaises(stages.StageError) as caught:
            stages.plan(SWISS_THEN_CUT, participants=2)
        self.assertEqual(caught.exception.field, 'participants')

    def test_the_summary_reads_as_a_sentence(self):
        lines = stages.summary(stages.plan(GROUPS_THEN_PLAYOFF))
        self.assertIn('top 2 from each group', lines[0])
        self.assertIn('decides it', lines[1])


class AdvancingTests(TestCase):
    def rows(self, n, groups=0):
        return [
            {'name': 'p%s' % i, 'position': i + 1,
             'group': (i % groups) + 1 if groups else 0}
            for i in range(n)
        ]

    def test_a_single_field_takes_the_top_n(self):
        out = stages.advancing(self.rows(16), 8)
        self.assertEqual(len(out), 8)
        self.assertEqual(out[0]['name'], 'p0')

    def test_with_groups_it_is_that_many_from_EACH_group(self):
        """What an organiser means by "top two from each group". Reading it the
        other way round produces a playoff of the wrong size with nothing
        looking wrong."""
        out = stages.advancing(self.rows(16, groups=4), 2, groups=4)
        self.assertEqual(len(out), 8)

    def test_nobody_advances_from_a_final_stage(self):
        self.assertEqual(stages.advancing(self.rows(8), 0), [])


class EndpointTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('stage_owner')
        self.stranger, self.stranger_auth = a_user('stage_stranger')
        game = Games.objects.get_or_create(game_title='Stage Probe')[0]
        now = timezone.now()
        self.t = Tournament.objects.create(
            tournament_title='Stage Probe Cup', tournament_creator=self.owner,
            tournament_game=game, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            bracket_type='single_elimination',
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=3), is_draft=False,
        )

    def url(self, suffix=''):
        return '/tournament/%s/stages/%s' % (self.t.tournament_id, suffix)

    def set_plan(self, plan, auth=None):
        return self.client.put(self.url('set/'), data={'stages': plan},
                               content_type='application/json',
                               **(auth if auth is not None else self.owner_auth))

    def test_anybody_can_read_the_shape_of_an_event(self):
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['data']['single_format'])
        self.assertTrue(res.json()['data']['catalogue'])

    def test_the_owner_composes_a_plan_and_gets_it_back_in_order(self):
        res = self.set_plan(GROUPS_THEN_PLAYOFF)
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['data']['stages']
        self.assertEqual([r['order'] for r in rows], [0, 1])
        self.assertEqual([r['format'] for r in rows],
                         ['round_robin', 'single_elimination'])

    def test_a_bad_plan_points_at_the_stage_that_is_wrong(self):
        """So the wizard can highlight the row rather than saying "invalid"."""
        res = self.set_plan([
            {'format': 'round_robin', 'label': 'Groups', 'advances': 4},
            {'format': 'battle_royale', 'label': 'Finals'},
        ])
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['stage_index'], 1)

    def test_a_stranger_cannot_shape_it(self):
        res = self.set_plan(GROUPS_THEN_PLAYOFF, auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_advancing_carries_the_survivors_into_the_next_stage(self):
        self.set_plan(SWISS_THEN_CUT)
        first, second = list(self.t.stages.all())
        standings = [{'name': 'p%s' % i, 'position': i + 1} for i in range(16)]

        res = self.client.post(
            self.url('%s/advance/' % first.id), data={'standings': standings},
            content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.json()['data']['advanced']), 8)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, 'complete')
        self.assertEqual(second.status, 'running')
        self.assertEqual(len(first.advanced), 8)

    def test_advancing_twice_is_refused(self):
        self.set_plan(SWISS_THEN_CUT)
        first = self.t.stages.first()
        standings = [{'name': 'p%s' % i, 'position': i + 1} for i in range(16)]
        body = {'standings': standings}
        self.client.post(self.url('%s/advance/' % first.id), data=body,
                         content_type='application/json', **self.owner_auth)
        again = self.client.post(self.url('%s/advance/' % first.id), data=body,
                                 content_type='application/json', **self.owner_auth)
        self.assertEqual(again.status_code, 409, again.content)

    def test_the_last_stage_has_nowhere_to_advance_to(self):
        self.set_plan(SWISS_THEN_CUT)
        last = self.t.stages.last()
        res = self.client.post(
            self.url('%s/advance/' % last.id),
            data={'standings': [{'name': 'p1', 'position': 1}]},
            content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'LAST_STAGE')

    def test_an_open_dispute_stops_the_advance(self):
        """A bracket that reseeds while a dispute is open is far worse than one
        that waits."""
        from .models import TournamentDispute
        self.set_plan(SWISS_THEN_CUT)
        first = self.t.stages.first()
        TournamentDispute.objects.create(
            tournament=self.t, raised_by=self.owner, description='Wrong score',
            status='open')

        standings = [{'name': 'p%s' % i, 'position': i + 1} for i in range(16)]
        res = self.client.post(
            self.url('%s/advance/' % first.id), data={'standings': standings},
            content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'DISPUTES_OPEN')

        forced = self.client.post(
            self.url('%s/advance/' % first.id),
            data={'standings': standings, 'ignore_disputes': True},
            content_type='application/json', **self.owner_auth)
        self.assertEqual(forced.status_code, 200, forced.content)

    def test_the_plan_is_fixed_once_a_stage_has_been_played(self):
        """Re-planning around a completed stage would change what it was."""
        self.set_plan(SWISS_THEN_CUT)
        first = self.t.stages.first()
        first.status = 'complete'
        first.save(update_fields=['status'])

        res = self.set_plan(GROUPS_THEN_PLAYOFF)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'STAGES_LOCKED')

    def test_advancing_without_standings_is_refused(self):
        """What was recorded has to be what was used."""
        self.set_plan(SWISS_THEN_CUT)
        first = self.t.stages.first()
        res = self.client.post(self.url('%s/advance/' % first.id), data={},
                               content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 400, res.content)
