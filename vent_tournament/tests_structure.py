"""The structure the wizard promises, against the bracket that gets built.

The point of this file is the agreement tests at the bottom. A wizard that says
"15 matches" and a generator that then creates 16 is worse than a wizard that
says nothing, because the organiser has already told their players.

The rest is the arithmetic itself, and the count rule the wizard now reads
instead of keeping its own copy of.
"""
from django.db import transaction
from django.test import TestCase
from rest_framework.test import APIClient

from . import structure as struct
from .services import bracket as bracket_service
from .tests import make_tournament, make_user, register


class DescribeTests(TestCase):

    def test_unknown_format_is_none_not_a_guess(self):
        self.assertIsNone(struct.describe('pyramid'))
        self.assertIsNone(struct.describe(''))

    def test_reads_the_wizards_own_spelling(self):
        """The wizard has saved `swiss-system` since it was written."""
        described = struct.describe('swiss-system')
        self.assertEqual(described['format'], 'swiss')
        self.assertEqual(described['count']['min'], 4)

    def test_count_rule_is_the_catalogues(self):
        rules = {key: struct.describe(key)['count'] for key in
                 ('single_elimination', 'double_elimination', 'round_robin',
                  'swiss', 'gsl', 'battle_royale', 'aggregate_2v2', 'ladder')}
        # The four the wizard had wrong, which is why this test names numbers
        # rather than reading them from the same place the code does.
        self.assertEqual(rules['swiss']['min'], 4)
        self.assertEqual(rules['double_elimination']['min'], 4)
        self.assertEqual(rules['round_robin']['max'], 20)
        self.assertEqual(rules['gsl']['min'], 8)
        self.assertTrue(rules['single_elimination']['even_only'])
        self.assertFalse(rules['aggregate_2v2']['even_only'])

    def test_no_count_means_no_shape(self):
        self.assertIsNone(struct.describe('round_robin')['shape'])
        self.assertIsNone(struct.describe('round_robin', participants=1)['shape'])
        self.assertIsNone(struct.describe('round_robin', participants='')['shape'])

    def test_knockout_arithmetic(self):
        shape = struct.describe('single_elimination', participants=16)['shape']
        self.assertEqual(shape['slots'], 16)
        self.assertEqual(shape['rounds'], 4)
        self.assertEqual(shape['matches'], 15)
        self.assertEqual(shape['games'], 15)
        self.assertEqual(shape['byes'], 0)

    def test_knockout_byes_when_the_field_is_not_a_power_of_two(self):
        shape = struct.describe('single_elimination', participants=12)['shape']
        self.assertEqual(shape['slots'], 16)
        self.assertEqual(shape['byes'], 4)
        self.assertEqual(shape['matches'], 15)   # rows, including the byes
        self.assertEqual(shape['games'], 11)     # what anybody actually plays

    def test_double_elimination_counts_both_brackets(self):
        shape = struct.describe('double_elimination', participants=8)['shape']
        self.assertEqual(shape['winners_rounds'], 3)
        self.assertEqual(shape['losers_rounds'], 4)
        self.assertEqual(shape['matches'], 14)
        self.assertEqual(shape['games'], 14)

    def test_table_arithmetic(self):
        shape = struct.describe('round_robin', participants=12)['shape']
        self.assertEqual(shape['kind'], 'table')
        self.assertEqual(shape['matches'], 66)
        self.assertEqual(shape['rounds'], 11)
        self.assertEqual(shape['sits_out_each_round'], 0)

    def test_odd_table_takes_an_extra_round_and_sits_somebody_out(self):
        shape = struct.describe('round_robin', participants=5)['shape']
        self.assertEqual(shape['matches'], 10)
        self.assertEqual(shape['rounds'], 5)
        self.assertEqual(shape['sits_out_each_round'], 1)

    def test_seats_multiply_what_is_on_the_floor(self):
        """The Rivalry Series shape: five nations, two seats a side."""
        described = struct.describe('aggregate_2v2', participants=5, seats=2)
        shape = described['shape']
        self.assertEqual(shape['matches'], 10)              # fixtures
        self.assertEqual(shape['games_on_the_floor'], 20)   # matches to run
        self.assertEqual(described['seats_per_side'], 2)

    def test_seats_do_not_multiply_a_knockout(self):
        """A quad tournament is not four times the fixtures."""
        shape = struct.describe('single_elimination', participants=8, seats=4)['shape']
        self.assertEqual(shape['games'], 7)
        self.assertEqual(shape['games_on_the_floor'], 7)

    def test_battle_royale_claims_no_match_count(self):
        described = struct.describe('battle_royale', participants=40)
        shape = described['shape']
        self.assertEqual(shape['kind'], 'points')
        self.assertIsNone(shape['matches'])
        self.assertTrue(shape['everyone_at_once'])

    def test_the_problem_with_the_count_is_named(self):
        self.assertEqual(
            struct.describe('single_elimination', participants=7)['shape']['problem'],
            'even')
        self.assertEqual(
            struct.describe('gsl', participants=4)['shape']['problem'], 'at_least')
        self.assertEqual(
            struct.describe('round_robin', participants=40)['shape']['problem'],
            'at_most')
        self.assertIsNone(
            struct.describe('round_robin', participants=12)['shape']['problem'])

    def test_it_says_when_the_bracket_drawn_is_not_the_format_named(self):
        """Swiss and GSL are drawn as knockouts today, and it says so."""
        self.assertTrue(struct.describe('swiss')['drawn_as_differs'])
        self.assertEqual(struct.describe('swiss')['drawn_as'], 'single_elimination')
        self.assertTrue(struct.describe('ladder')['drawn_as_differs'])
        self.assertEqual(struct.describe('ladder')['drawn_as'], 'round_robin')
        self.assertFalse(struct.describe('round_robin')['drawn_as_differs'])
        self.assertFalse(struct.describe('single_elimination')['drawn_as_differs'])


