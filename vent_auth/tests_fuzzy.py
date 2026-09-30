"""The forgiving matcher, against the cases shared with src/lib/fuzzy.js (inbox 383)."""
import json
import os

from django.test import SimpleTestCase

from vent_auth import fuzzy

FIXTURES = os.path.join(os.path.dirname(__file__), 'fuzzy_fixtures.json')


class FuzzyFixtureTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(FIXTURES, encoding='utf-8') as fh:
            cls.cases = json.load(fh)

    def test_every_match_case_matches(self):
        for query, text in self.cases['match']:
            with self.subTest(query=query, text=text):
                self.assertGreaterEqual(fuzzy.score(query, text), fuzzy.MIN_SCORE)

    def test_every_none_case_scores_nothing(self):
        for query, text in self.cases['none']:
            with self.subTest(query=query, text=text):
                self.assertEqual(fuzzy.score(query, text), 0.0)

    def test_ranking_order(self):
        for query, expected in self.cases['order']:
            shuffled = list(reversed(expected)) + ['Nothing Alike']
            got = fuzzy.search([{'name': n} for n in shuffled], query, ['name'])
            self.assertEqual([row['name'] for row in got], expected)

    def test_empty_query_returns_everything_in_order(self):
        items = [{'name': 'b'}, {'name': 'a'}]
        self.assertEqual(fuzzy.search(items, '  ', ['name']), items)
