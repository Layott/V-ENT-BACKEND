"""The league table, checked against the spreadsheet it came from.

The fixture is the real thing: `CADE ESPORTS DIVISION 2 CALCULATOR 2026`, 189
fixtures across 19 matchdays, twelve players, one points deduction. The
expected numbers are the ones the spreadsheet computed, not ones I worked out
and then agreed with myself about.

That distinction is the whole value of this file. An engine tested against my
own arithmetic proves I can be consistent. Tested against a table the CEO has
been keeping by hand all season, it proves the table on the site will be the
table they already trust - and if it is not, the difference is a real
disagreement worth having before anybody relies on it.
"""
import json
import os

from django.test import SimpleTestCase

from vent_tournament import league_stats as ls

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures_cade.json')

STATUS_FROM_SHEET = {
    'Played': ls.PLAYED,
    'Walkover_Home': ls.WALKOVER_HOME,
    'Walkover_Away': ls.WALKOVER_AWAY,
    'Cancelled': ls.CANCELLED,
}


def load():
    with open(FIXTURE, encoding='utf-8') as handle:
        return json.load(handle)


def as_matches(raw):
    out = []
    for row in raw['matches']:
        out.append({
            'home': row['home'],
            'away': row['away'],
            'home_goals': row['hg'],
            'away_goals': row['ag'],
            'status': STATUS_FROM_SHEET.get(row['status'], ls.PLAYED),
        })
    return out


def as_settings(raw):
    """The sheet's Settings, in this module's names."""
    s = raw['settings']
    return ls.clean_settings({
        'points_win': int(s['Win Pts (Winner)']),
        'points_draw': int(s['Draw Pts (Each)']),
        'points_loss': int(s['Loss Pts (Loser) [optional]']),
        'walkover_points_winner': int(s['Walkover Pts (Winner)']),
        'walkover_points_loser': int(s['Walkover Pts (Loser)']),
        'walkover_goals_winner': int(s['Walkover Winner Goals For']),
        'walkover_goals_loser': int(s['Walkover Loser Goals For']),
    })[0]


