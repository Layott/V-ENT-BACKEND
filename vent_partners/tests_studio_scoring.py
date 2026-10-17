"""Result schemas and scoring expressions.

Design section 14, item 4: results are "validated against the schema, scored by
the expression with a safe evaluator (never eval, R74)". Section 18 leaves the
evaluator to the builder: "a small arithmetic parser, no eval".

What matters most here is refusal. A studio writes the expression, so the
expression is untrusted input that runs on our server: everything it cannot do
is tested as carefully as what it can.
"""
import os

from django.test import SimpleTestCase

from vent_partners import studio_schema, studio_scoring
from vent_partners.studio_scoring import ExpressionError


RESULT = {
    'lineups': [
        {'side': 1, 'score': 13, 'roundsWon': 13, 'won': True},
        {'side': 2, 'score': 7, 'roundsWon': 7, 'won': False},
    ],
    'competitors': [
        {'account_id': 'a1', 'side': 1, 'kills': 20},
        {'account_id': 'a2', 'side': 1, 'kills': 5},
        {'account_id': 'b1', 'side': 2, 'kills': 9},
    ],
    'general': {'double': True, 'map': 'Bermuda'},
}


def scores(expression, settings=None, result=RESULT):
    return studio_scoring.score_sides(expression, result, settings or {})


def refusal(expression, env=None):
    try:
        studio_scoring.compile_expression(expression).evaluate(
            env if env is not None else studio_scoring.side_env(RESULT, 1, {}))
    except ExpressionError as exc:
        return exc.code
    return None


class ScoringTests(SimpleTestCase):
    def test_a_field_of_the_team(self):
        self.assertEqual(scores('team.score'), (13, 7))

    def test_a_sum_over_the_side_s_players(self):
        self.assertEqual(scores('competitors.sum(it.kills)'), (25, 9))

    def test_arithmetic_across_both(self):
        self.assertEqual(scores('team.roundsWon + competitors.sum(it.kills)'), (38, 16))

    def test_a_condition_on_the_whole_session(self):
        self.assertEqual(scores('global.double ? team.roundsWon * 2 : team.roundsWon'), (26, 14))

    def test_a_condition_on_the_chosen_settings(self):
        expression = "settings.mode == 'KillRace' ? competitors.sum(it.kills) : (team.won ? 1 : 0)"
        self.assertEqual(scores(expression, {'mode': 'KillRace'}), (25, 9))
        self.assertEqual(scores(expression, {'mode': 'Classic'}), (1, 0))

    def test_true_and_false_count_as_one_and_nothing(self):
        self.assertEqual(scores('team.won'), (1, 0))

    def test_the_opponent_is_readable(self):
        self.assertEqual(scores('team.score - opponent.score > 5 ? 3 : 1'), (3, 1))

    def test_list_methods(self):
        self.assertEqual(scores('competitors.count()'), (2, 1))
        self.assertEqual(scores('competitors.count(it.kills >= 9)'), (1, 1))
        self.assertEqual(scores('competitors.max(it.kills)'), (20, 9))
        self.assertEqual(scores('competitors.min(it.kills)'), (5, 9))
        self.assertEqual(scores('competitors.avg(it.kills)'), (13, 9))   # 12.5 rounds half up
        self.assertEqual(scores('competitors.any(it.kills > 10) ? 1 : 0'), (1, 0))
        self.assertEqual(scores('competitors.all(it.kills > 1) ? 1 : 0'), (1, 1))

    def test_functions(self):
        self.assertEqual(scores('max(team.score, 10)'), (13, 10))
        self.assertEqual(scores('min(team.score, 10)'), (10, 7))
        self.assertEqual(scores('abs(team.score - 20)'), (7, 13))
        self.assertEqual(scores('floor(team.score / 2)'), (6, 3))
        self.assertEqual(scores('ceil(team.score / 2)'), (7, 4))
        self.assertEqual(scores('round(team.score / 4)'), (3, 2))

    def test_words_for_logic(self):
        self.assertEqual(scores('team.won and not opponent.won ? 2 : 0'), (2, 0))
        self.assertEqual(scores('team.won or opponent.won ? 2 : 0'), (2, 2))
        self.assertEqual(scores('!team.won ? 5 : 0'), (0, 5))

    def test_precedence(self):
        self.assertEqual(scores('2 + 3 * 4'), (14, 14))
        self.assertEqual(scores('(2 + 3) * 4'), (20, 20))
        self.assertEqual(scores('-2 + 10'), (8, 8))
        self.assertEqual(scores('10 % 4'), (2, 2))

    def test_a_missing_field_is_nothing_rather_than_a_crash(self):
        self.assertEqual(scores('team.nothing == null ? 4 : 0'), (4, 4))

    def test_a_score_outside_what_a_match_holds_is_refused(self):
        with self.assertRaises(ExpressionError) as caught:
            scores('1000')
        self.assertEqual(caught.exception.code, 'SCORE_OUT_OF_RANGE')
        with self.assertRaises(ExpressionError) as caught:
            scores('0 - 1')
        self.assertEqual(caught.exception.code, 'SCORE_OUT_OF_RANGE')

    def test_a_text_answer_is_not_a_score(self):
        with self.assertRaises(ExpressionError) as caught:
            scores("'thirteen'")
        self.assertEqual(caught.exception.code, 'EXPRESSION_NOT_A_NUMBER')


