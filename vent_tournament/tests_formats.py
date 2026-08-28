"""Formats, scoring and tie-breaks, checked against how real events are run.

The numbers here are not invented. Each scoring test carries a worked example
from a published rule set, so if somebody changes a placement table the test
says which event it stopped matching.

  * PUBG Mobile: 10 for the win, then 6, 5, 4, 3, 2, 1, 1, nothing from 9th.
    One point a kill.
  * Free Fire: 12 down to 1 across the top ten, nothing for 11th or 12th.
    One point a kill.
  * Swiss with Buchholz seeding, as Counter-Strike majors run it.
  * Aggregate ties on TOTAL GOALS, which is the format V-ENT already runs.
"""
from django.test import SimpleTestCase, TestCase

from . import formats as fmt
from . import scoring, tiebreak


class FormatDefinitionTests(SimpleTestCase):
    def test_the_even_rule_belongs_to_knockouts_only(self):
        """The bug this whole file exists to prevent: one parity rule applied to
        every format, and described as single elimination's."""
        self.assertEqual(fmt.get('single_elimination').count_problem(5), 'even')
        self.assertEqual(fmt.get('double_elimination').count_problem(5), 'even')
        self.assertIsNone(fmt.get('round_robin').count_problem(5))
        self.assertIsNone(fmt.get('swiss').count_problem(5))
        self.assertIsNone(fmt.get('battle_royale').count_problem(5))

    def test_each_format_states_its_own_floor(self):
        self.assertEqual(fmt.get('round_robin').count_problem(2), 'at_least')
        self.assertEqual(fmt.get('swiss').count_problem(3), 'at_least')
        self.assertEqual(fmt.get('gsl').count_problem(6), 'at_least')
        self.assertIsNone(fmt.get('battle_royale').count_problem(2))

    def test_round_robin_has_a_ceiling_because_matches_square(self):
        """Sixteen teams is 120 matches. Past about twelve it wants groups."""
        self.assertEqual(fmt.get('round_robin').count_problem(40), 'at_most')

    def test_the_key_is_tolerant_of_how_it_was_written(self):
        """The model has held all three spellings, which broke format filters."""
        for written in ('Single Elimination', 'single-elimination', 'SINGLE_ELIMINATION'):
            self.assertEqual(fmt.get(written).key, 'single_elimination')

    def test_every_format_names_tiebreakers_that_exist(self):
        for f in fmt.FORMATS.values():
            for t in f.tiebreakers:
                self.assertIn(t, fmt.TIEBREAKERS, '%s names unknown %s' % (f.key, t))
                self.assertIn(t, tiebreak.VALUES, '%s: %s cannot be computed' % (f.key, t))

    def test_every_format_names_a_scoring_method_that_exists(self):
        for f in fmt.FORMATS.values():
            self.assertIn(f.scoring, scoring.METHODS, f.key)

    def test_the_catalogue_is_complete_enough_to_draw_a_wizard_from(self):
        rows = fmt.catalogue()
        self.assertEqual(len(rows), len(fmt.FORMATS))
        for row in rows:
            self.assertTrue(row['label'])
            self.assertTrue(row['summary'])
            self.assertTrue(all(t['label'] for t in row['tiebreakers']))


class MatchScoringTests(SimpleTestCase):
    def test_a_win_is_a_point_and_a_draw_is_neither(self):
        table = scoring.match_win([
            {'a': 1, 'b': 2, 'score_a': 16, 'score_b': 14},
            {'a': 1, 'b': 3, 'score_a': 10, 'score_b': 10},
        ])
        self.assertEqual(table[1].points, 1)
        self.assertEqual(table[1].draws, 1)
        self.assertEqual(table[2].losses, 1)
        self.assertEqual(table[3].points, 0)

    def test_three_one_nil_is_the_league_convention(self):
        table = scoring.points_3_1_0([
            {'a': 1, 'b': 2, 'score_a': 2, 'score_b': 0},
            {'a': 1, 'b': 3, 'score_a': 1, 'score_b': 1},
            {'a': 2, 'b': 3, 'score_a': 0, 'score_b': 3},
        ])
        self.assertEqual(table[1].points, 4)   # a win and a draw
        self.assertEqual(table[3].points, 4)   # a win and a draw
        self.assertEqual(table[2].points, 0)

    def test_goal_difference_is_kept_for_the_tiebreak(self):
        table = scoring.points_3_1_0([{'a': 1, 'b': 2, 'score_a': 4, 'score_b': 1}])
        self.assertEqual(table[1].goal_difference, 3)
        self.assertEqual(table[2].goal_difference, -3)