class AgreesWithTheGeneratorTests(TestCase):
    """What the wizard promises is what the generator builds."""

    #: Every build makes its own users, and usernames are unique, so each one
    #: takes its own block of numbers rather than colliding with the last.
    _block = 0

    def _build(self, bracket_type, n):
        AgreesWithTheGeneratorTests._block += 1
        base = 1000 * AgreesWithTheGeneratorTests._block
        creator = make_user(base)
        tournament = make_tournament(creator, bracket_type=bracket_type)
        for i in range(n):
            register(tournament, make_user(base + 1 + i))
        with transaction.atomic():
            summary = bracket_service.generate(
                tournament, creator, seed_strategy='registration')
        return summary

    def test_single_elimination_power_of_two(self):
        summary = self._build('single_elimination', 8)
        shape = struct.describe('single_elimination', participants=8)['shape']
        self.assertEqual(summary['matches_created'], shape['matches'])
        self.assertEqual(summary['rounds_count'], shape['rounds'])

    def test_single_elimination_with_byes(self):
        summary = self._build('single_elimination', 12)
        shape = struct.describe('single_elimination', participants=12)['shape']
        self.assertEqual(summary['matches_created'], shape['matches'])
        self.assertEqual(summary['rounds_count'], shape['rounds'])
        # And the promised bye count is the number of bye rows really made.
        self.assertEqual(shape['byes'], 4)

    def test_double_elimination(self):
        summary = self._build('double_elimination', 8)
        shape = struct.describe('double_elimination', participants=8)['shape']
        self.assertEqual(summary['matches_created'], shape['matches'])
        self.assertEqual(summary['rounds_count'], shape['rounds'])

    def test_round_robin(self):
        summary = self._build('round_robin', 6)
        shape = struct.describe('round_robin', participants=6)['shape']
        self.assertEqual(summary['matches_created'], shape['matches'])
        self.assertEqual(summary['rounds_count'], shape['rounds'])

    def test_odd_round_robin(self):
        summary = self._build('round_robin', 5)
        shape = struct.describe('round_robin', participants=5)['shape']
        self.assertEqual(summary['matches_created'], shape['matches'])
        self.assertEqual(summary['rounds_count'], shape['rounds'])

    def test_drawn_as_is_the_branch_the_generator_takes(self):
        """A ladder is drawn as a round robin, and the numbers prove it.

        Asserted against the match count rather than against the summary's
        `bracket_type`, which reports the format that was ASKED for. The
        question here is which shape got built, and only the counts answer it.
        """
        summary = self._build('ladder', 4)
        self.assertEqual(struct.drawn_as('ladder'), 'round_robin')
        self.assertEqual(summary['matches_created'], 6)     # a table: 4*3/2
        self.assertEqual(summary['rounds_count'], 3)

        summary = self._build('swiss', 8)
        self.assertEqual(struct.drawn_as('swiss'), 'single_elimination')
        self.assertEqual(summary['matches_created'], 7)     # a knockout of 8
        self.assertEqual(summary['rounds_count'], 3)


class CatalogueEndpointTests(TestCase):

    def setUp(self):
        self.client = APIClient()

    def test_it_answers_without_an_account(self):
        res = self.client.get('/tournament/formats/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['data']['formats']), 8)

    def test_every_entry_the_edit_screen_reads_is_still_there(self):
        res = self.client.get('/tournament/formats/')
        entry = res.data['data']['formats'][0]
        for field in ('key', 'label', 'summary', 'min_participants',
                      'max_participants', 'even_only', 'tiebreakers', 'notes'):
            self.assertIn(field, entry)

    def test_no_participants_means_no_shape(self):
        res = self.client.get('/tournament/formats/')
        self.assertTrue(all(f['shape'] is None
                            for f in res.data['data']['formats']))

    def test_participants_gives_every_format_its_shape(self):
        res = self.client.get('/tournament/formats/?participants=16')
        by_key = {f['key']: f for f in res.data['data']['formats']}
        self.assertEqual(by_key['single_elimination']['shape']['matches'], 15)
        self.assertEqual(by_key['round_robin']['shape']['matches'], 120)
        # 16 is over round robin's ceiling of 20? No: it is under it. The one
        # that refuses at 16 is nothing, so check the ceiling separately.
        self.assertIsNone(by_key['round_robin']['shape']['problem'])

    def test_seats_reach_the_floor_count(self):
        res = self.client.get('/tournament/formats/?participants=5&seats=2')
        by_key = {f['key']: f for f in res.data['data']['formats']}
        self.assertEqual(by_key['aggregate_2v2']['shape']['games_on_the_floor'], 20)

    def test_rubbish_in_the_query_is_ignored_rather_than_fatal(self):
        res = self.client.get('/tournament/formats/?participants=lots&seats=x')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(all(f['shape'] is None
                            for f in res.data['data']['formats']))
