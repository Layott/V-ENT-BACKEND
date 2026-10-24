"""How a team tie is decided (P3 W3c): total goals, most games won, or a crew
battle.

CEO, 24 October 2026: "make all brackets and formats live today and function
properly". The aggregate tie decided only on total goals; Dominant Duo
(research 7.2) is most games won, with a doubles game beside the singles, and a
crew battle (research 7.1) knocks a player out with every game lost until one
crew has nobody left.
"""
from django.test import TestCase

from . import stage_settings
from .models import LeagueRules, TieFixture
from .services import league
from .tests_stage_engine import add_stage, draw, field


def a_league(rule, seats=2, team_game=False, n=4):
    t, creator, regs = field(n, 'aggregate_2v2')
    LeagueRules.objects.create(tournament=t, players_per_team=seats)
    stage = add_stage(t, 0, 'aggregate_2v2',
                      settings={'tie_rule': rule, 'team_game': team_game})
    draw(stage, creator)
    tie = t.bracket_matches.filter(stage=stage, participant_2__isnull=False).order_by('id').first()
    return t, tie


def game(tie, slot, one, two):
    f = TieFixture.objects.get(tie=tie, slot=slot)
    f.goals_1, f.goals_2, f.status = one, two, 'completed'
    f.save()
    return league.settle(tie)


class SettingTests(TestCase):
    def test_goals_unless_set_and_anything_else_refused(self):
        self.assertEqual(stage_settings.clean('aggregate_2v2', {})['tie_rule'], 'goals')
        self.assertEqual(stage_settings.clean('aggregate_2v2', {'tie_rule': 'crew'})['tie_rule'], 'crew')
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('aggregate_2v2', {'tie_rule': 'stocks'})
        self.assertEqual((caught.exception.code, caught.exception.field), ('UNKNOWN_CHOICE', 'tie_rule'))

    def test_a_crew_battle_has_no_team_game(self):
        self.assertFalse(stage_settings.clean('aggregate_2v2',
                                              {'tie_rule': 'crew', 'team_game': True})['team_game'])


class GoalsTests(TestCase):
    def test_the_ceo_example_still_wins_on_goals(self):
        t, tie = a_league('goals')
        game(tie, 1, 3, 0)
        winner = game(tie, 2, 0, 2)
        self.assertEqual(winner, tie.participant_1)
        tie.refresh_from_db()
        self.assertEqual((tie.score_p1, tie.score_p2), (3, 2))


class DominantDuoTests(TestCase):
    def test_most_games_won_beats_more_goals(self):
        t, tie = a_league('sub_wins', team_game=True)
        # Two singles and the doubles: 3 fixtures.
        self.assertEqual(TieFixture.objects.filter(tie=tie).count(), 3)
        game(tie, 1, 5, 0)
        game(tie, 2, 0, 1)
        winner = game(tie, 3, 0, 1)
        # Six goals to two for side 1, and side 2 won two games of three.
        self.assertEqual(winner, tie.participant_2)
        tie.refresh_from_db()
        self.assertEqual((tie.score_p1, tie.score_p2), (1, 2))

    def test_not_settled_until_every_game_is_in(self):
        t, tie = a_league('sub_wins', team_game=True)
        game(tie, 1, 1, 0)
        self.assertIsNone(game(tie, 2, 1, 0))
        tie.refresh_from_db()
        self.assertNotEqual(tie.status, 'completed')
        self.assertEqual(league.aggregate(tie), (2, 0))


class CrewBattleTests(TestCase):
    def test_a_crew_of_three_is_at_most_five_games_with_no_fixed_seats(self):
        t, tie = a_league('crew', seats=3)
        fixtures = TieFixture.objects.filter(tie=tie)
        self.assertEqual(fixtures.count(), 5)
        self.assertFalse(fixtures.exclude(player_1=None).exists())

    def test_the_first_crew_to_knock_out_everybody_wins_and_the_rest_go(self):
        t, tie = a_league('crew', seats=3)
        game(tie, 1, 2, 0)
        game(tie, 2, 0, 1)
        game(tie, 3, 3, 0)
        winner = game(tie, 4, 1, 0)
        self.assertEqual(winner, tie.participant_1)
        tie.refresh_from_db()
        self.assertEqual((tie.status, tie.score_p1, tie.score_p2), ('completed', 3, 1))
        self.assertEqual(list(TieFixture.objects.filter(tie=tie).values_list('slot', flat=True)),
                         [1, 2, 3, 4])

    def test_a_corrected_game_reopens_the_battle_and_puts_the_games_back(self):
        t, tie = a_league('crew', seats=2)
        game(tie, 1, 1, 0)
        self.assertEqual(game(tie, 2, 1, 0), tie.participant_1)
        self.assertEqual(TieFixture.objects.filter(tie=tie).count(), 2)
        self.assertIsNone(game(tie, 2, 0, 1))
        tie.refresh_from_db()
        self.assertEqual(tie.status, 'in_progress')
        self.assertIsNone(tie.winner)
        self.assertEqual(TieFixture.objects.filter(tie=tie).count(), 3)


class TiePayloadTests(TestCase):
    def test_the_tie_says_how_it_is_decided(self):
        from rest_framework.test import APIClient

        t, tie = a_league('sub_wins', team_game=True)
        body = APIClient().get(f'/tournament/tie/{tie.pk}/').json()['data']
        self.assertEqual((body['tie_rule'], body['seats'], body['team_game']), ('sub_wins', 2, True))
        self.assertEqual(len(body['fixtures']), 3)