class HostileExpressionTests(SimpleTestCase):
    """Everything a studio's expression must never be able to do."""

    def test_python_internals_are_unknown_names(self):
        self.assertEqual(refusal('__class__'), 'EXPRESSION_UNKNOWN_NAME')
        self.assertEqual(refusal('__import__'), 'EXPRESSION_UNKNOWN_NAME')
        self.assertEqual(refusal('os'), 'EXPRESSION_UNKNOWN_NAME')

    def test_member_access_reads_data_never_python_attributes(self):
        # A dict has a `keys` attribute and a `__class__`; neither is a field.
        self.assertEqual(scores('team.keys == null ? 1 : 0'), (1, 1))
        self.assertEqual(scores('team.__class__ == null ? 1 : 0'), (1, 1))
        self.assertEqual(scores("global.map.upper == null ? 1 : 0"), (1, 1))

    def test_no_calling_anything_that_is_not_listed(self):
        self.assertEqual(refusal('eval(1)'), 'EXPRESSION_UNKNOWN_FUNCTION')
        self.assertEqual(refusal('open(1)'), 'EXPRESSION_UNKNOWN_FUNCTION')
        self.assertEqual(refusal('competitors.pop()'), 'EXPRESSION_UNKNOWN_FUNCTION')
        self.assertEqual(refusal('team.score()'), 'EXPRESSION_UNKNOWN_FUNCTION')

    def test_statements_and_python_syntax_are_syntax_errors(self):
        for text in ('import os', 'team.score; 1', 'lambda: 1', '[1, 2]', '{1: 2}',
                     '1 if 2 else 3', 'a = 1', '10 ** 999', '"a" "b"', '1 +', ')',
                     'team..score', "'unterminated"):
            self.assertEqual(refusal(text), 'EXPRESSION_SYNTAX', text)

    def test_deep_nesting_is_refused(self):
        self.assertEqual(refusal('(' * 40 + '1' + ')' * 40), 'EXPRESSION_TOO_COMPLEX')
        self.assertEqual(refusal('-' * 40 + '1'), 'EXPRESSION_TOO_COMPLEX')

    def test_too_many_parts_is_refused(self):
        self.assertEqual(refusal('+'.join(['1'] * 150)), 'EXPRESSION_TOO_COMPLEX')

    def test_too_long_is_refused(self):
        self.assertEqual(refusal('1' + ' ' * 500), 'EXPRESSION_TOO_LONG')

    def test_empty_is_refused(self):
        self.assertEqual(refusal('   '), 'EXPRESSION_EMPTY')

    def test_dividing_by_nothing_is_a_code(self):
        self.assertEqual(refusal('1 / 0'), 'EXPRESSION_DIVIDE_BY_ZERO')
        self.assertEqual(refusal('1 % 0'), 'EXPRESSION_DIVIDE_BY_ZERO')

    def test_huge_numbers_are_refused(self):
        self.assertEqual(refusal('9e99'), 'EXPRESSION_OUT_OF_RANGE')
        self.assertEqual(refusal('999999999 * 999999999'), 'EXPRESSION_OUT_OF_RANGE')

    def test_a_list_too_long_to_walk_is_refused(self):
        env = studio_scoring.side_env({'lineups': [{'side': 1}, {'side': 2}],
                                       'competitors': [{'side': 1, 'k': 1}] * 600}, 1, {})
        self.assertEqual(refusal('competitors.sum(it.k)', env), 'EXPRESSION_TOO_COMPLEX')

    def test_arithmetic_on_text_is_a_code(self):
        self.assertEqual(refusal("'a' * 99999999"), 'EXPRESSION_NOT_A_NUMBER')
        self.assertEqual(refusal("team.score + 'a'"), 'EXPRESSION_NOT_A_NUMBER')

    def test_the_module_never_reaches_for_python_s_own_evaluator(self):
        path = os.path.join(os.path.dirname(studio_scoring.__file__), 'studio_scoring.py')
        with open(path, encoding='utf-8') as handle:
            source = handle.read()
        for word in ('eval(', 'exec(', 'compile(', 'getattr(', '__import__'):
            self.assertNotIn(word, source, word)

    def test_a_refusal_says_where(self):
        with self.assertRaises(ExpressionError) as caught:
            studio_scoring.compile_expression('team.score +* 2')
        self.assertEqual(caught.exception.code, 'EXPRESSION_SYNTAX')
        self.assertEqual(caught.exception.position, 12)


