"""The Swiss with a top cut preset of P3 W3c, built as a plan.

Everybody plays a fixed number of Swiss rounds, then the top `cut` places
play a single elimination bracket. Built as a plan by
`plan_presets.swiss_top_cut`: one Swiss stage that sends the top `cut` up,
and the bracket they go into, with one line carrying their places. The Swiss
has no win or loss early exit, so every entrant plays the same rounds.
"""
from django.test import TestCase

from . import plan_presets, progressions, stages
from .tests import client_for
from .tests_stage_engine import field


class SwissTopCutPresetTests(TestCase):
    def test_swiss_then_a_bracket_of_the_top_cut(self):
        plan = plan_presets.swiss_top_cut(32, None, 8)
        self.assertEqual([(s['format'], s['kind'], s['round']) for s in plan['stages']],
                         [('swiss', 'round', 1), ('single_elimination', 'top', 2)])
        self.assertEqual(plan['stages'][0]['advances'], 8)
        self.assertEqual(plan['stages'][0]['settings'], {'rounds': 5})
        self.assertEqual(plan['stages'][1]['settings'], {})
        self.assertEqual([(l['source'], l['target'], l['place_from'], l['place_to'],
                           l['carry_points']) for l in plan['lines']],
                         [(0, 1, 1, 8, False)])
        self.assertEqual([r['entrants'] for r in plan['rounds']], [32, 8])

    def test_rounds_default_to_the_fewest_that_separate_the_field(self):
        for entrants, rounds in ((8, 3), (9, 4), (16, 4), (17, 5)):
            with self.subTest(entrants=entrants):
                plan = plan_presets.swiss_top_cut(entrants, None, 4)
                self.assertEqual(plan['stages'][0]['settings'], {'rounds': rounds})

    def test_a_cut_that_is_not_four_eight_sixteen_or_thirtytwo_is_refused(self):
        with self.assertRaises(plan_presets.PresetError) as caught:
            plan_presets.swiss_top_cut(32, 5, 6)
        self.assertEqual((caught.exception.code, caught.exception.field),
                         ('OUT_OF_RANGE', 'cut'))

    def test_a_cut_as_big_as_the_field_is_refused(self):
        for entrants, cut in ((8, 8), (16, 16), (12, 16)):
            with self.subTest(entrants=entrants, cut=cut):
                with self.assertRaises(plan_presets.PresetError) as caught:
                    plan_presets.swiss_top_cut(entrants, None, cut)
                self.assertEqual((caught.exception.code, caught.exception.field),
                                 ('OUT_OF_RANGE', 'cut'))

    def test_every_plan_it_writes_is_a_plan_the_server_accepts(self):
        for n, cut in ((8, 4), (16, 4), (16, 8), (32, 8), (64, 16)):
            with self.subTest(n=n, cut=cut):
                plan = plan_presets.swiss_top_cut(n, None, cut)
                cleaned = stages.plan(plan['stages'], participants=n, lines=True)
                progressions.clean(plan['lines'], cleaned, participants=n)


class SwissTopCutEndpointTests(TestCase):
    def test_the_planner_asks_the_server_for_the_swiss_with_a_top_cut(self):
        t, _creator, _regs = field(8, 'round_robin')
        res = self.client.get('/tournament/%s/stages/preset/' % t.tournament_id,
                              {'kind': 'swiss_top_cut', 'entrants': 16, 'cut': 4})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()['data']
        self.assertEqual([s['kind'] for s in body['stages']], ['round', 'top'])
        self.assertEqual(body['stages'][0]['settings'], {'rounds': 4})
        self.assertEqual(len(body['lines']), 1)


class CardGameTieBreakTests(TestCase):
    def test_the_swiss_breaks_ties_on_opponents_win_percentage(self):
        from . import ranking
        plan = plan_presets.swiss_top_cut(32, None, 8)
        rules = ranking.clean_rules('swiss', plan['stages'][0]['rules'])
        self.assertEqual(rules['tiebreakers'], ['opponents_win_pct', 'opp_opp_win_pct'])
