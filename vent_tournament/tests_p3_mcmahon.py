"""McMahon (P3 W3d, research catalogue 4.6): a Swiss where everybody starts on
a score from their seed band and every round pairs equal scores, so a mixed
field plays even games from round one.

CEO, 24 October 2026: "make all brackets and formats live today and function
properly".
"""
from django.test import TestCase

from . import stage_engine, stage_settings
from .services import bracket as bracket_service
from .tests_stage_engine import add_stage, draw, field, play


def mcmahon(n=8, bands=2, rounds=3):
    t, creator, regs = field(n, 'swiss')
    stage = add_stage(t, 0, 'swiss', settings={'rounds': rounds, 'mcmahon_bands': bands})
    draw(stage, creator)
    return t, creator, regs, stage


def score(stage):
    return {r['registration_id']: r['mcmahon_start'] + r['wins']
            for r in stage_engine.standings(stage)}


class StartTests(TestCase):
    def test_bands_split_by_seed_top_band_on_zero(self):
        self.assertEqual(bracket_service.mcmahon_starts([1, 2, 3, 4, 5, 6, 7, 8], 2),
                         {1: 0, 2: 0, 3: 0, 4: 0, 5: -1, 6: -1, 7: -1, 8: -1})
        self.assertEqual(sorted(bracket_service.mcmahon_starts(range(10), 3).values()),
                         [-2, -2, -2, -1, -1, -1, 0, 0, 0, 0])
        self.assertEqual(bracket_service.mcmahon_starts([1, 2, 3], 0), {})

    def test_the_setting_is_off_unless_set_and_held_in_range(self):
        self.assertEqual(stage_settings.clean('swiss', {})['mcmahon_bands'], 0)
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('swiss', {'mcmahon_bands': 11})
        self.assertEqual(caught.exception.field, 'mcmahon_bands')


class PairingTests(TestCase):
    def test_round_one_pairs_inside_each_band(self):
        t, creator, regs, stage = mcmahon()
        start = {r.id: (0 if r.seed <= 4 else -1) for r in regs}
        for m in stage.matches.filter(round_number=1):
            self.assertEqual(start[m.participant_1_id], start[m.participant_2_id])

    def test_later_rounds_pair_equal_scores_and_the_table_counts_the_start(self):
        t, creator, regs, stage = mcmahon()
        for m in stage.matches.filter(round_number=1):
            play(m, 1, 0)
        now = score(stage)
        second = list(stage.matches.filter(round_number=2))
        self.assertEqual(len(second), 4)
        level = sum(1 for m in second if now[m.participant_1_id] == now[m.participant_2_id])
        # 8 entrants on scores 1,1,0,0,0,0,-1,-1: every pair can be level.
        self.assertEqual(level, 4)
        rows = {r['registration_id']: r for r in stage_engine.standings(stage)}
        bottom = next(r for r in regs if r.seed == 8)
        self.assertEqual(rows[bottom.id]['mcmahon_start'], -1)

    def test_an_ordinary_swiss_is_unchanged(self):
        t, creator, regs = field(8, 'swiss')
        stage = add_stage(t, 0, 'swiss', settings={'rounds': 3})
        draw(stage, creator)
        first = stage.matches.filter(round_number=1).order_by('match_number').first()
        seeds = sorted((first.participant_1.seed, first.participant_2.seed))
        self.assertEqual(seeds, [1, 5])