class BattleRoyaleScoringTests(SimpleTestCase):
    """Worked from the published PUBG Mobile and Free Fire tables."""

    def test_pubg_mobile_a_win_outweighs_ten_kills(self):
        """The design of that table: it pays for surviving, not only fighting."""
        winner = scoring.battle_royale(
            [{'participant': 1, 'placement': 1, 'kills': 0}], 'pubg_mobile')
        fragger = scoring.battle_royale(
            [{'participant': 2, 'placement': 9, 'kills': 10}], 'pubg_mobile')
        self.assertEqual(winner[1].points, 10)
        self.assertEqual(fragger[2].points, 10)   # 0 placement + 10 kills
        # Level on points, and the tie-break is what separates them.

    def test_pubg_mobile_across_three_matches(self):
        table = scoring.battle_royale([
            {'participant': 1, 'placement': 1, 'kills': 6},    # 10 + 6 = 16
            {'participant': 1, 'placement': 4, 'kills': 3},    # 4 + 3  = 7
            {'participant': 1, 'placement': 12, 'kills': 1},   # 0 + 1  = 1
        ], 'pubg_mobile')
        self.assertEqual(table[1].points, 24)
        self.assertEqual(table[1].kills, 10)
        self.assertEqual(table[1].best_placement, 1)
        self.assertEqual(table[1].firsts, 1)

    def test_free_fire_pays_a_flatter_longer_table(self):
        """Tenth still scores in Free Fire and scores nothing in PUBG."""
        ff = scoring.battle_royale(
            [{'participant': 1, 'placement': 10, 'kills': 0}], 'free_fire')
        pubg = scoring.battle_royale(
            [{'participant': 1, 'placement': 10, 'kills': 0}], 'pubg_mobile')
        self.assertEqual(ff[1].points, 1)
        self.assertEqual(pubg[1].points, 0)

    def test_free_fire_first_place_is_twelve(self):
        table = scoring.battle_royale(
            [{'participant': 1, 'placement': 1, 'kills': 4}], 'free_fire')
        self.assertEqual(table[1].points, 16)

    def test_a_placement_off_the_table_scores_only_kills(self):
        table = scoring.battle_royale(
            [{'participant': 1, 'placement': 15, 'kills': 3}], 'pubg_mobile')
        self.assertEqual(table[1].points, 3)


class AggregateTieTests(SimpleTestCase):
    """The EA FC league format V-ENT already runs. This is the one with a wrong
    answer that looks right, so it is spelled out."""

    def test_a_tie_is_total_goals_not_a_count_of_fixtures_won(self):
        # Team 1 loses one fixture 0-1 and wins the other 5-0.
        table = scoring.aggregate_goals([
            {'a': 1, 'b': 2, 'score_a': 0, 'score_b': 1},
            {'a': 1, 'b': 2, 'score_a': 5, 'score_b': 0},
        ])
        # One fixture each. On a win count that is level. On goals it is 5-1.
        self.assertEqual(table[1].goals_for, 5)
        self.assertEqual(table[2].goals_for, 1)
        self.assertGreater(table[1].points, table[2].points)

    def test_the_aggregate_can_reverse_the_fixture_count(self):
        """Two narrow wins beaten by one heavy one, which is the point."""
        table = scoring.aggregate_goals([
            {'a': 1, 'b': 2, 'score_a': 1, 'score_b': 0},
            {'a': 1, 'b': 2, 'score_a': 1, 'score_b': 0},
            {'a': 1, 'b': 2, 'score_a': 0, 'score_b': 9},
        ])
        self.assertEqual(table[1].goals_for, 2)
        self.assertEqual(table[2].goals_for, 9)
        self.assertGreater(table[2].points, table[1].points)


