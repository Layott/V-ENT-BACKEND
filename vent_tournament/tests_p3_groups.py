"""Group formats of P3 W1 (design section 11): bracket groups, GSL as their preset, Berger order.

CEO, 16 October 2026, inbox 484: "i also need to implement new brackets, formats
and modes", approved with the design. Two rows of section 11:

- **Bracket groups**: "a group stage working as an Elimination Bracket (Simple
  or Double)" (toornament, research section 4.5). Several small knockouts, the
  people staying in their group, a threshold per group. GSL is the case of
  groups of four, double elimination, two going through, so it becomes this
  format's preset rather than code of its own ("GSL group | yes, hard-coded |
  P3 W1 becomes a bracket groups preset").
- **Berger order**: FIDE's published round robin tables (research catalogue
  3.1), with the last two rounds of the first cycle reversed in a double round
  robin so nobody keeps the same side three rounds running.
"""
from django.db import transaction
from django.test import TestCase

from . import stage_engine, stage_settings, stages, structure
from .models import BracketMatch
from .services import bracket as bracket_service
from .tests_stage_engine import add_stage, draw, field, play_out


def group_ranks(stage, group):
    return sorted(r['rank'] for r in stage_engine.standings(stage) if r['group'] == group and r['rank'])


class BracketGroupSettingsTests(TestCase):
    def test_the_default_is_the_gsl_shape(self):
        out = stage_settings.clean('bracket_groups', {})
        self.assertEqual((out['bracket'], out['threshold'], out['group_size'], out['grand_final']),
                         ('double', 2, 4, 'none'))

    def test_single_brackets_to_one_winner(self):
        out = stage_settings.clean('bracket_groups', {'bracket': 'single', 'threshold': 0})
        self.assertEqual((out['bracket'], out['threshold']), ('single', 0))

    def test_an_unknown_bracket_is_refused(self):
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('bracket_groups', {'bracket': 'triple'})
        self.assertEqual((caught.exception.code, caught.exception.field), ('UNKNOWN_CHOICE', 'bracket'))

    def test_a_group_size_below_two_is_refused(self):
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('bracket_groups', {'group_size': 1})
        self.assertEqual(caught.exception.field, 'group_size')


class BracketGroupDrawTests(TestCase):
    def test_four_double_groups_play_out_and_two_go_through_each(self):
        t, creator, _regs = field(16)
        stage = add_stage(t, 0, 'bracket_groups', advances=2, groups=4)
        add_stage(t, 1, 'single_elimination')
        draw(stage, creator)
        self.assertEqual(stage.matches.count(), 20)
        self.assertEqual(sorted({m.group_number for m in stage.matches.all()}), [1, 2, 3, 4])
        # Snake: group one holds seeds 1, 8, 9 and 16, and plays 1 v 16, 8 v 9.
        opening = stage.matches.filter(group_number=1, round_number=1, bracket_side='winners')
        self.assertEqual(sorted(tuple(sorted((m.participant_1.seed, m.participant_2.seed)))
                                for m in opening), [(1, 16), (8, 9)])
        play_out(stage)
        for g in (1, 2, 3, 4):
            self.assertEqual(group_ranks(stage, g), [1, 2, 3, 4])
        sent = stage_engine.advancing(stage)
        self.assertEqual([(r['group'], r['rank']) for r in sent],
                         [(1, 1), (2, 1), (3, 1), (4, 1), (1, 2), (2, 2), (3, 2), (4, 2)])

    def test_single_bracket_groups_to_one_winner(self):
        t, creator, _regs = field(8)
        stage = add_stage(t, 0, 'bracket_groups', groups=2,
                          settings={'bracket': 'single', 'threshold': 0})
        draw(stage, creator)
        self.assertEqual(stage.matches.count(), 6)
        play_out(stage)
        self.assertEqual(group_ranks(stage, 1), [1, 2, 3, 3])

    def test_the_groups_come_from_the_group_size_when_none_were_set(self):
        t, creator, _regs = field(12)
        stage = add_stage(t, 0, 'bracket_groups', settings={'group_size': 4})
        draw(stage, creator)
        stage.refresh_from_db()
        self.assertEqual(stage.groups, 3)
        self.assertEqual(stage.matches.count(), 15)

    def test_a_group_too_small_for_a_double_bracket_is_refused(self):
        t, creator, _regs = field(6)
        stage = add_stage(t, 0, 'bracket_groups', groups=3)
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            draw(stage, creator)
        self.assertEqual(caught.exception.code, 'GROUP_TOO_SMALL')
        self.assertFalse(stage.matches.exists())

    def test_a_threshold_too_big_for_a_group_is_refused(self):
        t, creator, _regs = field(8)
        stage = add_stage(t, 0, 'bracket_groups', groups=2,
                          settings={'bracket': 'single', 'threshold': 4})
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            draw(stage, creator)
        self.assertEqual(caught.exception.code, 'THRESHOLD_TOO_BIG')