class AgainstTheSpreadsheetTests(SimpleTestCase):
    """Every column, for every player, against what the sheet computed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raw = load()
        cls.settings = as_settings(cls.raw)
        cls.rows = ls.table(
            as_matches(cls.raw),
            adjustments=[{'player': a['player'], 'metric': a['metric'],
                          'value': a['value'], 'reason': a['reason']}
                         for a in cls.raw['adjustments']],
            settings=cls.settings)
        cls.by_name = {r['player']: r for r in cls.rows}
        cls.expected = [s for s in cls.raw['stats'] if s.get('P')]

    def test_the_fixture_is_the_real_league(self):
        """Guards against the fixture being quietly replaced by something
        smaller, which would make every assertion below meaningless."""
        self.assertEqual(len(self.raw['matches']), 189)
        self.assertGreaterEqual(len(self.expected), 10)
        self.assertEqual(len(self.raw['adjustments']), 1)

    def test_every_player_appears(self):
        for row in self.expected:
            self.assertIn(row['player'], self.by_name, row['player'])

    def test_matches_played(self):
        for row in self.expected:
            self.assertEqual(self.by_name[row['player']]['played'], row['P'],
                             row['player'])

    def test_wins_draws_losses(self):
        for row in self.expected:
            mine = self.by_name[row['player']]
            self.assertEqual(mine['wins'], row['W'], '%s W' % row['player'])
            self.assertEqual(mine['draws'], row['D'], '%s D' % row['player'])
            self.assertEqual(mine['losses'], row['L'], '%s L' % row['player'])

    def test_goals(self):
        for row in self.expected:
            mine = self.by_name[row['player']]
            self.assertEqual(mine['goals_for'], row['GF'],
                             '%s GF' % row['player'])
            self.assertEqual(mine['goals_against'], row['GA'],
                             '%s GA' % row['player'])
            self.assertEqual(mine['goal_difference'], row['GD'],
                             '%s GD' % row['player'])

    def test_points(self):
        for row in self.expected:
            mine = self.by_name[row['player']]
            self.assertEqual(mine['points_from_matches'], row['PtsMatches'],
                             '%s points from matches' % row['player'])
            self.assertEqual(mine['points'], row['TotalPts'],
                             '%s total points' % row['player'])

    def test_position(self):
        for row in self.expected:
            self.assertEqual(self.by_name[row['player']]['position'],
                             row['Pos'], '%s position' % row['player'])

    def test_clean_sheets(self):
        for row in self.expected:
            self.assertEqual(self.by_name[row['player']]['clean_sheets'],
                             row['CleanSheets'], '%s clean sheets' % row['player'])

    def test_averages_and_win_rate(self):
        for row in self.expected:
            mine = self.by_name[row['player']]
            self.assertAlmostEqual(mine['average_goals_for'], row['AvgGF'], 6,
                                   '%s avg GF' % row['player'])
            self.assertAlmostEqual(mine['average_goals_against'], row['AvgGA'], 6,
                                   '%s avg GA' % row['player'])
            self.assertAlmostEqual(mine['win_rate'], row['WinRate'], 6,
                                   '%s win rate' % row['player'])

    def test_biggest_win_and_loss(self):
        for row in self.expected:
            mine = self.by_name[row['player']]
            self.assertEqual(mine['biggest_win'], row['BiggestWin'],
                             '%s biggest win' % row['player'])
            self.assertEqual(mine['biggest_loss'], row['BiggestLoss'],
                             '%s biggest loss' % row['player'])

    def test_walkovers(self):
        for row in self.expected:
            mine = self.by_name[row['player']]
            self.assertEqual(mine['walkovers_given'], row['WoGiven'],
                             '%s walkovers given' % row['player'])
            self.assertEqual(mine['walkovers_received'], row['WoReceived'],
                             '%s walkovers received' % row['player'])

    def test_points_per_game(self):
        for row in self.expected:
            self.assertAlmostEqual(self.by_name[row['player']]['points_per_game'],
                                   row['PtsPerGame'], 6, row['player'])

    def test_the_deduction_lands_and_keeps_its_reason(self):
        """A deduction is a decision somebody defends later, so the reason
        travels with the number rather than being remembered."""
        row = self.by_name['WOLEVATION']
        self.assertTrue(row['adjustments'])
        entry = row['adjustments'][0]
        self.assertEqual(entry['metric'], 'GF')
        self.assertEqual(entry['value'], -3)
        self.assertIn('quit', entry['reason'].lower())


class TheChoicesChangeTheTableTests(SimpleTestCase):
    """Each setting is proven by the table moving, not by it being stored.

    A setting that is saved and never read is the most expensive kind: the
    organiser believes the league runs one way and it runs another.
    """

    def setUp(self):
        self.matches = [
            {'home': 'A', 'away': 'B', 'home_goals': 2, 'away_goals': 0,
             'status': ls.PLAYED},
            {'home': 'A', 'away': 'C', 'status': ls.WALKOVER_HOME},
            {'home': 'B', 'away': 'C', 'home_goals': 1, 'away_goals': 1,
             'status': ls.PLAYED},
            {'home': 'C', 'away': 'A', 'status': ls.CANCELLED},
        ]

    def run_with(self, **overrides):
        settings, errors = ls.clean_settings(overrides)
        self.assertEqual(errors, [])
        return {r['player']: r for r in ls.table(self.matches, settings=settings)}

    def test_a_cancelled_match_counts_for_nothing(self):
        """Not a draw, not a loss, not a played game."""
        rows = self.run_with()
        self.assertEqual(rows['A']['played'], 2)
        self.assertEqual(rows['C']['played'], 2)

    def test_walkover_goals_can_be_kept_out_of_the_goal_columns(self):
        with_goals = self.run_with(walkover_goals_count=True)
        without = self.run_with(walkover_goals_count=False)
        self.assertEqual(with_goals['A']['goals_for'], 5)   # 2 played + 3 given
        self.assertEqual(without['A']['goals_for'], 2)
        # The points are unaffected either way: only the goals were notional.
        self.assertEqual(with_goals['A']['points'], without['A']['points'])

    def test_a_walkover_need_not_count_as_a_game_played(self):
        counted = self.run_with(walkover_counts_as_played=True)
        not_counted = self.run_with(walkover_counts_as_played=False)
        self.assertEqual(counted['A']['played'], 2)
        self.assertEqual(not_counted['A']['played'], 1)
        # And the averages move with it, which is the point of the setting.
        self.assertNotEqual(counted['A']['points_per_game'],
                            not_counted['A']['points_per_game'])

    def test_a_walkover_clean_sheet_can_be_refused(self):
        counted = self.run_with(clean_sheet_includes_walkover=True)
        refused = self.run_with(clean_sheet_includes_walkover=False)
        self.assertEqual(counted['A']['clean_sheets'], 2)
        self.assertEqual(refused['A']['clean_sheets'], 1)

    def test_win_rate_can_give_a_draw_half_credit(self):
        plain = self.run_with(win_rate_method=ls.WIN_RATE_WINS)
        halved = self.run_with(win_rate_method=ls.WIN_RATE_WITH_DRAWS)
        self.assertEqual(plain['B']['win_rate'], 0.0)
        self.assertEqual(halved['B']['win_rate'], 0.25)   # one draw of two

    def test_biggest_win_by_margin_or_by_goals(self):
        matches = [{'home': 'A', 'away': 'B', 'home_goals': 9, 'away_goals': 2,
                    'status': ls.PLAYED},
                   {'home': 'A', 'away': 'C', 'home_goals': 8, 'away_goals': 0,
                    'status': ls.PLAYED}]
        by_margin, _ = ls.clean_settings({'biggest_win_method': ls.BIGGEST_BY_MARGIN})
        by_goals, _ = ls.clean_settings({'biggest_win_method': ls.BIGGEST_BY_GOALS})
        margin = {r['player']: r for r in ls.table(matches, settings=by_margin)}
        goals = {r['player']: r for r in ls.table(matches, settings=by_goals)}
        self.assertEqual(margin['A']['biggest_win'], 8)   # 8-0
        self.assertEqual(goals['A']['biggest_win'], 9)    # 9-2

    def test_the_form_window_is_the_organisers(self):
        rows = self.run_with(form_window=1)
        self.assertEqual(len(rows['A']['form']), 1)

    def test_form_is_scored_out_of_what_a_perfect_run_is_worth(self):
        """The sheet divides by a hardcoded 15, which is five wins at three
        points. A league where a win is worth 2 would read over 100%."""
        rows = self.run_with(points_win=2, form_window=5)
        self.assertLessEqual(rows['A']['form_score'], 100)

    def test_walkover_points_are_separate_from_win_points(self):
        rows = self.run_with(points_win=3, walkover_points_winner=1)
        # One played win at 3, one walkover at 1.
        self.assertEqual(rows['A']['points'], 4)


class SettingsAreValidatedTests(SimpleTestCase):
    def test_an_unknown_setting_is_refused(self):
        _settings, errors = ls.clean_settings({'points_for_vibes': 3})
        self.assertTrue(errors)

    def test_an_invalid_method_is_refused_rather_than_defaulted(self):
        """Silently substituting a rule the organiser did not choose is how a
        table ends up disagreeing with the rules the league was told."""
        _settings, errors = ls.clean_settings({'win_rate_method': 'vibes'})
        self.assertTrue(errors)

    def test_a_number_sent_as_words_is_refused(self):
        _settings, errors = ls.clean_settings({'points_win': 'three'})
        self.assertTrue(errors)

    def test_a_negative_form_window_is_refused(self):
        _settings, errors = ls.clean_settings({'form_window': 0})
        self.assertTrue(errors)

    def test_defaults_are_the_spreadsheets(self):
        settings, errors = ls.clean_settings({})
        self.assertEqual(errors, [])
        self.assertEqual(settings['points_win'], 3)
        self.assertEqual(settings['points_draw'], 1)
        self.assertEqual(settings['walkover_goals_winner'], 3)
        self.assertTrue(settings['walkover_goals_count'])


class HeadToHeadTests(SimpleTestCase):
    def test_only_the_matches_between_the_two(self):
        matches = [
            {'home': 'A', 'away': 'B', 'home_goals': 3, 'away_goals': 1,
             'status': ls.PLAYED},
            {'home': 'B', 'away': 'A', 'home_goals': 2, 'away_goals': 2,
             'status': ls.PLAYED},
            {'home': 'A', 'away': 'C', 'home_goals': 5, 'away_goals': 0,
             'status': ls.PLAYED},
        ]
        result = ls.head_to_head(matches, 'A', 'B')
        self.assertEqual(result['matches'], 2)
        self.assertEqual(result['A']['goals_for'], 5)     # 3 + 2, not 10
        self.assertEqual(result['A']['points'], 4)