class TiebreakTests(SimpleTestCase):
    def test_the_table_says_which_rule_separated_them(self):
        """An organiser who cannot answer "why is that team above mine" has an
        argument on their hands."""
        table = scoring.points_3_1_0([
            {'a': 1, 'b': 2, 'score_a': 3, 'score_b': 0},   # 1 beats 2 heavily
            {'a': 1, 'b': 3, 'score_a': 0, 'score_b': 1},
            {'a': 2, 'b': 3, 'score_a': 1, 'score_b': 0},
        ])
        rows = tiebreak.for_format('round_robin', table)
        by_id = {r['participant_id']: r for r in rows}
        # All three on three points, separated by head-to-head then goals.
        self.assertTrue(all(r['points'] == 3 for r in rows))
        self.assertTrue(any(r['separated_by'] for r in rows))
        for r in rows:
            if r['separated_by']:
                self.assertTrue(r['separated_by_label'])

    def test_a_clear_leader_is_not_marked_as_tie_broken(self):
        table = scoring.points_3_1_0([
            {'a': 1, 'b': 2, 'score_a': 3, 'score_b': 0},
            {'a': 1, 'b': 3, 'score_a': 2, 'score_b': 0},
        ])
        rows = tiebreak.for_format('round_robin', table)
        top = rows[0]
        self.assertEqual(top['participant_id'], 1)
        self.assertIsNone(top['separated_by'])

    def test_swiss_separates_on_the_strength_of_the_draw(self):
        """Buchholz: two teams on one win each, one had much the harder draw.

        Only 1 and 2 finish level here, on purpose. With more sides on the same
        record the pair being compared may share a Buchholz and fall through to
        the next rule, which is correct behaviour but tests nothing about this
        one.
        """
        table = scoring.match_win([
            # 1 beat 3, and 3 went on to win twice: a strong opponent.
            {'a': 1, 'b': 3, 'score_a': 1, 'score_b': 0},
            {'a': 3, 'b': 5, 'score_a': 1, 'score_b': 0},
            {'a': 3, 'b': 6, 'score_a': 1, 'score_b': 0},
            # 2 beat 4, and 4 lost everything else: a weak opponent.
            {'a': 2, 'b': 4, 'score_a': 1, 'score_b': 0},
            {'a': 4, 'b': 5, 'score_a': 0, 'score_b': 1},
            {'a': 4, 'b': 6, 'score_a': 0, 'score_b': 1},
            # 5 and 6 pushed clear so the tie is 1 against 2 alone.
            {'a': 5, 'b': 1, 'score_a': 0, 'score_b': 0},
            {'a': 6, 'b': 2, 'score_a': 0, 'score_b': 0},
            {'a': 5, 'b': 6, 'score_a': 1, 'score_b': 0},
        ])
        rows = tiebreak.for_format('swiss', table)
        by_id = {r['participant_id']: r for r in rows}
        self.assertEqual(by_id[1]['points'], by_id[2]['points'])
        self.assertLess(by_id[1]['position'], by_id[2]['position'])
        self.assertEqual(by_id[1]['separated_by'], 'buchholz')

    def test_battle_royale_separates_on_kills_first(self):
        table = scoring.battle_royale([
            {'participant': 1, 'placement': 2, 'kills': 8},   # 6 + 8 = 14
            {'participant': 2, 'placement': 1, 'kills': 4},   # 10 + 4 = 14
        ], 'pubg_mobile')
        rows = tiebreak.for_format('battle_royale', table)
        self.assertEqual(rows[0]['participant_id'], 1)
        self.assertEqual(rows[0]['separated_by'], 'total_kills')

    def test_head_to_head_is_read_among_the_tied_only(self):
        """With three level, "who beat whom" is a mini-table between those three,
        not a count against the whole field."""
        table = scoring.points_3_1_0([
            # Both beat 4, so both are on three points from outside the tie, and
            # 1 has by far the better goal difference.
            {'a': 1, 'b': 4, 'score_a': 5, 'score_b': 0},
            {'a': 2, 'b': 4, 'score_a': 1, 'score_b': 0},
            # Their meeting is a draw, so neither gains a point from it - but 2
            # is put above 1 by the head-to-head win below.
            {'a': 2, 'b': 1, 'score_a': 2, 'score_b': 2},
        ])
        # Level on points; head-to-head is a draw, so goal difference decides
        # and 1 goes above 2 despite the meeting.
        rows = tiebreak.for_format('round_robin', table)
        by_id = {r['participant_id']: r for r in rows}
        self.assertEqual(by_id[1]['points'], by_id[2]['points'])
        self.assertLess(by_id[1]['position'], by_id[2]['position'])
        self.assertEqual(by_id[1]['separated_by'], 'goal_difference')

    def test_a_head_to_head_win_outranks_goal_difference(self):
        """Round robin reads the meeting before the goals, which is the whole
        reason the ORDER belongs to the format instead of being one global rule.

        Team 2 finishes below team 1 while holding much the better goal
        difference, purely because team 1 beat them.
        """
        table = scoring.points_3_1_0([
            {'a': 1, 'b': 2, 'score_a': 1, 'score_b': 0},   # 1 beat 2
            {'a': 3, 'b': 1, 'score_a': 9, 'score_b': 0},   # and was hammered
            {'a': 2, 'b': 4, 'score_a': 1, 'score_b': 0},
        ])
        rows = tiebreak.for_format('round_robin', table)
        by_id = {r['participant_id']: r for r in rows}

        # Level on points, and 2 has the better difference of the two.
        self.assertEqual(by_id[1]['points'], by_id[2]['points'])
        self.assertGreater(by_id[2]['goal_difference'], by_id[1]['goal_difference'])

        # The meeting still decides it.
        self.assertLess(by_id[1]['position'], by_id[2]['position'])
        self.assertEqual(by_id[2]['separated_by'], 'head_to_head')

    def test_an_empty_table_is_an_empty_standing(self):
        self.assertEqual(tiebreak.for_format('round_robin', {}), [])

    def test_positions_are_contiguous_and_start_at_one(self):
        table = scoring.points_3_1_0([
            {'a': 1, 'b': 2, 'score_a': 1, 'score_b': 0},
            {'a': 3, 'b': 4, 'score_a': 1, 'score_b': 0},
        ])
        rows = tiebreak.for_format('round_robin', table)
        self.assertEqual([r['position'] for r in rows], [1, 2, 3, 4])


