"""The custom bracket schema of P3 W2 (design section 11).

CEO, 16 October 2026, inbox 484, approved with the design: "Custom bracket schema
(P3 W2): a JSON description of rounds, matches and slot sources, compiled to rows
and checked (every slot has one source, each winner and loser goes at most once,
no cycles, every exit has a place). The presets are schemas produced by a function
for N entrants, so a new preset is a function, not a new engine."

The line language is toornament's (research section 4.7): one match a line,
`A=[1,4]`, `C=[W:A,W:B]`, `D=[L:A,L:B]`, with `#round N` and a side in square
brackets; V-ENT adds `@branch`, `!` for the final and `>1,2` for the places a
match settles.
"""
from django.test import SimpleTestCase

from . import bracket_schema
from .bracket_schema import SchemaError

FOUR = """
#round 1
A=[1,4]
B=[2,3]
#round 2
C=[W:A,W:B]! >1,2
[PL]
D=[L:A,L:B] >3,4
"""

FOUR_SCHEMA = {'matches': [
    {'id': 'A', 'round': 1, 'side': 'winners', 'branch': '', 'final': False,
     'winner_place': None, 'loser_place': None, 'slots': [{'seed': 1}, {'seed': 4}]},
    {'id': 'B', 'round': 1, 'side': 'winners', 'branch': '', 'final': False,
     'winner_place': None, 'loser_place': None, 'slots': [{'seed': 2}, {'seed': 3}]},
    {'id': 'C', 'round': 2, 'side': 'winners', 'branch': '', 'final': True,
     'winner_place': 1, 'loser_place': 2, 'slots': [{'winner': 'A'}, {'winner': 'B'}]},
    {'id': 'D', 'round': 2, 'side': 'placement', 'branch': '', 'final': False,
     'winner_place': 3, 'loser_place': 4, 'slots': [{'loser': 'A'}, {'loser': 'B'}]},
]}


def schema_of(*matches):
    out = []
    for m in matches:
        row = {'round': 1, 'side': 'winners', 'branch': '', 'final': False,
               'winner_place': None, 'loser_place': None}
        row.update(m)
        out.append(row)
    return {'matches': out}


class ParseTests(SimpleTestCase):
    def test_toornaments_four_player_example(self):
        self.assertEqual(bracket_schema.parse(FOUR), FOUR_SCHEMA)

    def test_rounds_are_worked_out_when_not_written(self):
        got = bracket_schema.parse('A=[1,4]\nB=[2,3]\nC=[W:A,W:B]!')
        self.assertEqual([m['round'] for m in got['matches']], [1, 1, 2])

    def test_a_branch_and_a_bye(self):
        got = bracket_schema.parse('@east\nA=[1,-]\n@\nB=[2,3]\nC=[W:A,W:B]!')
        self.assertEqual(got['matches'][0]['branch'], 'east')
        self.assertEqual(got['matches'][0]['slots'], [{'seed': 1}, {'bye': True}])
        self.assertEqual(got['matches'][1]['branch'], '')

    def test_comments_and_blank_lines_are_ignored(self):
        got = bracket_schema.parse('// a final\n\nA=[1,2]!\n')
        self.assertEqual(len(got['matches']), 1)

    def test_a_line_it_cannot_read_names_its_number(self):
        with self.assertRaises(SchemaError) as caught:
            bracket_schema.parse('A=[1,2]!\nB=(3,4)')
        self.assertEqual((caught.exception.code, caught.exception.line), ('SCHEMA_LINE', 2))

    def test_an_unknown_side_is_refused(self):
        with self.assertRaises(SchemaError) as caught:
            bracket_schema.parse('[XB]\nA=[1,2]!')
        self.assertEqual(caught.exception.code, 'SCHEMA_LINE')


