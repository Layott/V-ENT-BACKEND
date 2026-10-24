"""The split league preset of P3 W3a, built as a plan.

A split league is one league everybody plays, then a top league for its top
places and a bottom league for the rest, each carrying the points it won in
the first. Built as a plan by `plan_presets.split_league`: the league, and two
lines that send places 1 to `top` up and the rest down, carrying points. The
final order comes from `progressions.placement_order`, which already puts the
top league above the bottom whatever the points.
"""
from django.db import transaction
from django.test import TestCase

from . import plan_presets, progressions, stage_engine, stages
from .tests import client_for
from .tests_stage_engine import draw, field, play_out


class SplitLeaguePresetTests(TestCase):
    def test_one_league_then_top_and_bottom_carrying_points(self):
        plan = plan_presets.split_league(10, 2, 4)
        self.assertEqual([(s['format'], s['kind'], s['round']) for s in plan['stages']],
                         [('round_robin', 'round', 1), ('round_robin', 'top', 2), ('round_robin', 'bottom', 2)])
        self.assertEqual([s['settings'] for s in plan['stages']], [{'legs': 2}] * 3)
        self.assertEqual([(l['source'], l['target'], l['place_from'], l['place_to'],
                           l['carry_points']) for l in plan['lines']],
                         [(0, 1, 1, 4, True), (0, 2, 5, 10, True)])
        self.assertEqual([r['entrants'] for r in plan['rounds']], [10, 4, 6])

    def test_top_and_entrants_out_of_range_are_refused(self):
        for entrants, top, field_name in ((10, 1, 'top'), (10, 2, 'top'), (10, 9, 'top'),
                                          (5, 2, 'entrants'), (3, 2, 'entrants')):
            with self.subTest(entrants=entrants, top=top):
                with self.assertRaises(plan_presets.PresetError) as caught:
                    plan_presets.split_league(entrants, 2, top)
                self.assertEqual((caught.exception.code, caught.exception.field),
                                 ('OUT_OF_RANGE', field_name))

    def test_every_plan_it_writes_is_a_plan_the_server_accepts(self):
        for n, top in ((6, 3), (7, 3), (8, 4), (9, 3), (10, 4), (12, 3)):
            for legs in (1, 2):
                with self.subTest(n=n, top=top, legs=legs):
                    plan = plan_presets.split_league(n, legs, top)
                    cleaned = stages.plan(plan['stages'], participants=n, lines=True)
                    progressions.clean(plan['lines'], cleaned, participants=n)


class SplitLeagueEndpointTests(TestCase):
    def test_the_planner_asks_the_server_for_the_split(self):
        t, _creator, _regs = field(8, 'round_robin')
        res = self.client.get('/tournament/%s/stages/preset/' % t.tournament_id,
                              {'kind': 'split_league', 'entrants': 8, 'legs': 1, 'top': 4})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()['data']
        self.assertEqual([s['kind'] for s in body['stages']], ['round', 'top', 'bottom'])
        self.assertEqual(len(body['lines']), 2)

    def test_the_free_for_all_preset_still_answers(self):
        t, _creator, _regs = field(4, 'battle_royale')
        res = self.client.get('/tournament/%s/stages/preset/' % t.tournament_id,
                              {'kind': 'ffa_bracket', 'entrants': 30, 'lobby_size': 10, 'through': 3})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()['data']
        self.assertEqual([r['entrants'] for r in body['rounds']], [30, 9])
        self.assertEqual(len(body['stages']), 2)


class SplitLeaguePlaysThroughTests(TestCase):
    def test_top_four_and_bottom_four_carry_their_points(self):
        t, creator, regs = field(8, 'round_robin')
        plan = plan_presets.split_league(8, 1, 4)
        res = client_for(creator).put('/tournament/%s/stages/set/' % t.tournament_id,
                                      {'stages': plan['stages'], 'progressions': plan['lines']},
                                      format='json')
        self.assertEqual(res.status_code, 200, res.content)
        first, top, bottom = list(t.stages.order_by('order'))
        seed_of = {reg.id: reg.seed for reg in regs}
        draw(first, creator)
        play_out(first)
        before = {r['registration_id']: r['points'] for r in stage_engine.standings(first)}
        with transaction.atomic():
            stage_engine.advance(first, creator)
        top.refresh_from_db()
        bottom.refresh_from_db()
        self.assertEqual(sorted(seed_of[i] for i in top.entrants), [1, 2, 3, 4])
        self.assertEqual(sorted(seed_of[i] for i in bottom.entrants), [5, 6, 7, 8])
        for stage in (top, bottom):
            rows = stage_engine.standings(stage)
            self.assertEqual(len(rows), 4)
            for r in rows:
                self.assertEqual(r['points'], before[r['registration_id']])
