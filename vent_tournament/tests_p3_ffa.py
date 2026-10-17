"""The free for all bracket of P3 W1 (design section 11), built as a plan.

CEO, 16 October 2026, inbox 484, approved with the design. Section 11: "Free for
all | FFA bracket (lobbies, top N advance) | no | P3 W1", and "a new preset is
a function, not a new engine". Toornament's FFA bracket (research section 6):
"match_size players a match, match_winners go through, winners spread into
different matches", built down to one final match.

V-ENT already plays every piece of that: battle royale stages seat squads in
lobbies, score placement and kills, and a line can send "the top N of each
lobby" on, rank by rank across lobbies, into a stage that seats them by snake
so lobby winners land in different lobbies. So the free for all bracket is a
function that writes the plan: one battle royale stage per round, each joined
to the next by such a line, down to a final lobby. One model per thing: no
second lobby engine.
"""
from django.db import transaction
from django.test import TestCase

from . import plan_presets, progressions, stage_engine, stages
from .tests_battle_royale import enter
from .tests_stage_engine import field


class FfaPresetTests(TestCase):
    def test_forty_eight_in_lobbies_of_twelve_top_four(self):
        plan = plan_presets.ffa_bracket(48, lobby_size=12, through=4, maps=3, final_maps=6)
        self.assertEqual([r['entrants'] for r in plan['rounds']], [48, 16, 8])
        self.assertEqual([r['lobbies'] for r in plan['rounds']], [4, 2, 1])
        kinds = [(s['kind'], s['round']) for s in plan['stages']]
        self.assertEqual(kinds, [('round', 1), ('round', 2), ('final', 3)])
        first, second, final = plan['stages']
        self.assertEqual(first['format'], 'battle_royale')
        self.assertEqual((first['advances'], first['settings']['advance_by'],
                          first['settings']['lobby_size'], first['settings']['maps']),
                         (4, 'lobby', 12, 3))
        self.assertEqual((final['advances'], final['settings']['lobby_size'],
                          final['settings']['maps']), (0, 8, 6))
        self.assertEqual([(l['source'], l['target'], l['place_from'], l['place_to'])
                          for l in plan['lines']], [(0, 1, 1, 4), (1, 2, 1, 4)])

    def test_a_field_that_fits_one_lobby_is_just_a_final(self):
        plan = plan_presets.ffa_bracket(10, lobby_size=12, through=4)
        self.assertEqual([s['kind'] for s in plan['stages']], ['final'])
        self.assertEqual(plan['lines'], [])

    def test_a_lobby_that_sends_everybody_never_narrows(self):
        with self.assertRaises(plan_presets.PresetError) as caught:
            plan_presets.ffa_bracket(13, lobby_size=12, through=11)
        self.assertEqual((caught.exception.code, caught.exception.field),
                         ('FFA_NEVER_NARROWS', 'through'))

    def test_bad_numbers_are_refused_with_their_field(self):
        for kwargs, field_name in (({'lobby_size': 1, 'through': 1}, 'lobby_size'),
                                   ({'lobby_size': 8, 'through': 8}, 'through'),
                                   ({'lobby_size': 8, 'through': 0}, 'through'),
                                   ({'lobby_size': 8, 'through': 2, 'maps': 0}, 'maps')):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(plan_presets.PresetError) as caught:
                    plan_presets.ffa_bracket(20, **kwargs)
                self.assertEqual(caught.exception.field, field_name)

    def test_every_plan_it_writes_is_a_plan_the_server_accepts(self):
        for n in (2, 9, 12, 13, 24, 25, 48, 100, 250):
            for size, through in ((12, 4), (10, 3), (16, 8), (4, 2), (20, 10)):
                with self.subTest(n=n, size=size, through=through):
                    plan = plan_presets.ffa_bracket(n, lobby_size=size, through=through)
                    cleaned = stages.plan(plan['stages'], participants=n)
                    progressions.clean(plan['lines'], cleaned, participants=n)
                    self.assertEqual(plan['rounds'][-1]['lobbies'], 1)
                    self.assertLessEqual(plan['rounds'][-1]['entrants'], size)


class FfaEndpointTests(TestCase):
    def test_the_planner_asks_the_server_for_the_plan(self):
        t, _creator, _regs = field(4, 'battle_royale')
        res = self.client.get('/tournament/%s/stages/preset/' % t.tournament_id,
                              {'kind': 'ffa_bracket', 'entrants': 30, 'lobby_size': 10, 'through': 3})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()['data']
        self.assertEqual([r['entrants'] for r in body['rounds']], [30, 9])
        self.assertEqual(len(body['stages']), 2)

    def test_a_bad_preset_is_a_code(self):
        t, _creator, _regs = field(4, 'battle_royale')
        res = self.client.get('/tournament/%s/stages/preset/' % t.tournament_id,
                              {'kind': 'ffa_bracket', 'entrants': 13, 'lobby_size': 12, 'through': 11})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'FFA_NEVER_NARROWS')
        res = self.client.get('/tournament/%s/stages/preset/' % t.tournament_id, {'kind': 'nope'})
        self.assertEqual(res.json()['code'], 'PRESET_UNKNOWN')


class FfaPlaysThroughTests(TestCase):
    def test_two_rounds_of_lobbies_into_a_final(self):
        t, creator, regs = field(12, 'battle_royale')
        plan = plan_presets.ffa_bracket(12, lobby_size=4, through=2, maps=1, final_maps=1)
        self.assertEqual([r['entrants'] for r in plan['rounds']], [12, 6, 4])
        from . import bracket_edit
        with transaction.atomic():
            bracket_edit.save_plan(t, [dict(s, label='R%s' % s['round']) for s in plan['stages']],
                                   creator, participants=12, raw_lines=plan['lines'])
        first, second, final = list(t.stages.order_by('order'))
        with transaction.atomic():
            stage_engine.draw(first, creator)
        for current, nxt in ((first, second), (second, final)):
            current.refresh_from_db()
            for lobby in current.br_lobbies.all():
                seated = sorted((s.registration for s in lobby.seats.all()), key=lambda r: r.seed)
                enter(lobby.maps.first(), {reg: (i + 1, 0) for i, reg in enumerate(seated)}, creator)
            self.assertTrue(stage_engine.stage_finished(current))
            with transaction.atomic():
                stage_engine.advance(current, creator)
            nxt.refresh_from_db()
            self.assertIsNotNone(nxt.drawn_at)
        seated = sorted(s.registration.seed for s in final.br_lobbies.get().seats.all())
        # Round one by snake: {1, 6, 7, 12}, {2, 5, 8, 11}, {3, 4, 9, 10}; the
        # top two of each go on rank by rank (1, 2, 3, then 6, 5, 4) and are
        # seated by snake again: {1, 6, 5} and {2, 3, 4}. Seed 4 meets 2 and 3
        # there and goes out, so the final is 1, 2, 3 and 5.
        self.assertEqual(seated, [1, 2, 3, 5])