class CheckTests(SimpleTestCase):
    def assertRefused(self, schema, code, match=None):
        with self.assertRaises(SchemaError) as caught:
            bracket_schema.check(schema)
        self.assertEqual(caught.exception.code, code)
        if match is not None:
            self.assertEqual(caught.exception.match, match)

    def test_the_four_player_schema_passes(self):
        self.assertEqual(bracket_schema.check(FOUR_SCHEMA), 4)          # four seeds

    def test_empty(self):
        self.assertRefused({'matches': []}, 'SCHEMA_EMPTY')

    def test_duplicate_id(self):
        self.assertRefused(schema_of({'id': 'A', 'slots': [{'seed': 1}, {'seed': 2}], 'final': True},
                                     {'id': 'A', 'slots': [{'seed': 3}, {'seed': 4}]}),
                           'SCHEMA_DUPLICATE_ID', 'A')

    def test_two_slots(self):
        self.assertRefused(schema_of({'id': 'A', 'slots': [{'seed': 1}], 'final': True}),
                           'SCHEMA_TWO_SLOTS', 'A')

    def test_one_source_per_slot(self):
        self.assertRefused(schema_of({'id': 'A', 'slots': [{'seed': 1, 'winner': 'B'}, {'seed': 2}],
                                      'final': True}), 'SCHEMA_SLOT_SOURCE', 'A')

    def test_unknown_match(self):
        self.assertRefused(schema_of({'id': 'A', 'slots': [{'seed': 1}, {'seed': 2}]},
                                     {'id': 'C', 'round': 2, 'slots': [{'winner': 'A'}, {'winner': 'Z'}],
                                      'final': True}), 'SCHEMA_UNKNOWN_MATCH', 'C')

    def test_a_winner_goes_once(self):
        self.assertRefused(schema_of({'id': 'A', 'slots': [{'seed': 1}, {'seed': 2}]},
                                     {'id': 'B', 'round': 2, 'slots': [{'winner': 'A'}, {'seed': 3}]},
                                     {'id': 'C', 'round': 3, 'slots': [{'winner': 'A'}, {'winner': 'B'}],
                                      'final': True}), 'SCHEMA_WINNER_TWICE', 'C')

    def test_a_loser_goes_once(self):
        self.assertRefused(schema_of({'id': 'A', 'slots': [{'seed': 1}, {'seed': 2}]},
                                     {'id': 'B', 'round': 2, 'slots': [{'loser': 'A'}, {'seed': 3}]},
                                     {'id': 'C', 'round': 2, 'slots': [{'loser': 'A'}, {'seed': 4}]},
                                     {'id': 'F', 'round': 3, 'slots': [{'winner': 'B'}, {'winner': 'C'}],
                                      'final': True}), 'SCHEMA_LOSER_TWICE', 'C')

    def test_a_source_plays_before_the_match_it_feeds(self):
        self.assertRefused(schema_of({'id': 'A', 'round': 2, 'slots': [{'winner': 'B'}, {'seed': 1}],
                                      'final': True},
                                     {'id': 'B', 'round': 2, 'slots': [{'winner': 'A'}, {'seed': 2}]}),
                           'SCHEMA_ROUND_ORDER')

    def test_seeds_are_one_to_n_each_once(self):
        self.assertRefused(schema_of({'id': 'A', 'slots': [{'seed': 1}, {'seed': 3}], 'final': True}),
                           'SCHEMA_SEEDS')
        self.assertRefused(schema_of({'id': 'A', 'slots': [{'seed': 1}, {'seed': 1}], 'final': True}),
                           'SCHEMA_SEEDS')

    def test_there_is_a_final_or_a_first_place(self):
        self.assertRefused(schema_of({'id': 'A', 'slots': [{'seed': 1}, {'seed': 2}]}), 'SCHEMA_NO_FINAL')
        stated = schema_of({'id': 'A', 'slots': [{'seed': 1}, {'seed': 2}], 'winner_place': 1,
                            'loser_place': 2})
        self.assertEqual(bracket_schema.check(stated), 2)

    def test_only_one_final(self):
        self.assertRefused(schema_of({'id': 'A', 'slots': [{'seed': 1}, {'seed': 2}], 'final': True},
                                     {'id': 'B', 'slots': [{'seed': 3}, {'seed': 4}], 'final': True}),
                           'SCHEMA_TWO_FINALS')

    def test_every_winner_that_goes_nowhere_has_a_place(self):
        self.assertRefused(schema_of({'id': 'A', 'slots': [{'seed': 1}, {'seed': 2}], 'final': True},
                                     {'id': 'B', 'slots': [{'seed': 3}, {'seed': 4}]}),
                           'SCHEMA_EXIT_NO_PLACE', 'B')

    def test_two_byes_make_no_match(self):
        self.assertRefused(schema_of({'id': 'A', 'slots': [{'bye': True}, {'bye': True}], 'final': True}),
                           'SCHEMA_EMPTY_MATCH', 'A')

    def test_bad_values(self):
        self.assertRefused(schema_of({'id': 'A', 'side': 'sideways', 'slots': [{'seed': 1}, {'seed': 2}],
                                      'final': True}), 'SCHEMA_VALUE', 'A')
        self.assertRefused(schema_of({'id': 'A', 'round': 0, 'slots': [{'seed': 1}, {'seed': 2}],
                                      'final': True}), 'SCHEMA_VALUE', 'A')
        self.assertRefused({'matches': 'A=[1,2]'}, 'SCHEMA_VALUE')


