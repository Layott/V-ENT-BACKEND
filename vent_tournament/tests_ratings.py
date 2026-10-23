"""The rating maths, one engine for every rating on V-ENT (P3 W3).

Design section 11, "Ratings (Elo, Glicko, TrueSkill) | no | P3 W3", and research
catalogue 5.2. Session C's circuit rankings (P5b) read the Elo update from here
so the formula exists once; their tests pin the same numbers as the first three
here (1016 and 984 for equals at K 32, 1000 each for a draw, 1008 at K 16).
"""
from django.test import SimpleTestCase

from . import ratings


class EloTests(SimpleTestCase):
    def test_equals_at_k_32(self):
        self.assertEqual(ratings.elo_update(1000, 1000, 1, 32), (1016, 984))

    def test_a_draw_between_equals_moves_nothing(self):
        self.assertEqual(ratings.elo_update(1000, 1000, 0.5, 32), (1000, 1000))

    def test_k_16(self):
        self.assertEqual(ratings.elo_update(1000, 1000, 1, 16)[0], 1008)

    def test_the_favourite_gains_less(self):
        self.assertAlmostEqual(ratings.elo_expected(1400, 1000), 0.909, places=3)
        a, b = ratings.elo_update(1400, 1000, 1, 32)
        self.assertAlmostEqual(a, 1402.91, places=2)
        self.assertAlmostEqual(a - 1400, 1000 - b, places=9)

    def test_fide_k_factors(self):
        # FIDE: 40 until 30 games, 20 below 2400, 10 from 2400, 40 for a
        # junior below 2300.
        self.assertEqual(ratings.fide_k(1500, games=10), 40)
        self.assertEqual(ratings.fide_k(1500, games=40), 20)
        self.assertEqual(ratings.fide_k(2450, games=200), 10)
        self.assertEqual(ratings.fide_k(2100, games=100, junior=True), 40)
        self.assertEqual(ratings.fide_k(2350, games=100, junior=True), 20)


class Glicko2Tests(SimpleTestCase):
    def test_glickmans_worked_example(self):
        # glicko.net, "Example of the Glicko-2 system": 1500 / 200 / 0.06,
        # tau 0.5, against 1400/30 (win), 1550/100 (loss), 1700/300 (loss).
        r, rd, vol = ratings.glicko2_update(1500, 200, 0.06,
                                            [(1400, 30, 1), (1550, 100, 0), (1700, 300, 0)], tau=0.5)
        # The paper rounds mu' to -0.2069 on the way and prints 1464.06; the
        # unrounded result is 1464.05.
        self.assertAlmostEqual(r, 1464.06, delta=0.01)
        self.assertAlmostEqual(rd, 151.52, places=2)
        self.assertAlmostEqual(vol, 0.05999, delta=0.00001)        # printed rounded

    def test_no_games_only_widens_the_deviation(self):
        r, rd, vol = ratings.glicko2_update(1500, 200, 0.06, [])
        self.assertEqual(r, 1500)
        self.assertGreater(rd, 200)
        self.assertEqual(vol, 0.06)


class TrueSkillTests(SimpleTestCase):
    def test_two_new_players_with_the_published_defaults(self):
        # Two players at 25 / 8.333 with the defaults (beta 25/6, tau 25/300,
        # draw probability 0.1): the winner goes to 29.396 / 7.171, the loser
        # to 20.604 / 7.171, the example the TrueSkill library documents.
        (wm, ws), (lm, ls) = ratings.trueskill_1v1((25, 25 / 3), (25, 25 / 3))
        self.assertAlmostEqual(wm, 29.396, places=3)
        self.assertAlmostEqual(ws, 7.171, places=3)
        self.assertAlmostEqual(lm, 20.604, places=3)
        self.assertAlmostEqual(ls, 7.171, places=3)

    def test_the_leaderboard_number_is_mean_less_three_sigma(self):
        self.assertAlmostEqual(ratings.trueskill_conservative((25, 25 / 3)), 0, places=9)
