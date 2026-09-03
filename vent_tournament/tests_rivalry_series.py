# -*- coding: utf-8 -*-
"""The Rivalry Series, end to end, in the shape it is actually run.

CEO, 3 September 2026: "DO A FULL TEST WITH THE STRUCTURE OF THE RIVALRY
SERIES, IN ONE OF THE TEST SETUPS, ASSUME THESE ARE THE ELEMENTS THAT WILL ALL
BE USED PLEASE TEST THEM ALL."

The shape, from the CEO's own specification:

  Five nations, TWO SEATS each. A MATCH is one player against one player; a
  FIXTURE is one nation against one nation, made of two matches. Every pair
  meets once: 10 fixtures, 20 matches, 4 fixtures each, 12 points available.

  A fixture is decided on goals ADDED across both matches, NEVER on matches
  won. 3-0 then 0-2 is 3-2 and the side that lost one of the two takes it.
  Counting matches won calls that a draw.

  Two tables, always. Nations on the fixture, individuals on their own match. A
  player can win their match while their country loses the fixture, and both
  have to record it.

And, since 3 September, each nation is a SQUAD: two players who belong to two
different clubs, playing together for their country while still representing
the club they actually play for. That is the whole reason squads exist, and
this is the tournament they were built for.

Every graphic that would be on air for this is driven from this data and
asserted, because "assume these are the elements that will all be used" means
the test has to use them all.
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, Teams, TeamMembers, Users
from vent_tournament import presentation
from vent_tournament.models import (
    BracketMatch, BroadcastElement, LeagueRules, SquadMember, TieFixture,
    Tournament, TournamentRegistration, TournamentSquad)
from vent_tournament.services import league


#: The five nations, and for each the two players and the CLUB each plays for.
#: Deliberately two different clubs per nation: that is the case that broke the
#: old model, where a nation had to be either a fake club or two lone players.
NATIONS = [
    ('Nigeria', 'NGA', [('tolu', 'Lagos Lions'), ('zainab', 'Abuja Aces')]),
    ('Ghana', 'GHA', [('kwame', 'Accra Arrows'), ('ama', 'Kumasi Kings')]),
    ('Kenya', 'KEN', [('otieno', 'Nairobi Nine'), ('wanjiru', 'Mombasa Made')]),
    ('Egypt', 'EGY', [('hassan', 'Cairo Crown'), ('nour', 'Giza Giants')]),
    ('Senegal', 'SEN', [('mamadou', 'Dakar Dons'), ('aminata', 'Thies Thunder')]),
]


@override_settings(FRONTEND_URL='https://v-ent.co')
class RivalrySeriesTests(TestCase):

    def setUp(self):
        self.organiser = Users.objects.create(
            username='rivalry_org', email='rivalry@vent.test', is_active=True,
            login_session_token='rivalryorgtoken1')
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer ' + self.organiser.login_session_token}
        self.game = Games.objects.create(game_title='EA FC 26')

        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Rivalry Series', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=3),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False, bracket_type='round_robin')

        # Without a LeagueRules row a tournament generates a PLAIN round robin,
        # fixtures with nothing inside them, and no sign of why. The memory
        # records that as a trap; here it is the setup.
        LeagueRules.objects.create(
            tournament=self.tournament,
            points_win=3, points_draw=1, points_loss=0,
            players_per_team=2,
            tiebreakers=['goal_difference', 'goals_for'])

        self.ref = self.tournament.slug or self.tournament.tournament_id
        self.squads = {}
        self.players = {}
        self.build_the_nations()

    # ------------------------------------------------------------- setting up

    def build_the_nations(self):
        """Five squads of two, each player from a different club."""
        for name, tag, roster in NATIONS:
            squad = TournamentSquad.objects.create(
                tournament=self.tournament, name=name, tag=tag,
                created_by=self.organiser)
            for username, club_name in roster:
                player = Users.objects.create(
                    username=username, email='%s@vent.test' % username,
                    is_active=True)
                club = Teams.objects.create(
                    team_name=club_name, game=self.game, team_creator=player,
                    team_owner=player, description='', penalty_points=0,
                    number_of_members=1)
                TeamMembers.objects.create(team=club, user=player)
                SquadMember.objects.create(
                    squad=squad, user=player, represents_team=club,
                    represents_name=club.team_name)
                self.players[username] = player
            TournamentRegistration.objects.create(
                tournament=self.tournament, squad=squad, status='confirmed')
            self.squads[name] = squad

    def registration(self, nation):
        return TournamentRegistration.objects.get(squad=self.squads[nation])

    def feed(self):
        res = self.client.get('/tournament/%s/overlay-feed/' % self.ref)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    # --------------------------------------------------------- the structure

    def test_five_nations_of_two_players_from_ten_different_clubs(self):
        self.assertEqual(TournamentSquad.objects.filter(
            tournament=self.tournament).count(), 5)
        self.assertEqual(SquadMember.objects.filter(
            squad__tournament=self.tournament).count(), 10)

        # Ten players, ten DIFFERENT clubs. This is the case the old model
        # could not express at all.
        clubs = {m.represents_name for m in SquadMember.objects.filter(
            squad__tournament=self.tournament)}
        self.assertEqual(len(clubs), 10, 'each player should keep their own club')

        nigeria = self.squads['Nigeria']
        self.assertEqual(
            sorted(m.represents_name for m in nigeria.members.all()),
            ['Abuja Aces', 'Lagos Lions'])

    def test_the_draw_is_ten_fixtures_of_two_seats(self):
        from vent_tournament.services import bracket
        bracket.generate(self.tournament, self.organiser)

        fixtures = BracketMatch.objects.filter(tournament=self.tournament)
        self.assertEqual(fixtures.count(), 10,
                         'five nations meeting once each is ten fixtures')

        seats = TieFixture.objects.filter(tie__tournament=self.tournament)
        self.assertEqual(seats.count(), 20, 'two seats in every fixture')

        # Every nation plays four.
        for name in self.squads:
            reg = self.registration(name)
            played = (fixtures.filter(participant_1=reg).count()
                      + fixtures.filter(participant_2=reg).count())
            self.assertEqual(played, 4, '%s should play four fixtures' % name)

    # ------------------------------------------------- the rule people get wrong

    def test_the_worked_example_a_side_that_lost_a_match_takes_the_fixture(self):
        """3-0 then 0-2 is 3-2. Counting matches won calls it a draw."""
        from vent_tournament.services import bracket
        bracket.generate(self.tournament, self.organiser)

        tie = BracketMatch.objects.filter(tournament=self.tournament).first()
        seats = list(TieFixture.objects.filter(tie=tie).order_by('slot'))
        self.assertEqual(len(seats), 2)

        seats[0].goals_1, seats[0].goals_2, seats[0].status = 3, 0, 'completed'
        seats[0].save()
        seats[1].goals_1, seats[1].goals_2, seats[1].status = 0, 2, 'completed'
        seats[1].save()

        self.assertEqual(league.aggregate(tie), (3, 2))
        winner = league.settle(tie)
        self.assertEqual(winner, tie.participant_1,
                         'the side that lost one of the two still takes it')
        self.assertIsNotNone(winner, 'this is not a draw')

    def test_a_level_aggregate_is_a_draw_with_no_decider(self):
        from vent_tournament.services import bracket
        bracket.generate(self.tournament, self.organiser)
        tie = BracketMatch.objects.filter(tournament=self.tournament).first()
        seats = list(TieFixture.objects.filter(tie=tie).order_by('slot'))
        for seat, (a, b) in zip(seats, [(2, 1), (0, 1)]):
            seat.goals_1, seat.goals_2, seat.status = a, b, 'completed'
            seat.save()
        self.assertEqual(league.aggregate(tie), (2, 2))
        self.assertIsNone(league.settle(tie), 'a level aggregate is a draw')

    def test_a_half_played_tie_has_a_running_aggregate_but_does_not_settle(self):
        from vent_tournament.services import bracket
        bracket.generate(self.tournament, self.organiser)
        tie = BracketMatch.objects.filter(tournament=self.tournament).first()
        seats = list(TieFixture.objects.filter(tie=tie).order_by('slot'))
        seats[0].goals_1, seats[0].goals_2, seats[0].status = 4, 1, 'completed'
        seats[0].save()

        self.assertEqual(league.aggregate(tie), (4, 1),
                         'a live table shows the running aggregate')
        self.assertIsNone(league.settle(tie),
                          'a tie with a seat still to play must not close')

    # ------------------------------------------------------------ both tables

    def play_the_whole_series(self):
        """Every fixture played, with a mix of results including the worked one."""
        from vent_tournament.services import bracket
        bracket.generate(self.tournament, self.organiser)

        scores = [
            (3, 0, 0, 2),   # 3-2, the worked example
            (1, 1, 1, 1),   # 2-2, a draw
            (2, 0, 1, 0),   # 3-0
            (0, 1, 0, 3),   # 0-4
            (5, 1, 0, 0),   # 5-1
            (1, 2, 2, 1),   # 3-3, a draw
            (4, 0, 0, 1),   # 4-1
            (0, 0, 2, 3),   # 2-3
            (1, 0, 1, 0),   # 2-0
            (2, 2, 1, 1),   # 3-3, a draw
        ]
        ties = list(BracketMatch.objects.filter(
            tournament=self.tournament).order_by('id'))
        self.assertEqual(len(ties), len(scores))

        for tie, (a1, b1, a2, b2) in zip(ties, scores):
            seats = list(TieFixture.objects.filter(tie=tie).order_by('slot'))
            for seat, (a, b) in zip(seats, [(a1, b1), (a2, b2)]):
                seat.goals_1, seat.goals_2, seat.status = a, b, 'completed'
                seat.save()
            league.settle(tie)

    def test_the_nation_table_adds_up(self):
        self.play_the_whole_series()
        table = league.team_table(self.tournament)
        self.assertEqual(len(table), 5, 'a row per nation')

        for row in table:
            self.assertEqual(row['played'], 4,
                             '%s played %s, should be 4' % (row.get('name'), row['played']))

        # Ten fixtures: every one gives out 3 points or 2 (a draw gives 1 each).
        draws = sum(1 for t in BracketMatch.objects.filter(
            tournament=self.tournament) if t.winner_id is None
            and t.status == 'completed')
        expected_points = (10 - draws) * 3 + draws * 2
        self.assertEqual(sum(r['points'] for r in table), expected_points,
                         'the points handed out must match the results')

        # Goals for across the table equals goals against across the table.
        self.assertEqual(sum(r['goals_for'] for r in table),
                         sum(r['goals_against'] for r in table),
                         'every goal scored is a goal conceded by somebody')

    def test_the_player_table_records_a_player_who_won_while_their_country_lost(self):
        """The point of having two tables at all."""
        self.play_the_whole_series()

        players = league.player_table(self.tournament)
        self.assertEqual(len(players), 10, 'a row per player, not per nation')

        # In the worked example the losing nation's second seat won 2-0.
        tie = BracketMatch.objects.filter(
            tournament=self.tournament).order_by('id').first()
        self.assertIsNotNone(tie.winner_id)
        loser = (tie.participant_2 if tie.winner_id == tie.participant_1_id
                 else tie.participant_1)

        roster = {m.user.username for m in loser.squad.members.all()}
        by_name = {row['name']: row for row in players}
        won_anyway = [u for u in roster
                      if by_name.get(u, {}).get('won', 0) >= 1]
        self.assertTrue(
            won_anyway,
            'somebody in %s won their own match and the player table must say '
            'so. Rows: %s' % (loser.entrant_name,
                              {u: by_name.get(u) for u in roster}))

    def test_every_player_row_carries_the_club_they_actually_play_for(self):
        self.play_the_whole_series()
        teams = self.feed()['teams']
        self.assertEqual(len(teams), 5)

        seen = {}
        for team in teams:
            for player in team['players']:
                seen[player['ign']] = (team['name'], player['represents'])

        self.assertEqual(len(seen), 10)
        for _, _, roster in NATIONS:
            for username, club in roster:
                nation, represents = seen[username]
                self.assertEqual(
                    represents, club,
                    '%s plays for %s and the broadcast must say so, not %s'
                    % (username, club, represents))

    # --------------------------------------------- every graphic, on this data

    def start_broadcast(self):
        res = self.client.post('/tournament/%s/studio/sessions/' % self.ref,
                               data={'name': 'Rivalry Series, day 1'}, **self.auth)
        self.assertIn(res.status_code, (200, 201), res.content[:300])
        return res.json()['data']['session']

    def test_every_graphic_this_series_would_use_goes_on_air_with_real_data(self):
        self.play_the_whole_series()
        session = self.start_broadcast()

        nigeria = self.squads['Nigeria']
        ghana = self.squads['Ghana']

        # What an operator would actually put up across a broadcast day.
        wanted = {
            'intro': {'title': 'Rivalry Series', 'subtitle': 'Matchday 1'},
            'scorebar': {'home': nigeria.name, 'away': ghana.name,
                         'home_score': 3, 'away_score': 2,
                         'caption': 'Aggregate, seat 2'},
            'standings': {'title': 'Rivalry Series table', 'limit': 5},
            'lower_third': {'title': 'Tolu', 'subtitle': 'Nigeria, seat 1'},
            'player_card': {'player': 'tolu'},
            'sponsors': {'title': 'With thanks to'},
            'ticker': {'title': 'Results'},
            'bracket': {},
            'outro': {'title': 'Thanks for watching',
                      'subtitle': 'Matchday 2 tomorrow'},
        }

        for kind, payload in wanted.items():
            res = self.client.post(
                '/tournament/%s/studio/sessions/%s/element/%s/'
                % (self.ref, session['id'], kind),
                data={'active': True, 'payload': payload},
                content_type='application/json', **self.auth)
            self.assertEqual(res.status_code, 200,
                             '%s would not go on air: %s' % (kind, res.content[:200]))

        res = self.client.get('/studio/%s/feed/' % session['token'])
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']

        for kind, payload in wanted.items():
            element = data['elements'][kind]
            self.assertTrue(element['active'], '%s is not on air' % kind)
            for key, value in payload.items():
                self.assertEqual(
                    element['payload'].get(key), value,
                    '%s lost %s on the way to the feed' % (kind, key))
            self.assertIn(kind, session['urls'], '%s has no URL for OBS' % kind)

    def test_the_standings_a_graphic_draws_are_the_league_table(self):
        """The graphic and the table must not be two answers to one question."""
        self.play_the_whole_series()

        table = league.team_table(self.tournament)
        broadcast = self.feed()['teams']

        by_name = {row['name']: row for row in broadcast}
        # The feed calls goals `points_for` and `points_against`, because a
        # scorebar is not only football. Same numbers, so they must agree.
        pairs = [('played', 'played'), ('won', 'won'), ('lost', 'lost'),
                 ('goals_for', 'points_for'), ('goals_against', 'points_against')]
        for row in table:
            drawn = by_name.get(row['name'])
            self.assertIsNotNone(drawn, '%s is missing from the feed' % row['name'])
            for table_key, feed_key in pairs:
                self.assertEqual(
                    drawn[feed_key], row[table_key],
                    '%s: the table says %s=%s and the graphic says %s'
                    % (row['name'], table_key, row[table_key], drawn[feed_key]))

    def test_the_scorebar_can_name_both_nations_and_neither_is_substituted(self):
        """The fault found on production: an unmatched name became another team."""
        self.play_the_whole_series()
        teams = {t['name'] for t in self.feed()['teams']}
        self.assertEqual(teams, {n for n, _, _ in NATIONS})
        # Every nation is addressable by its own tag, which is what `?t=` uses.
        tags = {t['tag'] for t in self.feed()['teams']}
        self.assertEqual(tags, {tag for _, tag, _ in NATIONS})

    def test_the_presentation_options_all_work_on_this_tournament_too(self):
        session = self.start_broadcast()
        for entry in presentation.ENTRANCES:
            for exit_ in presentation.EXITS:
                res = self.client.post(
                    '/tournament/%s/studio/sessions/%s/element/scorebar/'
                    % (self.ref, session['id']),
                    data={'active': True,
                          'payload': {'options': {'entry': entry, 'exit': exit_}}},
                    content_type='application/json', **self.auth)
                self.assertEqual(res.status_code, 200,
                                 '%s/%s refused' % (entry, exit_))
        feed = self.client.get('/studio/%s/feed/' % session['token']).json()['data']
        look = feed['elements']['scorebar']['presentation']
        self.assertEqual(look['entry'], presentation.ENTRANCES[-1])
        self.assertEqual(look['exit'], presentation.EXITS[-1])