# ---------------------------------------------------------------------------
# Drawn and played (Task 2)
# ---------------------------------------------------------------------------

from django.test import TestCase  # noqa: E402

from . import match_shape, stage_engine, stage_settings, structure  # noqa: E402
from .tests_stage_engine import add_stage, draw, field, play_out  # noqa: E402

EIGHT = """
A=[1,8]
B=[4,5]
C=[2,7]
D=[3,6]
E=[W:A,W:B]
F=[W:C,W:D]
G=[W:E,W:F]!
"""


def ranks(stage):
    return {r['seed']: r['rank'] for r in stage_engine.standings(stage)}


class CompileTests(TestCase):
    def drawn(self, n, text, **settings):
        t, creator, _regs = field(n)
        stage = add_stage(t, 0, 'custom_bracket', settings=dict(settings, schema_text=text))
        draw(stage, creator)
        stage.refresh_from_db()
        return stage

    def test_the_four_player_schema_draws_and_plays_to_its_places(self):
        stage = self.drawn(4, FOUR)
        self.assertEqual(stage.matches.count(), 4)
        play_out(stage)
        self.assertEqual(ranks(stage), {1: 1, 2: 2, 3: 3, 4: 4})

    def test_seeds_above_the_field_are_byes(self):
        stage = self.drawn(6, EIGHT)
        self.assertEqual(stage.matches.count(), 7)
        self.assertEqual(stage.matches.filter(status='bye').count(), 2)
        play_out(stage)
        self.assertEqual(sorted(ranks(stage).values()), [1, 2, 3, 3, 5, 5])

    def test_places_not_stated_are_shared_by_the_round_they_went_out_in(self):
        stage = self.drawn(8, EIGHT)
        play_out(stage)
        self.assertEqual(sorted(ranks(stage).values()), [1, 2, 3, 3, 5, 5, 5, 5])

    def test_a_schema_too_small_for_the_field_is_refused(self):
        t, creator, _regs = field(5)
        stage = add_stage(t, 0, 'custom_bracket', settings={'schema_text': FOUR})
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            draw(stage, creator)
        self.assertEqual(caught.exception.code, 'SCHEMA_TOO_SMALL')
        self.assertFalse(stage.matches.exists())

    def test_branches_are_kept_and_served(self):
        stage = self.drawn(4, '@east\nA=[1,4]\nB=[2,3]\nC=[W:A,W:B]!')
        first = stage.matches.select_related('stage').get(round_number=1, match_number=1)
        self.assertEqual(first.branch, 'east')
        self.assertEqual(match_shape.match_row(first)['branch'], 'east')

    def test_the_preview_agrees_with_the_draw(self):
        stage = self.drawn(6, EIGHT)
        shape = structure.describe('custom_bracket', 6, settings=stage.settings)['shape']
        self.assertEqual((shape['matches'], shape['byes']), (7, 2))


class CustomSettingsTests(SimpleTestCase):
    def test_the_text_is_kept_and_read(self):
        out = stage_settings.clean('custom_bracket', {'schema_text': FOUR})
        self.assertEqual(out['schema'], FOUR_SCHEMA)
        self.assertEqual(out['schema_text'], FOUR)
        self.assertEqual(out['preset'], '')

    def test_a_bad_schema_is_refused_with_the_match(self):
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('custom_bracket', {'schema_text': 'A=[1,2]!\nB=[3,4]'})
        self.assertEqual((caught.exception.code, caught.exception.field), ('SCHEMA_EXIT_NO_PLACE', 'schema'))
        self.assertEqual(caught.exception.detail, {'match': 'B'})

    def test_a_line_number_travels_with_the_refusal(self):
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('custom_bracket', {'schema_text': 'A=[1,2]!\nB=(3,4)'})
        self.assertEqual(caught.exception.code, 'SCHEMA_LINE')
        self.assertEqual(caught.exception.detail, {'line': 2})

    def test_no_schema_and_no_preset_is_refused(self):
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('custom_bracket', {})
        self.assertEqual(caught.exception.code, 'SCHEMA_EMPTY')


# ---------------------------------------------------------------------------
# Presets: schemas written by a function (Task 3)
# ---------------------------------------------------------------------------

from . import bracket_presets  # noqa: E402


def preset_stage(n, preset):
    t, creator, regs = field(n)
    stage = add_stage(t, 0, 'custom_bracket', settings={'preset': preset})
    draw(stage, creator)
    stage.refresh_from_db()
    return stage, regs


