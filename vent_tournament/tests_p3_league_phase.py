"""The league phase from pots (P3 W3a, design section 11, research 3.3).

The field is dealt into pots by seed and everybody meets the same number of
opponents from every pot, about half at home, nobody twice. Asked for with
the rest of the new modes on 16 October 2026 (inbox 484) and to be live
"today" on 24 October 2026.
"""
from collections import Counter

from django.test import SimpleTestCase, TestCase

from . import stage_engine, stage_settings
from .services.bracket import BracketError, league_phase_pairings
from .tests_stage_engine import add_stage, draw, field, play_out


class Entrant:
    def __init__(self, seed):
        self.seed = seed


class PairingsTests(SimpleTestCase):
    def check(self, n, pots, per_pot):
        players = [Entrant(i + 1) for i in range(n)]
        out = league_phase_pairings(players, pots, per_pot)
        size = n // pots
        pot_of = {id(p): i // size for i, p in enumerate(players)}
        met = Counter()
        home = Counter()
        pairs = set()
        for r, a, b in out:
            pairs.add(frozenset((id(a), id(b))))
            home[id(a)] += 1
            met[(id(a), pot_of[id(b)])] += 1
            met[(id(b), pot_of[id(a)])] += 1
        self.assertEqual(len(pairs), len(out), 'a pair met twice')
        for p in players:
            for q in range(pots):
                self.assertEqual(met[(id(p), q)], per_pot)
        busy = Counter((r, id(x)) for r, a, b in out for x in (a, b))
        self.assertEqual(max(busy.values()), 1, 'somebody plays twice in a round')
        return out, home

    def test_the_uefa_shape(self):
        out, home = self.check(36, 4, 2)
        self.assertEqual(len(out), 144)
        self.assertEqual(set(home.values()), {4})

    def test_other_shapes(self):
        for n, pots, per_pot in ((16, 4, 2), (8, 4, 1), (12, 3, 3), (24, 3, 2)):
            with self.subTest(n=n, pots=pots, per_pot=per_pot):
                self.check(n, pots, per_pot)

    def test_a_field_that_does_not_divide_is_refused_with_a_code(self):
        with self.assertRaises(BracketError) as caught:
            league_phase_pairings([Entrant(i) for i in range(10)], 4, 1)
        self.assertEqual(caught.exception.code, 'league_phase_pots')

    def test_more_opponents_than_a_pot_holds_is_refused_with_a_code(self):
        for n, pots, per_pot in ((8, 4, 2), (12, 4, 1)):
            with self.subTest(n=n, pots=pots, per_pot=per_pot):
                with self.assertRaises(BracketError) as caught:
                    league_phase_pairings([Entrant(i) for i in range(n)], pots, per_pot)
                self.assertEqual(caught.exception.code, 'league_phase_per_pot')

    def test_settings_default_to_four_pots_of_two(self):
        self.assertEqual(stage_settings.clean('league_phase', {})['pots'], 4)
        self.assertEqual(stage_settings.clean('league_phase', {})['per_pot'], 2)


class DrawnAndPlayedTests(TestCase):
    def test_sixteen_in_four_pots_play_to_one_first(self):
        t, creator, regs = field(16)
        stage = add_stage(t, 0, 'league_phase', settings={'pots': 4, 'per_pot': 2})
        draw(stage, creator)
        stage.refresh_from_db()
        self.assertEqual(stage.matches.count(), 64)
        play_out(stage)
        stage.refresh_from_db()
        self.assertTrue(stage_engine.stage_finished(stage))
        firsts = [r for r in stage_engine.standings(stage) if r['rank'] == 1]
        self.assertEqual(len(firsts), 1)
        self.assertEqual(firsts[0]['seed'], 1)

    def test_a_field_that_does_not_divide_is_refused_at_the_draw(self):
        t, creator, regs = field(10)
        stage = add_stage(t, 0, 'league_phase', settings={'pots': 4, 'per_pot': 1})
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            draw(stage, creator)
        self.assertEqual(caught.exception.code, 'LEAGUE_PHASE_POTS')