class GslIsTheBracketGroupsPresetTests(TestCase):
    def test_gsl_draws_as_double_bracket_groups_of_four(self):
        t, creator, _regs = field(8)
        stage = add_stage(t, 0, 'gsl', advances=2)
        add_stage(t, 1, 'single_elimination')
        draw(stage, creator)
        self.assertEqual(stage.matches.count(), 10)
        self.assertTrue(stage.matches.filter(bracket_side='losers').exists())
        play_out(stage)
        self.assertEqual(group_ranks(stage, 1), [1, 2, 3, 4])
        self.assertEqual([(r['group'], r['rank']) for r in stage_engine.advancing(stage)],
                         [(1, 1), (2, 1), (1, 2), (2, 2)])

    def test_a_gsl_drawn_before_p3_still_ranks(self):
        """The layout GSL was drawn in until P3: five matches a group, all on
        the winners side, the decider in round three."""
        t, creator, regs = field(4)
        stage = add_stage(t, 0, 'gsl')
        stage.entrants = [r.id for r in regs]
        from django.utils import timezone
        stage.drawn_at = timezone.now()
        stage.save(update_fields=['entrants', 'drawn_at'])
        s1, s2, s3, s4 = regs
        mk = lambda r, m, a=None, b=None, w=None: BracketMatch.objects.create(  # noqa: E731
            tournament=t, stage=stage, group_number=1, round_number=r, match_number=m,
            bracket_side='winners', participant_1=a, participant_2=b, winner=w,
            status='completed' if w else 'scheduled')
        mk(1, 1, s1, s4, s1)
        mk(1, 2, s2, s3, s2)
        mk(2, 1, s1, s2, s1)
        mk(2, 2, s4, s3, s3)
        mk(3, 1, s2, s3, s2)
        rows = {r['registration_id']: r['rank'] for r in stage_engine.standings(stage)}
        self.assertEqual([rows[r.id] for r in regs], [1, 2, 3, 4])


class BracketGroupShapeTests(TestCase):
    def test_shape(self):
        shape = structure.describe('bracket_groups', 16, groups=4,
                                   settings=stage_settings.clean('bracket_groups', {}))['shape']
        self.assertEqual((shape['groups'], shape['matches'], shape['qualify']), (4, 20, 8))

    def test_the_preview_agrees_with_the_draw(self):
        cases = [(16, 4, {}), (12, 3, {}), (8, 2, {'bracket': 'single', 'threshold': 0}),
                 (10, 2, {'bracket': 'single', 'threshold': 2}), (9, 0, {'group_size': 3, 'bracket': 'single'}),
                 (16, 2, {'threshold': 4})]
        for n, groups, settings in cases:
            with self.subTest(n=n, groups=groups, settings=settings):
                t, creator, _regs = field(n)
                stage = add_stage(t, 0, 'bracket_groups', groups=groups, settings=settings)
                draw(stage, creator)
                shape = structure.describe('bracket_groups', n, groups=groups,
                                           settings=stage.settings)['shape']
                self.assertEqual(stage.matches.count(), shape['matches'])


class BracketGroupPlanTests(TestCase):
    def test_a_group_sends_at_most_its_qualifiers(self):
        with self.assertRaises(stages.StageError) as caught:
            stages.plan([
                {'format': 'bracket_groups', 'label': 'Groups', 'advances': 3, 'groups': 4,
                 'settings': {'threshold': 2}},
                {'format': 'single_elimination', 'label': 'Playoff'},
            ])
        self.assertEqual(caught.exception.code, 'BRACKET_GROUPS_SEND_QUALIFIERS')

    def test_two_from_each_group_is_a_plan(self):
        out = stages.plan([
            {'format': 'bracket_groups', 'label': 'Groups', 'advances': 2, 'groups': 4},
            {'format': 'single_elimination', 'label': 'Playoff'},
        ])
        self.assertEqual(len(out), 2)


# ---------------------------------------------------------------------------
# Berger order
# ---------------------------------------------------------------------------

FIDE_SIX = [
    [(1, 6), (2, 5), (3, 4)],
    [(6, 4), (5, 3), (1, 2)],
    [(2, 6), (3, 1), (4, 5)],
    [(6, 5), (1, 4), (2, 3)],
    [(3, 6), (4, 2), (5, 1)],
]


def by_round(pairings):
    out = {}
    for r, a, b in pairings:
        out.setdefault(r, []).append((a, b))
    return [out[r] for r in sorted(out)]


class BergerTests(TestCase):
    def test_six_players_follow_the_fide_table(self):
        self.assertEqual(by_round(bracket_service.round_robin_pairings([1, 2, 3, 4, 5, 6], method='berger')),
                         FIDE_SIX)

    def test_an_odd_field_gives_the_highest_number_the_bye(self):
        got = by_round(bracket_service.round_robin_pairings([1, 2, 3, 4, 5], method='berger'))
        self.assertEqual(got, [[p for p in rnd if 6 not in p] for rnd in FIDE_SIX])

    def test_a_double_round_robin_reverses_the_last_two_rounds_of_the_first_cycle(self):
        got = by_round(bracket_service.round_robin_pairings([1, 2, 3, 4, 5, 6], legs=2, method='berger'))
        first = FIDE_SIX[:3] + [FIDE_SIX[4], FIDE_SIX[3]]
        second = [[(b, a) for a, b in rnd] for rnd in FIDE_SIX]
        self.assertEqual(got, first + second)

    def test_the_circle_order_is_unchanged(self):
        self.assertEqual(bracket_service.round_robin_pairings([1, 2, 3, 4]),
                         bracket_service.round_robin_pairings([1, 2, 3, 4], method='circle'))

    def test_the_setting(self):
        self.assertEqual(stage_settings.clean('round_robin', {})['pairing'], 'circle')
        self.assertEqual(stage_settings.clean('league', {'pairing': 'berger'})['pairing'], 'berger')
        with self.assertRaises(stage_settings.SettingsError):
            stage_settings.clean('round_robin', {'pairing': 'snake'})

    def test_a_stage_drawn_in_berger_order(self):
        t, creator, _regs = field(6, 'round_robin')
        stage = add_stage(t, 0, 'round_robin', settings={'pairing': 'berger'})
        draw(stage, creator)
        first = [(m.participant_1.seed, m.participant_2.seed)
                 for m in stage.matches.filter(round_number=1).order_by('match_number')]
        self.assertEqual(first, FIDE_SIX[0])