def played_by(stage, reg):
    return stage.matches.filter(status='completed').filter(
        participant_1=reg).count() + stage.matches.filter(status='completed', participant_2=reg).count()


class PresetTests(TestCase):
    def test_every_preset_writes_a_schema_the_compiler_accepts(self):
        for key, build in bracket_presets.PRESETS.items():
            for n in bracket_presets.SIZES[key]:
                with self.subTest(preset=key, n=n):
                    self.assertGreaterEqual(bracket_schema.check(build(n)), n)

    def test_shaughnessy(self):
        stage, _regs = preset_stage(4, 'shaughnessy')
        self.assertEqual(stage.matches.count(), 3)
        firsts = sorted(tuple(sorted((m.participant_1.seed, m.participant_2.seed)))
                        for m in stage.matches.filter(round_number=1))
        self.assertEqual(firsts, [(1, 4), (2, 3)])

    def test_afl_final_eight_gives_the_top_four_a_second_chance(self):
        stage, regs = preset_stage(8, 'afl_final_eight')
        self.assertEqual(stage.matches.count(), 9)
        play_out(stage)
        self.assertEqual(ranks(stage), {1: 1, 2: 2, 3: 3, 4: 3, 5: 5, 6: 5, 7: 7, 8: 7})
        # Seed 4 lost its qualifying final and played on: three matches.
        self.assertEqual(played_by(stage, regs[3]), 3)

    def test_compass_names_eight_directions_and_places_everybody(self):
        stage, regs = preset_stage(16, 'compass')
        self.assertEqual(stage.matches.count(), 32)
        self.assertEqual(set(stage.matches.values_list('branch', flat=True)),
                         {'east', 'northeast', 'north', 'northwest', 'west', 'southwest',
                          'south', 'southeast'})
        play_out(stage)
        self.assertEqual(sorted(ranks(stage).values()), list(range(1, 17)))
        # Everybody plays a match every round: four each.
        self.assertTrue(all(played_by(stage, r) == 4 for r in regs))

    def test_feed_in_consolation_finds_a_fifth(self):
        stage, regs = preset_stage(16, 'feed_in_consolation')
        self.assertEqual(stage.matches.count(), 27)
        play_out(stage)
        self.assertEqual(sorted(ranks(stage).values()),
                         [1, 2, 3, 4, 5, 6, 7, 7] + [9] * 4 + [13] * 4)
        # Nobody is out after one match: round one losers play on.
        self.assertTrue(all(played_by(stage, r) >= 2 for r in regs))

    def test_a_field_too_big_for_a_fixed_preset_is_refused(self):
        t, creator, _regs = field(9)
        stage = add_stage(t, 0, 'custom_bracket', settings={'preset': 'afl_final_eight'})
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            draw(stage, creator)
        self.assertEqual(caught.exception.code, 'SCHEMA_TOO_SMALL')

    def test_an_unknown_preset_is_refused(self):
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('custom_bracket', {'preset': 'quadruple'})
        self.assertEqual((caught.exception.code, caught.exception.field), ('UNKNOWN_CHOICE', 'preset'))


class SchemaRefusalReachesTheScreenTests(TestCase):
    """The planner says which match or line is wrong: the refusal carries the
    code and, in its detail, where (the screen fills {detail.match})."""

    def setUp(self):
        from .tests_stages import EndpointTests
        EndpointTests.setUp(self)

    def put(self, text):
        return self.client.put('/tournament/%s/stages/set/' % self.t.tournament_id,
                               data={'stages': [{'format': 'custom_bracket', 'label': 'Mine',
                                                 'settings': {'schema_text': text}}]},
                               content_type='application/json', **self.owner_auth)

    def test_the_match_travels(self):
        res = self.put('A=[1,2]!\nB=[3,4]')
        self.assertEqual(res.status_code, 400, res.content)
        body = res.json()
        self.assertEqual((body['code'], body['detail']), ('SCHEMA_EXIT_NO_PLACE', {'match': 'B'}))

    def test_the_line_travels(self):
        body = self.put('A=[1,2]!\nB=(3,4)').json()
        self.assertEqual((body['code'], body['detail']), ('SCHEMA_LINE', {'line': 2}))

    def test_a_good_schema_saves(self):
        res = self.put(FOUR)
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_planner_reads_the_ready_made_brackets(self):
        res = self.client.get('/tournament/%s/stages/' % self.t.tournament_id)
        keys = [p['key'] for p in res.json()['data']['custom_presets']]
        self.assertEqual(keys, ['shaughnessy', 'afl_final_eight', 'compass', 'feed_in_consolation',
                                'repechage', 'triple_elimination'])