class SchemaCheckTests(SimpleTestCase):
    def test_a_plain_schema_passes(self):
        schema = {
            'type': 'object',
            'required': ['lineups'],
            'properties': {
                'lineups': {'type': 'array', 'minItems': 2, 'maxItems': 2,
                            'items': {'type': 'object', 'required': ['score'],
                                      'properties': {'score': {'type': 'integer', 'minimum': 0}}}},
            },
        }
        self.assertEqual(studio_schema.check_schema(schema), [])

    def test_a_keyword_we_do_not_validate_is_refused_rather_than_ignored(self):
        self.assertEqual(studio_schema.check_schema({'type': 'string', 'pattern': '^a'}),
                         ['SCHEMA_UNKNOWN_KEYWORD:pattern'])
        self.assertEqual(studio_schema.check_schema({'$ref': '#/x'}),
                         ['SCHEMA_UNKNOWN_KEYWORD:$ref'])

    def test_not_an_object(self):
        self.assertEqual(studio_schema.check_schema([1]), ['SCHEMA_NOT_OBJECT'])

    def test_bad_types(self):
        self.assertEqual(studio_schema.check_schema({'type': 'banana'}), ['SCHEMA_BAD_TYPE'])
        self.assertEqual(studio_schema.check_schema({'required': 'score'}), ['SCHEMA_BAD_TYPE'])
        self.assertEqual(studio_schema.check_schema({'minimum': 'zero'}), ['SCHEMA_BAD_TYPE'])

    def test_too_deep(self):
        schema = {'type': 'object'}
        node = schema
        for _ in range(15):
            node['properties'] = {'x': {'type': 'object'}}
            node = node['properties']['x']
        self.assertEqual(studio_schema.check_schema(schema), ['SCHEMA_TOO_DEEP'])

    def test_too_large(self):
        schema = {'type': 'object', 'properties': {('f%d' % i): {'type': 'string', 'description': 'x' * 50}
                                                   for i in range(200)}}
        self.assertEqual(studio_schema.check_schema(schema), ['SCHEMA_TOO_LARGE'])

    def test_an_empty_schema_accepts_anything(self):
        self.assertEqual(studio_schema.check_schema({}), [])
        self.assertEqual(studio_schema.validate({'x': 1}, {}), [])


