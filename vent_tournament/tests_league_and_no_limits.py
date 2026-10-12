"""A League of its own, and no ceiling on how many enter.

CEO, 12 October 2026:

    "Normal league format should be available also, league format is the one
    where every player plays against other players." (inbox 440)

    "Remove the limit on 64 players/teams registeration for touraen creation,
    no more limits. Everyone can run unlimited events." (inbox 441)
"""
from collections import Counter

from django.db import transaction
from django.test import TestCase

from . import formats as fmt
from . import options, stage_settings, structure
from .models import BracketMatch
from .services import bracket as bracket_service
from .tests_stage_engine import field


class LeagueIsAFormatTests(TestCase):

    def test_the_catalogue_offers_league_by_that_name(self):
        entry = next(e for e in fmt.catalogue() if e['key'] == 'league')
        self.assertEqual(entry['label'], 'League')
        self.assertIsNone(entry['max_participants'])

    def test_league_is_no_longer_read_as_round_robin(self):
        self.assertEqual(fmt.get('league').key, 'league')
        self.assertEqual(fmt.get('League').key, 'league')
        self.assertEqual(fmt.get('round_robin').key, 'round_robin')

    def test_a_league_is_home_and_away_unless_told_once(self):
        self.assertEqual(stage_settings.clean('league', {})['legs'], 2)
        self.assertEqual(stage_settings.clean('league', {'legs': 0})['legs'], 2)
        self.assertEqual(stage_settings.clean('league', {'legs': 1})['legs'], 1)
        self.assertEqual(stage_settings.clean('round_robin', {})['legs'], 1)
        self.assertEqual(stage_settings.clean('round_robin', {'legs': 2})['legs'], 2)

    def test_the_option_is_kept_and_bounded(self):
        self.assertEqual(options.clean({'legs': 1})['legs'], 1)
        self.assertEqual(options.clean({'legs': 9})['legs'], 2)
        self.assertEqual(options.clean({})['legs'], 0)

    def test_the_preview_counts_both_legs(self):
        home_away = structure.describe('league', 10)['shape']
        self.assertEqual(home_away['matches'], 90)
        self.assertEqual(home_away['rounds'], 18)
        once = structure.describe('league', 10, settings={'legs': 1})['shape']
        self.assertEqual(once['matches'], 45)

    def test_a_four_team_league_draws_twelve_matches_every_pair_twice_sides_swapped(self):
        t, creator, regs = field(4, bracket_type='league')
        with transaction.atomic():
            bracket_service.generate(t, creator)
        matches = list(BracketMatch.objects.filter(tournament=t))
        self.assertEqual(len(matches), 12)
        pairs = Counter(frozenset((m.participant_1_id, m.participant_2_id)) for m in matches)
        self.assertEqual(set(pairs.values()), {2})
        ordered = Counter((m.participant_1_id, m.participant_2_id) for m in matches)
        self.assertEqual(set(ordered.values()), {1})

    def test_a_league_set_to_once_draws_six(self):
        t, creator, regs = field(4, bracket_type='league')
        t.options = dict(t.options or {}, legs=1)
        t.save(update_fields=['options'])
        with transaction.atomic():
            bracket_service.generate(t, creator)
        self.assertEqual(BracketMatch.objects.filter(tournament=t).count(), 6)


class NoCeilingTests(TestCase):

    def test_no_format_refuses_a_big_field_except_page_playoff(self):
        for key in ('round_robin', 'league', 'stepladder', 'winner_stays_on',
                    'single_elimination', 'swiss', 'ladder'):
            self.assertIsNone(fmt.get(key).count_problem(200), key)
        # Exactly four is what a Page playoff is, not a limit on it.
        self.assertEqual(fmt.get('page_playoff').count_problem(5), 'at_most')

    def test_the_premium_list_no_longer_sells_more_than_64_entrants(self):
        from vent_auth import premium
        self.assertNotIn('unlimited_size', premium.FEATURES)
        self.assertFalse(any('64' in text for text in premium.FEATURES.values()))

    def test_sixty_four_is_not_the_largest_round_robin(self):
        shape = structure.describe('round_robin', 100)['shape']
        self.assertEqual(shape['matches'], 100 * 99 // 2)
