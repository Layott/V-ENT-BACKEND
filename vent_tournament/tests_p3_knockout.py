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


# ---------------------------------------------------------------------------
# A reseeded knockout (Task A3)
# ---------------------------------------------------------------------------

from . import bracket_edit, match_shape, results  # noqa: E402
from .tests_stage_engine import play  # noqa: E402


def match_of(stage, round_number, *seeds):
    """The match of this round between these seeds (either order)."""
    for m in stage.matches.filter(round_number=round_number, bracket_side='winners'):
        sides = {m.participant_1.seed if m.participant_1 else None,
                 m.participant_2.seed if m.participant_2 else None}
        if sides == set(seeds):
            return m
    raise AssertionError('no round %s match between %s' % (round_number, seeds))


def pairs(stage, round_number):
    out = []
    for m in stage.matches.filter(round_number=round_number, bracket_side='winners',
                                  winner_place__isnull=True).order_by('match_number'):
        if m.match_number > 1 and m.slots.filter(kind='loser').exists():
            continue                      # the third-place match
        out.append(tuple(p.seed if p else None for p in (m.participant_1, m.participant_2)))
    return out


def win(match, seed):
    """`seed` wins `match` 2-0."""
    match.refresh_from_db()
    if match.participant_1.seed == seed:
        return play(match, 2, 0)
    return play(match, 0, 2)


class ReseedTests(TestCase):
    def reseeded(self, n, **settings):
        t, creator, regs = field(n)
        stage = add_stage(t, 0, 'single_elimination', settings=dict(settings, reseed=True))
        draw(stage, creator)
        return t, creator, stage

    def test_the_best_remaining_meets_the_worst_remaining(self):
        _t, _c, stage = self.reseeded(8)
        self.assertEqual(pairs(stage, 2), [(None, None), (None, None)])
        win(match_of(stage, 1, 1, 8), 8)
        win(match_of(stage, 1, 2, 7), 7)
        win(match_of(stage, 1, 4, 5), 4)
        self.assertEqual(pairs(stage, 2), [(None, None), (None, None)])
        win(match_of(stage, 1, 3, 6), 3)
        # Fixed lines would give 8 v 4 and 7 v 3.
        self.assertEqual(pairs(stage, 2), [(3, 8), (4, 7)])

    def test_byes_count_as_survivors(self):
        # Six entrants: seeds 1 and 2 have byes. Fixed lines would give
        # 1 v 4 and 2 v 6 after these two results.
        _t, _c, stage = self.reseeded(6)
        win(match_of(stage, 1, 4, 5), 4)
        win(match_of(stage, 1, 3, 6), 6)
        self.assertEqual(pairs(stage, 2), [(1, 6), (2, 4)])

    def test_the_seats_say_where_they_come_from(self):
        _t, _c, stage = self.reseeded(8)
        rows = {}
        for m in stage.matches.filter(round_number=2).order_by('match_number'):
            match_shape.attach_slots([m])
            rows[m.match_number] = [(r['kind'], r['rank']) for r in match_shape.slot_rows(m)]
        self.assertEqual(rows[1], [('reseed', 1), ('reseed', 4)])
        self.assertEqual(rows[2], [('reseed', 2), ('reseed', 3)])
        final = stage.matches.get(round_number=3)
        match_shape.attach_slots([final])
        self.assertEqual([(r['kind'], r['rank']) for r in match_shape.slot_rows(final)],
                         [('reseed', 1), ('reseed', 2)])

    def test_reopening_a_result_empties_the_next_round(self):
        t, creator, stage = self.reseeded(4)
        first = win(match_of(stage, 1, 1, 4), 1)
        win(match_of(stage, 1, 2, 3), 2)
        self.assertEqual(pairs(stage, 2), [(1, 2)])
        bracket_edit.reopen(first, creator)
        self.assertEqual(pairs(stage, 2), [(None, None)])

    def test_a_corrected_result_pairs_the_next_round_again(self):
        _t, _c, stage = self.reseeded(8)
        for a, b in ((1, 8), (2, 7), (3, 6), (4, 5)):
            win(match_of(stage, 1, a, b), a)
        self.assertEqual(pairs(stage, 2), [(1, 4), (2, 3)])
        first = stage.matches.get(round_number=1, participant_1__seed=1)
        play(first, 0, 2)                  # seed 8 won after all
        self.assertEqual(pairs(stage, 2), [(2, 8), (3, 4)])

    def test_no_correction_once_the_next_round_is_played(self):
        _t, _c, stage = self.reseeded(8)
        for a, b in ((1, 8), (2, 7), (3, 6), (4, 5)):
            win(match_of(stage, 1, a, b), a)
        win(match_of(stage, 2, 1, 4), 1)
        first = stage.matches.get(round_number=1, participant_1__seed=1)
        with self.assertRaises(results.ResultError) as caught:
            play(first, 0, 2)
        self.assertEqual(caught.exception.code, 'NEXT_MATCH_ALREADY_PLAYED')
        with self.assertRaises(bracket_edit.EditError) as refused:
            bracket_edit.reopen(first, _c)
        self.assertEqual(refused.exception.code, 'NEXT_MATCH_ALREADY_PLAYED')

    def test_places_are_shared_as_in_any_knockout(self):
        _t, _c, stage = self.reseeded(8)
        play_out(stage)
        self.assertEqual(ranks(stage), [1, 2, 3, 3, 5, 5, 5, 5])

    def test_a_third_place_match_still_takes_the_semi_final_losers(self):
        _t, _c, stage = self.reseeded(8, third_place=True)
        play_out(stage)
        self.assertEqual(ranks(stage), [1, 2, 3, 4, 5, 5, 5, 5])

    def test_a_threshold_bracket_can_be_reseeded(self):
        _t, _c, stage = self.reseeded(8, threshold=2)
        win(match_of(stage, 1, 1, 8), 8)
        for a, b in ((2, 7), (3, 6), (4, 5)):
            win(match_of(stage, 1, a, b), a)
        self.assertEqual(pairs(stage, 2), [(2, 8), (3, 4)])