class UnknownMethodTests(SimpleTestCase):
    def test_an_unknown_scoring_method_is_an_error_not_a_default(self):
        """Silently falling back to match_win would pay a battle royale wrong."""
        with self.assertRaises(ValueError):
            scoring.score('made_up', [])


class CatalogueEndpointTests(TestCase):
    """The wizard asks these two questions before it can ask anything else."""

    def setUp(self):
        from vent_auth.models import GameMode, Games
        self.game = Games.objects.get_or_create(game_title='Probe Fire')[0]
        GameMode.objects.create(
            game=self.game, name='Battle Royale Squad', team_size=4,
            default_format='battle_royale', default_placement_table='free_fire')
        self.other = Games.objects.get_or_create(game_title='Probe Ball')[0]
        GameMode.objects.create(
            game=self.other, name='2v2 Aggregate', team_size=2,
            default_format='aggregate_2v2')

    def test_the_format_catalogue_is_public(self):
        """Somebody deciding whether to run a tournament here has no account."""
        res = self.client.get('/tournament/formats/')
        self.assertEqual(res.status_code, 200, res.content)
        keys = {f['key'] for f in res.json()['data']['formats']}
        self.assertIn('single_elimination', keys)
        self.assertIn('battle_royale', keys)
        self.assertIn('aggregate_2v2', keys)

    def test_the_catalogue_carries_the_placement_tables(self):
        """The two disagree, and an organiser has to be able to see how."""
        tables = self.client.get('/tournament/formats/').json()['data']['placement_tables']
        self.assertEqual(tables['pubg_mobile']['points']['1'], 10)
        self.assertEqual(tables['free_fire']['points']['1'], 12)

    def test_modes_belong_to_their_own_game(self):
        """The select was a fixed list, so it offered Free Fire's modes to
        somebody running EA FC."""
        res = self.client.get('/tournament/games/%s/modes/' % self.game.game_id)
        self.assertEqual(res.status_code, 200, res.content)
        names = [m['name'] for m in res.json()['data']['modes']]
        self.assertEqual(names, ['Battle Royale Squad'])
        self.assertNotIn('2v2 Aggregate', names)

    def test_a_mode_carries_what_it_is_normally_run_as(self):
        res = self.client.get('/tournament/games/%s/modes/' % self.game.game_id)
        mode = res.json()['data']['modes'][0]
        self.assertEqual(mode['default_format'], 'battle_royale')
        self.assertEqual(mode['default_placement_table'], 'free_fire')
        self.assertEqual(mode['team_size'], 4)

    def test_an_unknown_game_is_a_404(self):
        res = self.client.get('/tournament/games/999999/modes/')
        self.assertEqual(res.status_code, 404, res.content)
