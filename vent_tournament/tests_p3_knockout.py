"""Knockout settings of P3 W1 (design section 11): threshold, grand final none, reseed.

CEO, 16 October 2026, inbox 484: "i also need to implement new brackets, formats
and modes", approved with the design ("i aprove the design"). Three rows of
section 11 are settings on a knockout rather than new formats:

- a threshold (qualifier) bracket stops when so many are left, so a stage can
  send its top four on without playing the semi-finals and the final
  (toornament's `threshold`, research toornament section 4.2);
- a double elimination with no grand final sends both bracket winners on
  (toornament's `grand_final: none`, section 4.3);
- a reseeded knockout pairs the best remaining seed against the worst remaining
  seed each round instead of following fixed lines (the NFL, research catalogue
  section 2.1).
"""
from django.test import TestCase

from . import stage_settings


class SettingsTests(TestCase):
    def test_a_threshold_is_kept(self):
        out = stage_settings.clean('single_elimination', {'threshold': 4})
        self.assertEqual(out['threshold'], 4)

    def test_a_threshold_must_be_a_power_of_two(self):
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('single_elimination', {'threshold': 3})
        self.assertEqual((caught.exception.code, caught.exception.field),
                         ('NOT_A_POWER_OF_TWO', 'threshold'))

    def test_a_threshold_of_one_is_no_threshold(self):
        self.assertEqual(stage_settings.clean('single_elimination', {'threshold': 1})['threshold'], 0)

    def test_a_threshold_drops_the_matches_for_places(self):
        out = stage_settings.clean('single_elimination',
                                   {'threshold': 2, 'third_place': True, 'every_place': True})
        self.assertEqual((out['third_place'], out['every_place']), (False, False))

    def test_every_place_has_no_reseed(self):
        out = stage_settings.clean('single_elimination', {'every_place': True, 'reseed': True})
        self.assertFalse(out['reseed'])

    def test_reseed_is_kept(self):
        self.assertTrue(stage_settings.clean('single_elimination', {'reseed': True})['reseed'])

    def test_an_old_row_cleans_to_the_old_behaviour(self):
        out = stage_settings.clean('single_elimination', {'third_place': True})
        self.assertEqual((out['threshold'], out['reseed'], out['third_place']), (0, False, True))
        de = stage_settings.clean('double_elimination', {})
        self.assertEqual((de['threshold'], de['grand_final']), (0, 'reset'))

    def test_a_double_elimination_may_have_no_grand_final(self):
        self.assertEqual(stage_settings.clean('double_elimination', {'grand_final': 'none'})['grand_final'],
                         'none')

    def test_a_double_elimination_threshold_plays_no_grand_final(self):
        out = stage_settings.clean('double_elimination', {'threshold': 4, 'grand_final': 'reset'})
        self.assertEqual((out['threshold'], out['grand_final']), (4, 'none'))

    def test_threshold_of(self):
        clean = stage_settings.clean
        self.assertEqual(stage_settings.threshold_of(clean('double_elimination', {'grand_final': 'none'})), 2)
        self.assertEqual(stage_settings.threshold_of(clean('double_elimination', {'threshold': 4})), 4)
        self.assertEqual(stage_settings.threshold_of(clean('single_elimination', {'threshold': 8})), 8)
        self.assertEqual(stage_settings.threshold_of(clean('single_elimination', {})), 0)
        self.assertEqual(stage_settings.threshold_of(clean('double_elimination', {})), 0)
        self.assertEqual(stage_settings.threshold_of(None), 0)
