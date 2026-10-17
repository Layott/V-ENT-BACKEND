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


# ---------------------------------------------------------------------------
# A threshold bracket: drawn, played, ranked (Task A2)
# ---------------------------------------------------------------------------

from . import stage_engine, structure  # noqa: E402
from .tests_stage_engine import add_stage, draw, field, play_out  # noqa: E402


def ranks(stage):
    return sorted(r['rank'] for r in stage_engine.standings(stage) if r['rank'])


class ThresholdDrawTests(TestCase):
    def test_a_single_elimination_stops_at_four(self):
        t, creator, _regs = field(16)
        stage = add_stage(t, 0, 'single_elimination', settings={'threshold': 4})
        draw(stage, creator)
        self.assertEqual(stage.matches.count(), 12)
        self.assertEqual(max(m.round_number for m in stage.matches.all()), 2)
        self.assertFalse(stage.matches.filter(is_final=True).exists())
        play_out(stage)
        self.assertTrue(stage_engine.stage_finished(stage))
        self.assertEqual(ranks(stage), [1] * 4 + [5] * 4 + [9] * 8)
        qualified = [r for r in stage_engine.standings(stage) if r['rank'] == 1]
        self.assertEqual(sorted(r['seed'] for r in qualified), [1, 2, 3, 4])
        self.assertTrue(all(r['status'] == 'qualified' for r in qualified))

    def test_a_threshold_too_big_is_refused(self):
        t, creator, _regs = field(5)
        stage = add_stage(t, 0, 'single_elimination', settings={'threshold': 8})
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            draw(stage, creator)
        self.assertEqual(caught.exception.code, 'THRESHOLD_TOO_BIG')
        self.assertFalse(stage.matches.exists())

    def test_nine_entrants_may_stop_at_eight(self):
        t, creator, _regs = field(9)
        stage = add_stage(t, 0, 'single_elimination', settings={'threshold': 8})
        draw(stage, creator)
        self.assertEqual(stage.matches.count(), 8)
        play_out(stage)
        self.assertEqual(ranks(stage), [1] * 8 + [9])

    def test_a_double_elimination_with_no_grand_final(self):
        t, creator, _regs = field(8, 'double_elimination')
        stage = add_stage(t, 0, 'double_elimination', settings={'grand_final': 'none'})
        draw(stage, creator)
        self.assertEqual(stage.matches.count(), 13)
        self.assertFalse(stage.matches.filter(bracket_side='grand_final').exists())
        play_out(stage)
        rows = stage_engine.standings(stage)
        by_rank = {r['rank']: r for r in rows if r['rank'] in (1, 2)}
        # The upper bracket's champion is above the lower bracket's.
        self.assertEqual((by_rank[1]['seed'], by_rank[2]['seed']), (1, 2))
        self.assertEqual(ranks(stage)[:2], [1, 2])

    def test_a_double_elimination_stops_at_four(self):
        t, creator, _regs = field(8, 'double_elimination')
        stage = add_stage(t, 0, 'double_elimination', settings={'threshold': 4})
        draw(stage, creator)
        self.assertEqual(stage.matches.count(), 10)
        play_out(stage)
        rows = stage_engine.standings(stage)
        self.assertEqual(ranks(stage)[:4], [1, 1, 3, 3])
        upper = sorted(r['seed'] for r in rows if r['rank'] == 1)
        self.assertEqual(upper, [1, 2])
        self.assertTrue(all(r['status'] == 'qualified' for r in rows if r['rank'] in (1, 3)))

    def test_the_qualifiers_go_on_in_seed_order(self):
        t, creator, _regs = field(16)
        first = add_stage(t, 0, 'single_elimination', advances=4, settings={'threshold': 4})
        add_stage(t, 1, 'single_elimination')
        draw(first, creator)
        play_out(first)
        sent = stage_engine.advancing(first)
        self.assertEqual([r['seed'] for r in sent], [1, 2, 3, 4])

    def test_upper_qualifiers_go_on_ahead_of_lower_ones(self):
        t, creator, _regs = field(8, 'double_elimination')
        first = add_stage(t, 0, 'double_elimination', advances=4, settings={'threshold': 4})
        add_stage(t, 1, 'single_elimination')
        draw(first, creator)
        play_out(first)
        sent = stage_engine.advancing(first)
        self.assertEqual([r['rank'] for r in sent], [1, 1, 3, 3])

    def test_a_threshold_stage_that_is_the_last_finishes_the_tournament(self):
        t, creator, _regs = field(8)
        stage = add_stage(t, 0, 'single_elimination', settings={'threshold': 2})
        draw(stage, creator)
        play_out(stage)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        positions = sorted(t.registrations.values_list('final_position', flat=True))
        self.assertEqual(positions, [1, 1, 3, 3, 5, 5, 5, 5])


class ThresholdShapeTests(TestCase):
    def shape(self, fmt, n, settings):
        return structure.describe(fmt, n, settings=settings)['shape']

    def test_single_elimination_shape(self):
        shape = self.shape('single_elimination', 16, {'threshold': 4})
        self.assertEqual((shape['matches'], shape['rounds'], shape['qualify']), (12, 2, 4))

    def test_double_elimination_shape(self):
        shape = self.shape('double_elimination', 8, {'threshold': 4, 'grand_final': 'none'})
        self.assertEqual((shape['matches'], shape['qualify']), (10, 4))
        none = self.shape('double_elimination', 8, {'grand_final': 'none'})
        self.assertEqual((none['matches'], none['qualify']), (13, 2))

    def test_a_threshold_too_big_is_named(self):
        # Six, not five: an odd count is already refused as 'even' for this format.
        shape = self.shape('single_elimination', 6, {'threshold': 8})
        self.assertEqual(shape['problem'], 'threshold_too_big')

    def test_the_preview_agrees_with_the_draw(self):
        cases = [('single_elimination', n, {'threshold': th}) for n in (4, 5, 8, 9, 13, 16, 17, 33)
                 for th in (2, 4, 8) if th * 2 <= (1 << (n - 1).bit_length())]
        cases += [('double_elimination', n, s) for n in (4, 5, 8, 11, 16)
                  for s in ({'grand_final': 'none'}, {'threshold': 4})
                  if s.get('threshold', 2) * 2 <= (1 << (n - 1).bit_length())]
        for fmt, n, settings in cases:
            with self.subTest(fmt=fmt, n=n, settings=settings):
                t, creator, _regs = field(n, fmt)
                stage = add_stage(t, 0, fmt, settings=settings)
                draw(stage, creator)
                shape = self.shape(fmt, n, stage.settings)
                self.assertEqual(stage.matches.count(), shape['matches'])