class SchemaValidateTests(SimpleTestCase):
    SCHEMA = {
        'type': 'object',
        'required': ['lineups'],
        'additionalProperties': False,
        'properties': {
            'lineups': {'type': 'array', 'items': {
                'type': 'object', 'required': ['score'],
                'properties': {'score': {'type': 'integer', 'minimum': 0, 'maximum': 50},
                               'side': {'enum': [1, 2]}}}},
            'competitors': {'type': 'array'},
            'general': {'type': 'object', 'properties': {
                'map': {'type': 'string', 'minLength': 2, 'maxLength': 20}}},
        },
    }

    def errors(self, instance):
        return [(e['path'], e['code']) for e in studio_schema.validate(instance, self.SCHEMA)]

    def test_a_good_result_has_no_errors(self):
        self.assertEqual(self.errors({'lineups': [{'score': 3, 'side': 1}, {'score': 1, 'side': 2}]}), [])

    def test_each_error_names_where(self):
        self.assertEqual(self.errors({}), [('$', 'REQUIRED')])
        self.assertEqual(self.errors({'lineups': [{'score': -1}]}), [('$.lineups[0].score', 'MINIMUM')])
        self.assertEqual(self.errors({'lineups': [{'score': 51}]}), [('$.lineups[0].score', 'MAXIMUM')])
        self.assertEqual(self.errors({'lineups': [{'score': 'x'}]}), [('$.lineups[0].score', 'TYPE')])
        self.assertEqual(self.errors({'lineups': [{'score': 1, 'side': 3}]}), [('$.lineups[0].side', 'ENUM')])
        self.assertEqual(self.errors({'lineups': [], 'extra': 1}), [('$.extra', 'ADDITIONAL')])
        self.assertEqual(self.errors({'lineups': [], 'general': {'map': 'B'}}),
                         [('$.general.map', 'MIN_LENGTH')])

    def test_true_is_not_an_integer(self):
        self.assertEqual(self.errors({'lineups': [{'score': True}]}), [('$.lineups[0].score', 'TYPE')])

    def test_a_whole_float_is_an_integer_and_a_fraction_is_not(self):
        self.assertEqual(self.errors({'lineups': [{'score': 3.0}]}), [])
        self.assertEqual(self.errors({'lineups': [{'score': 3.5}]}), [('$.lineups[0].score', 'TYPE')])

    def test_errors_stop_at_fifty(self):
        many = {'lineups': [{'score': -1}] * 80}
        self.assertEqual(len(studio_schema.validate(many, self.SCHEMA)), 50)


class EnvelopeTests(SimpleTestCase):
    def codes(self, result):
        return [(e['path'], e['code']) for e in studio_schema.envelope_problems(result)]

    def test_the_sample_is_a_good_envelope(self):
        self.assertEqual(self.codes(RESULT), [])

    def test_two_lineups_one_per_side(self):
        self.assertEqual(self.codes({'lineups': [{'side': 1}]}), [('$.lineups', 'TWO_LINEUPS')])
        self.assertEqual(self.codes({'lineups': [{'side': 1}, {'side': 1}]}),
                         [('$.lineups', 'TWO_LINEUPS')])
        self.assertEqual(self.codes({'lineups': [{'side': 1}, {'side': 3}]}),
                         [('$.lineups', 'TWO_LINEUPS')])

    def test_not_an_object(self):
        self.assertEqual(self.codes([1, 2]), [('$', 'TYPE')])

    def test_competitors_belong_to_a_side(self):
        bad = dict(RESULT, competitors=[{'side': 3}])
        self.assertEqual(self.codes(bad), [('$.competitors[0].side', 'ENUM')])

    def test_too_many_competitors(self):
        bad = dict(RESULT, competitors=[{'side': 1}] * 201)
        self.assertEqual(self.codes(bad), [('$.competitors', 'MAX_ITEMS')])

    def test_general_is_an_object(self):
        self.assertEqual(self.codes(dict(RESULT, general=[1])), [('$.general', 'TYPE')])

    def test_too_large(self):
        bad = dict(RESULT, general={'notes': 'x' * 20001})
        self.assertEqual(self.codes(bad), [('$', 'TOO_LARGE')])
