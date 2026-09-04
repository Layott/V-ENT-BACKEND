# -*- coding: utf-8 -*-
"""The Rivalry Series stream elements, and what the feed has to carry for them.

CEO, 4 September 2026, sending the STREAM ELEMENTS tab of the event flow:
"these are all the overlays that will be used, please you can recreate them in
a way good for the site, that they will be usable on the production studio, we
need to use them in like 2hours, please make sure they are intrgrate with the
tournament model and show information based of proper stats from the
tournament."

Eight new graphics, and every one of them is about a FIXTURE rather than a
match. That is the whole difficulty: in this format a fixture is two matches
added together, a side can lose a match and take the tie, and a graphic that
knows only about matches tells the audience the opposite of what happened.

So the assertions here are mostly about the seam rather than about arithmetic.
The arithmetic is `services.league`'s and is pinned by `tests_aggregate.py` and
`tests_rivalry_series.py`; what is new is whether it reaches a browser source
in the shape a graphic draws, and whether the version moves when it changes.
That last one is not a detail: an element page skips its redraw when the
version has not moved, so a feed that carries a correct score under a stale
version is a graphic frozen on its first frame for the rest of the broadcast.
It has already happened once, to the squad depth element.
"""
from datetime import date, time, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_event.models import Event, EventTournamentLink

from .models import (
    BracketMatch, BroadcastElement, BroadcastSession, LeagueRules, RunSheet,
    RunSheetDay, RunSheetItem, SquadMember, TieFixture, Tournament,
    TournamentRegistration, TournamentSquad)


#: The kinds this contract adds. `now_next` is here because it becomes a
#: TOURNAMENT kind today; it was already an event one.
NEW_KINDS = ['fixture_card', 'fixture_result', 'match_result', 'head_to_head',
             'break_screen', 'now_next', 'award', 'explainer']

#: What an event may put on air. Written out rather than read from the model,
#: because the point of the assertion is that adding eight tournament graphics
#: did not quietly change the other half of the studio.
EVENT_KINDS_BEFORE = ['now_next', 'programme', 'lower_third', 'sponsors',
                      'doors', 'media', 'ticker', 'intro', 'outro']

#: The columns the contract names, exactly. Set equality rather than a
#: containment check: a column the frontend draws and the feed stopped sending
#: fills with nothing and reads as a design that did not load.
NATION_COLUMNS = {'place', 'name', 'tag', 'logo', 'played', 'won', 'drawn',
                  'lost', 'goals_for', 'goals_against', 'goal_difference',
                  'points'}
PLAYER_COLUMNS = {'place', 'name', 'nation', 'seat', 'played', 'won', 'drawn',
                  'lost', 'goals_for', 'goals_against', 'goal_difference',
                  'points'}


def an_organiser(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        full_name=name.replace('_', ' ').title(),
        login_session_token=('%s_tok' % name)[:16])
    return user, {'HTTP_AUTHORIZATION': 'Bearer ' + user.login_session_token}


def a_tournament(creator, game, bracket_type, title):
    now = timezone.now()
    return Tournament.objects.create(
        tournament_title=title, tournament_game=game,
        tournament_creator=creator,
        start_date_and_time=now + timedelta(days=1),
        end_date_and_time=now + timedelta(days=3),
        tournament_visibility='public', tournament_type='online',
        prize_type='no_prize', tournament_access='team', entry_fee='Free',
        is_draft=False, bracket_type=bracket_type)


@override_settings(FRONTEND_URL='https://v-ent.co')
class RivalryFeedTests(TestCase):
    """Three nations of two seats, which is the Rivalry Series in miniature."""

    def setUp(self):
        self.organiser, self.auth = an_organiser('rivalry_overlay_org')
        self.game = Games.objects.create(game_title='EA FC 26 OVERLAY')
        self.tournament = a_tournament(self.organiser, self.game,
                                       'aggregate_2v2', 'Rivalry Overlay')
        LeagueRules.objects.create(
            tournament=self.tournament, points_win=3, points_draw=1,
            points_loss=0, players_per_team=2,
            tiebreakers=['goal_difference', 'goals_for'])
        self.ref = self.tournament.slug or self.tournament.tournament_id

        self.squads = {}
        self.regs = {}
        self.players = {}
        for nation, tag, roster in (
            ('Nigeria', 'NGA', ['tolu', 'zainab']),
            ('Ghana', 'GHA', ['kwame', 'ama']),
            ('Kenya', 'KEN', ['otieno', 'wanjiru']),
        ):
            squad = TournamentSquad.objects.create(
                tournament=self.tournament, name=nation, tag=tag,
                created_by=self.organiser)
            for seat, username in enumerate(roster, start=1):
                player = Users.objects.create(
                    username=username, email='%s@vent.test' % username,
                    full_name=username.title() + ' Player', is_active=True)
                SquadMember.objects.create(
                    squad=squad, user=player, is_captain=(seat == 1))
                self.players[username] = player
            self.squads[nation] = squad
            self.regs[nation] = TournamentRegistration.objects.create(
                tournament=self.tournament, squad=squad, status='confirmed')

    # ------------------------------------------------------------- fixtures

    def a_tie(self, home, away, round_number=1, match_number=1):
        tie = BracketMatch.objects.create(
            tournament=self.tournament, round_number=round_number,
            match_number=match_number, participant_1=self.regs[home],
            participant_2=self.regs[away], status='scheduled')
        home_players = list(self.squads[home].members.order_by(
            '-is_captain', 'added_at', 'pk'))
        away_players = list(self.squads[away].members.order_by(
            '-is_captain', 'added_at', 'pk'))
        for slot in (1, 2):
            TieFixture.objects.create(
                tie=tie, slot=slot,
                player_1=home_players[slot - 1].user,
                player_2=away_players[slot - 1].user,
                status='scheduled')
        return tie

    def play(self, tie, slot, home_goals, away_goals):
        leg = TieFixture.objects.get(tie=tie, slot=slot)
        leg.goals_1, leg.goals_2, leg.status = home_goals, away_goals, 'completed'
        leg.save()
        return leg

    # ----------------------------------------------------------- the feeds

    def feed(self, ref=None):
        res = self.client.get('/tournament/%s/overlay-feed/' % (ref or self.ref))
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    def a_session(self):
        res = self.client.post('/tournament/%s/studio/sessions/' % self.ref,
                               data={'name': 'Rivalry'}, **self.auth)
        self.assertIn(res.status_code, (200, 201), res.content[:300])
        return res.json()['data']['session']

    def studio_feed(self, session):
        res = self.client.get('/studio/%s/feed/' % session['token'])
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    # ------------------------------------------------------------ the kinds

    def test_the_eight_new_kinds_are_offered_for_a_tournament(self):
        res = self.client.get('/tournament/%s/studio/sessions/' % self.ref,
                              **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        offered = [row['kind'] for row in res.json()['data']['kinds']]
        for kind in NEW_KINDS:
            self.assertIn(kind, offered,
                          '%s is not offered to a tournament broadcast' % kind)

    def test_each_new_kind_can_actually_go_on_air(self):
        """Offered in a list is not the same as accepted by the endpoint.

        The column's choices and `kinds_for()` are two lists, and a kind in one
        and not the other is a graphic the console shows and refuses to switch
        on. Pressing it is the only way to know.
        """
        session = self.a_session()
        for kind in NEW_KINDS:
            res = self.client.post(
                '/tournament/%s/studio/sessions/%s/element/%s/'
                % (self.ref, session['id'], kind),
                data={'active': True, 'payload': {'title': 'On air'}},
                content_type='application/json', **self.auth)
            self.assertEqual(res.status_code, 200,
                             '%s could not go on air: %s' % (kind, res.content[:200]))
        data = self.studio_feed(session)
        for kind in NEW_KINDS:
            self.assertTrue(data['elements'][kind]['active'],
                            '%s went on air and the feed says otherwise' % kind)

    def test_the_event_element_list_is_unchanged(self):
        self.assertEqual([k for k, _ in BroadcastElement.EVENT_KINDS],
                         EVENT_KINDS_BEFORE,
                         'adding tournament graphics changed the event list')

    def test_a_kind_is_listed_once_in_the_column_choices(self):
        """`now_next` belongs to both halves and is one value, not two."""
        values = [k for k, _ in BroadcastElement.KINDS]
        self.assertEqual(len(values), len(set(values)),
                         'a kind is offered twice in the column choices')
        for kind in [k for k, _ in BroadcastElement.EVENT_KINDS]:
            self.assertIn(kind, values)
        for kind in [k for k, _ in BroadcastElement.TOURNAMENT_KINDS]:
            self.assertIn(kind, values)

    # --------------------------------------------------------- switched off

    def test_rivalry_is_off_for_a_single_elimination_tournament(self):
        knockout = a_tournament(self.organiser, self.game,
                                'single_elimination', 'Knockout Overlay')
        data = self.feed(knockout.slug or knockout.tournament_id)
        rivalry = data['rivalry']
        self.assertFalse(rivalry['enabled'])
        self.assertEqual(rivalry['fixtures'], [])
        self.assertEqual(rivalry['table_nations'], [])
        self.assertEqual(rivalry['table_players'], [])
        self.assertIsNone(rivalry['now'])

    def test_rivalry_is_off_for_a_round_robin_nobody_configured(self):
        """No league rules means one player a side, which is not a tie at all.

        The trap is `league.rules_for`, whose unsaved default is two players a
        side. Reading seats from there would turn every ordinary round robin on
        the platform into a rivalry with imaginary second seats.
        """
        plain = a_tournament(self.organiser, self.game, 'round_robin',
                             'Plain Round Robin')
        data = self.feed(plain.slug or plain.tournament_id)
        self.assertFalse(data['rivalry']['enabled'])

    # ---------------------------------------------------------- switched on

    def test_rivalry_is_on_for_an_aggregate_tournament(self):
        self.a_tie('Nigeria', 'Ghana')
        rivalry = self.feed()['rivalry']
        self.assertTrue(rivalry['enabled'])
        self.assertEqual(rivalry['seats'], 2)
        self.assertEqual(len(rivalry['fixtures']), 1)

    def test_a_fixture_carries_both_legs_in_seat_order_with_the_aggregate(self):
        """The worked example: 3-0 then 0-2 is 3-2, and Nigeria take it."""
        tie = self.a_tie('Nigeria', 'Ghana')
        self.play(tie, 1, 3, 0)
        self.play(tie, 2, 0, 2)

        fixture = self.feed()['rivalry']['fixtures'][0]
        self.assertEqual(fixture['id'], tie.pk)
        self.assertEqual(fixture['home']['name'], 'Nigeria')
        self.assertEqual(fixture['home']['tag'], 'NGA')
        self.assertEqual(fixture['away']['name'], 'Ghana')

        self.assertEqual([leg['seat'] for leg in fixture['legs']], [1, 2],
                         'the legs have to arrive in seat order')
        self.assertEqual(
            [(leg['home_score'], leg['away_score']) for leg in fixture['legs']],
            [(3, 0), (0, 2)])

        self.assertEqual(fixture['home']['aggregate'], 3)
        self.assertEqual(fixture['away']['aggregate'], 2,
                         'the tie is total goals, never matches won')

        # A leg names the player twice: once as it is drawn, and once as the
        # operator types it into a head to head payload.
        first = fixture['legs'][0]
        self.assertEqual(first['home_player'], 'Tolu Player')
        self.assertEqual(first['home_player_username'], 'tolu')
        self.assertEqual(first['away_player_username'], 'kwame')

    def test_a_leg_carries_every_field_the_contract_names(self):
        tie = self.a_tie('Nigeria', 'Ghana')
        self.play(tie, 1, 1, 1)
        leg = self.feed()['rivalry']['fixtures'][0]['legs'][0]
        for name in ('seat', 'home_player', 'away_player', 'home_score',
                     'away_score', 'status'):
            self.assertIn(name, leg, '%s is missing from a leg' % name)
        self.assertEqual(leg['status'], 'completed')

    def test_a_half_played_fixture_shows_the_running_aggregate_and_is_not_decided(self):
        tie = self.a_tie('Nigeria', 'Ghana')
        self.play(tie, 1, 4, 1)

        fixture = self.feed()['rivalry']['fixtures'][0]
        self.assertEqual((fixture['home']['aggregate'],
                          fixture['away']['aggregate']), (4, 1))
        self.assertFalse(fixture['decided'])
        self.assertEqual(fixture['points'], {'home': 0, 'away': 0},
                         'points are not paid until the fixture is decided')

    def test_a_decided_fixture_pays_the_organisers_points(self):
        from .services import league

        tie = self.a_tie('Nigeria', 'Ghana')
        self.play(tie, 1, 3, 0)
        self.play(tie, 2, 0, 2)
        league.settle(tie)

        fixture = self.feed()['rivalry']['fixtures'][0]
        self.assertTrue(fixture['decided'])
        self.assertEqual(fixture['status'], 'completed')
        self.assertEqual(fixture['points'], {'home': 3, 'away': 0})

    def test_a_drawn_fixture_pays_both_sides(self):
        from .services import league

        tie = self.a_tie('Nigeria', 'Ghana')
        self.play(tie, 1, 2, 1)
        self.play(tie, 2, 0, 1)
        league.settle(tie)

        fixture = self.feed()['rivalry']['fixtures'][0]
        self.assertEqual(fixture['points'], {'home': 1, 'away': 1})

    def test_now_names_the_fixture_and_the_seat_being_played(self):
        tie = self.a_tie('Nigeria', 'Ghana')
        self.play(tie, 1, 2, 0)
        leg = TieFixture.objects.get(tie=tie, slot=2)
        leg.status = 'in_progress'
        leg.save(update_fields=['status'])

        now = self.feed()['rivalry']['now']
        self.assertEqual(now, {'fixture_id': tie.pk, 'seat': 2})

    def test_now_is_null_before_anything_starts(self):
        self.a_tie('Nigeria', 'Ghana')
        self.assertIsNone(self.feed()['rivalry']['now'])

    # ------------------------------------------------------------ the tables

    def play_the_group(self):
        """Every pair meets once, so both tables have something to say."""
        from .services import league

        results = [
            (('Nigeria', 'Ghana'), (3, 0), (0, 2)),      # 3-2 Nigeria
            (('Nigeria', 'Kenya'), (1, 1), (1, 1)),      # 2-2 draw
            (('Ghana', 'Kenya'), (0, 2), (1, 3)),        # 1-5 Kenya
        ]
        for index, ((home, away), first, second) in enumerate(results, start=1):
            tie = self.a_tie(home, away, round_number=index, match_number=1)
            self.play(tie, 1, *first)
            self.play(tie, 2, *second)
            league.settle(tie)

    def test_the_nation_table_carries_the_columns_the_contract_names(self):
        self.play_the_group()
        table = self.feed()['rivalry']['table_nations']
        self.assertEqual(len(table), 3)
        for row in table:
            self.assertEqual(set(row.keys()), NATION_COLUMNS)

        self.assertEqual([row['place'] for row in table], [1, 2, 3])
        # The badge and the short form, which a table row has no way to know
        # and every leaderboard graphic draws.
        self.assertEqual({row['tag'] for row in table}, {'NGA', 'GHA', 'KEN'})

        by_name = {row['name']: row for row in table}
        self.assertEqual(by_name['Kenya']['points'], 4)   # a win and a draw
        self.assertEqual(by_name['Nigeria']['points'], 4)
        self.assertEqual(by_name['Ghana']['points'], 0)
        self.assertEqual(by_name['Kenya']['goals_for'], 7)

    def test_the_player_table_carries_the_columns_the_contract_names(self):
        self.play_the_group()
        table = self.feed()['rivalry']['table_players']
        self.assertEqual(len(table), 6, 'three nations of two seats')
        for row in table:
            self.assertEqual(set(row.keys()), PLAYER_COLUMNS)

        by_name = {row['name']: row for row in table}
        # The nation and the seat are the two things the individual table needs
        # and `league.player_table` cannot know: it counts a person's own games
        # and has never heard of a side.
        self.assertEqual(by_name['Tolu Player']['nation'], 'Nigeria')
        self.assertEqual(by_name['Tolu Player']['seat'], 1)
        self.assertEqual(by_name['Zainab Player']['seat'], 2)
        self.assertEqual(by_name['Wanjiru Player']['nation'], 'Kenya')

    def test_a_player_can_win_their_match_while_their_nation_loses_the_tie(self):
        """The one thing the two tables exist to keep apart."""
        from .services import league

        tie = self.a_tie('Nigeria', 'Ghana')
        self.play(tie, 1, 2, 0)       # Tolu wins his match
        self.play(tie, 2, 0, 5)       # Zainab loses hers heavily
        league.settle(tie)

        rivalry = self.feed()['rivalry']
        nations = {row['name']: row for row in rivalry['table_nations']}
        players = {row['name']: row for row in rivalry['table_players']}

        self.assertEqual(nations['Nigeria']['lost'], 1)
        self.assertEqual(players['Tolu Player']['won'], 1,
                         'a player keeps their own result')

    # ---------------------------------------------------- the studio forwards

    def test_the_studio_feed_carries_the_same_block(self):
        tie = self.a_tie('Nigeria', 'Ghana')
        self.play(tie, 1, 3, 0)
        session = self.a_session()
        data = self.studio_feed(session)
        self.assertTrue(data['rivalry']['enabled'])
        self.assertEqual(data['rivalry']['fixtures'][0]['home']['aggregate'], 3)
        self.assertIn('run_of_show', data)

    def test_a_retired_broadcast_still_carries_the_empty_blocks(self):
        """A cleared screen must not be a page that threw on the way there."""
        session = self.a_session()
        BroadcastSession.objects.filter(pk=session['id']).update(status='ended')
        data = self.studio_feed(session)
        self.assertFalse(data['rivalry']['enabled'])
        self.assertIsNone(data['run_of_show']['now'])

    # ----------------------------------------------------------- the version

    def test_the_version_moves_when_a_score_changes(self):
        tie = self.a_tie('Nigeria', 'Ghana')
        self.play(tie, 1, 3, 0)
        before = self.feed()['version']

        # 3-0 and 2-1 are the same total. A version built from sums alone would
        # not move, and the fixture card would keep its first frame.
        leg = TieFixture.objects.get(tie=tie, slot=1)
        leg.goals_1, leg.goals_2 = 2, 1
        leg.save()

        self.assertNotEqual(self.feed()['version'], before,
                            'a corrected scoreline left the version standing')

    def test_the_rivalry_stamp_is_the_thing_that_moves(self):
        """"The version changed" is not proof that this block is why.

        Something else in that string could be doing the work, and then the
        graphic would still freeze the day the other thing stopped changing.
        So: the stamp is IN the version, and the stamp is what moved.
        """
        from django.test import RequestFactory

        from .views_overlay_feed import rivalry_for

        tie = self.a_tie('Nigeria', 'Ghana')
        self.play(tie, 1, 3, 0)
        request = RequestFactory().get('/')

        _block, before = rivalry_for(self.tournament, request)
        self.assertTrue(before, 'an aggregate tournament has no stamp')
        self.assertIn(before, self.feed()['version'],
                      'the version does not carry the rivalry stamp at all')

        leg = TieFixture.objects.get(tie=tie, slot=1)
        leg.goals_1, leg.goals_2 = 2, 1
        leg.save()

        _block, after = rivalry_for(self.tournament, request)
        self.assertNotEqual(after, before)
        self.assertIn(after, self.feed()['version'])

    def test_the_version_moves_when_a_seat_finishes(self):
        tie = self.a_tie('Nigeria', 'Ghana')
        self.play(tie, 1, 1, 0)
        before = self.feed()['version']
        self.play(tie, 2, 0, 0)
        self.assertNotEqual(self.feed()['version'], before)

    def test_the_version_is_steady_when_nothing_changes(self):
        """A version that moves on its own redraws every graphic every poll."""
        tie = self.a_tie('Nigeria', 'Ghana')
        self.play(tie, 1, 1, 0)
        self.assertEqual(self.feed()['version'], self.feed()['version'])


@override_settings(FRONTEND_URL='https://v-ent.co')
class RivalryWithNoLeagueRulesTests(TestCase):
    """The state the live tournament was actually in, which turned it all off.

    `services.schedule.build_league` draws two seats a tie from its own default
    argument and never writes a `LeagueRules` row. So an aggregate tournament
    with twenty seated matches on the board and no league settings is not an
    edge case: it is how a Rivalry Series arrives. Deciding "is this an
    aggregate league" from that settings row answered no, and every graphic
    drew its empty state on air with nothing anywhere saying why.

    The draw is the answer. This class is the fixture of the real fault.
    """

    def setUp(self):
        from .services import schedule

        self.organiser, self.auth = an_organiser('no_rules_org')
        self.game = Games.objects.create(game_title='EA FC 26 NO RULES')
        self.tournament = a_tournament(self.organiser, self.game,
                                       'aggregate_2v2', 'Rivalry No Rules')
        self.ref = self.tournament.slug or self.tournament.tournament_id

        self.regs = {}
        for nation, tag, roster in (
            ('Nigeria', 'NGA', ['nr_tolu', 'nr_zainab']),
            ('Ghana', 'GHA', ['nr_kwame', 'nr_ama']),
            ('Kenya', 'KEN', ['nr_otieno', 'nr_wanjiru']),
            ('Egypt', 'EGY', ['nr_hassan', 'nr_nour']),
        ):
            squad = TournamentSquad.objects.create(
                tournament=self.tournament, name=nation, tag=tag,
                created_by=self.organiser)
            for seat, username in enumerate(roster, start=1):
                player = Users.objects.create(
                    username=username, email='%s@vent.test' % username,
                    full_name=username.replace('nr_', '').title(),
                    is_active=True)
                SquadMember.objects.create(squad=squad, user=player,
                                           is_captain=(seat == 1))
            self.regs[nation] = TournamentRegistration.objects.create(
                tournament=self.tournament, squad=squad, status='confirmed')

        # Exactly how the live one was built: the league scheduler, with its
        # own default of two players a side and nothing written to LeagueRules.
        schedule.build_league(self.tournament)

    def feed(self):
        res = self.client.get('/tournament/%s/overlay-feed/' % self.ref)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    def test_the_setup_really_has_no_league_rules_row(self):
        """If this ever starts failing the rest of the class proves nothing."""
        self.assertFalse(
            LeagueRules.objects.filter(tournament=self.tournament).exists(),
            'the fixture stopped reproducing the fault it exists for')
        self.assertEqual(
            TieFixture.objects.filter(tie__tournament=self.tournament).count(),
            12, 'four sides meeting once is six ties of two seats')

    def test_rivalry_is_enabled_from_the_draw_alone(self):
        from .services import bracket

        # The old guard's answer, asserted rather than described. It is 1 here,
        # so a guard reading it would switch every graphic off, and this test
        # fails the moment anybody puts it back.
        self.assertEqual(bracket._seats_for(self.tournament), 1,
                         'the settings row stopped being the wrong answer; '
                         'this test no longer proves anything')

        rivalry = self.feed()['rivalry']
        self.assertTrue(rivalry['enabled'],
                        'an aggregate draw with no settings row read as not a league')
        self.assertEqual(rivalry['seats'], 2)
        self.assertEqual(len(rivalry['fixtures']), 6)

    def test_the_fixtures_carry_their_legs_and_the_people_in_them(self):
        fixture = self.feed()['rivalry']['fixtures'][0]
        self.assertEqual([leg['seat'] for leg in fixture['legs']], [1, 2])
        # Empty seats here would mean the league scheduler drew fixtures with
        # nobody in them, which is a different fault with the same symptom.
        for leg in fixture['legs']:
            self.assertTrue(leg['home_player'],
                            'a seat was drawn with nobody in it')
            self.assertTrue(leg['away_player_username'])

    def test_both_tables_come_back_once_something_is_played(self):
        from .services import league

        tie = BracketMatch.objects.filter(
            tournament=self.tournament).order_by('id').first()
        legs = list(TieFixture.objects.filter(tie=tie).order_by('slot'))
        for leg, (home, away) in zip(legs, [(3, 2), (1, 1)]):
            leg.goals_1, leg.goals_2, leg.status = home, away, 'completed'
            leg.save()
        league.settle(tie)

        rivalry = self.feed()['rivalry']
        self.assertEqual(len(rivalry['table_nations']), 4)
        self.assertEqual(len(rivalry['table_players']), 4,
                         'two seats each of the two sides that have played')
        for row in rivalry['table_nations']:
            self.assertEqual(set(row.keys()), NATION_COLUMNS)
        for row in rivalry['table_players']:
            self.assertEqual(set(row.keys()), PLAYER_COLUMNS)
            self.assertTrue(row['nation'], 'a player with no side in the table')
            self.assertIn(row['seat'], (1, 2))

        # The points an organiser who set nothing gets: the familiar defaults,
        # from the same unsaved LeagueRules the tables themselves use.
        fixture = next(f for f in rivalry['fixtures'] if f['id'] == tie.pk)
        self.assertEqual(fixture['home']['aggregate'], 4)
        self.assertEqual(fixture['away']['aggregate'], 3)
        self.assertEqual(fixture['points'], {'home': 3, 'away': 0})

    def test_a_plain_round_robin_is_still_not_a_rivalry(self):
        """The thing reading the draw must not promote an ordinary league.

        A plain round robin's ties carry no TieFixture rows at all, so counting
        seats answers zero and the fallback answers one.
        """
        from .services import bracket

        plain = a_tournament(self.organiser, self.game, 'round_robin',
                             'Plain No Rules')
        for name in ('Alpha', 'Bravo', 'Charlie'):
            squad = TournamentSquad.objects.create(
                tournament=plain, name=name, created_by=self.organiser)
            player = Users.objects.create(
                username='pl_%s' % name.lower(),
                email='pl_%s@vent.test' % name.lower(), is_active=True)
            SquadMember.objects.create(squad=squad, user=player)
            TournamentRegistration.objects.create(
                tournament=plain, squad=squad, status='confirmed')
        bracket.generate(plain, self.organiser)

        self.assertEqual(
            TieFixture.objects.filter(tie__tournament=plain).count(), 0,
            'a plain round robin drew seats it should not have')
        res = self.client.get('/tournament/%s/overlay-feed/'
                              % (plain.slug or plain.tournament_id))
        self.assertFalse(res.json()['data']['rivalry']['enabled'])


@override_settings(FRONTEND_URL='https://v-ent.co')
class RunOfShowFeedTests(TestCase):
    """What is on and what follows, against the clock on the wall of the venue."""

    def setUp(self):
        self.organiser, self.auth = an_organiser('runshow_org')
        self.game = Games.objects.create(game_title='EA FC 26 RUNSHOW')
        self.tournament = a_tournament(self.organiser, self.game,
                                       'aggregate_2v2', 'Run Of Show Overlay')
        LeagueRules.objects.create(tournament=self.tournament,
                                   players_per_team=2)
        self.ref = self.tournament.slug or self.tournament.tournament_id

        # 13:00 UTC is 14:00 in Lagos on the same date, so the day never wraps
        # and the assertions do not depend on when the suite is run.
        self.frozen = timezone.now().replace(hour=13, minute=0, second=0,
                                             microsecond=0)
        self.today = (self.frozen + timedelta(hours=1)).date()

    def a_sheet(self, day_date, visibility=RunSheet.PUBLIC):
        sheet = RunSheet.objects.create(
            tournament=self.tournament, name='Rivalry Series day one',
            time_zone='Africa/Lagos', visibility=visibility,
            created_by=self.organiser)
        day = RunSheetDay.objects.create(sheet=sheet, label='Day 1',
                                         date=day_date, position=0)
        RunSheetItem.objects.create(
            day=day, phase='STREAM STARTS', activity='Countdown',
            owner='GFX', starts_at=time(13, 0), ends_at=time(13, 30),
            position=0)
        RunSheetItem.objects.create(
            day=day, phase='MATCHES ONGOING', activity='NGA1 v GHA1',
            owner='Casters / GFX', match='NGA1 v GHA1',
            starts_at=time(13, 45), ends_at=time(14, 15), position=1)
        RunSheetItem.objects.create(
            day=day, phase='BREAK', activity='Half time analysis',
            owner='Analyst desk', starts_at=time(14, 30), ends_at=time(15, 0),
            position=2)
        return sheet, day

    def feed(self):
        with patch('django.utils.timezone.now', return_value=self.frozen):
            res = self.client.get('/tournament/%s/overlay-feed/' % self.ref)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    def studio_feed(self):
        res = self.client.post('/tournament/%s/studio/sessions/' % self.ref,
                               data={'name': 'Run of show'}, **self.auth)
        self.assertIn(res.status_code, (200, 201), res.content[:300])
        token = res.json()['data']['session']['token']
        with patch('django.utils.timezone.now', return_value=self.frozen):
            res = self.client.get('/studio/%s/feed/' % token)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    # ------------------------------------------------------------- today

    def test_now_and_next_for_a_sheet_dated_today(self):
        self.a_sheet(self.today)
        block = self.feed()['run_of_show']

        self.assertEqual(block['day_label'], 'Day 1')
        self.assertEqual(block['time_zone'], 'Africa/Lagos')

        self.assertEqual(block['now']['activity'], 'NGA1 v GHA1')
        self.assertEqual(block['now']['starts_at'], '13:45')
        self.assertEqual(block['now']['ends_at'], '14:15')
        self.assertEqual(block['now']['owner'], 'Casters / GFX')
        self.assertEqual(block['now']['match'], 'NGA1 v GHA1')

        self.assertEqual(block['next']['activity'], 'Half time analysis')
        self.assertEqual(block['next']['starts_at'], '14:30')
        self.assertEqual(block['next']['owner'], 'Analyst desk')

    def test_the_times_are_the_venues_and_not_the_readers(self):
        """14:00 Lagos, from 13:00 UTC. A caster reading 13:45 is on air late."""
        self.a_sheet(self.today)
        block = self.feed()['run_of_show']
        self.assertEqual(block['now']['starts_at'], '13:45')

    def test_a_gap_between_cues_is_reported_as_a_gap(self):
        sheet, day = self.a_sheet(self.today)
        # Everything ends before 14:00, so nothing is on and something is next.
        day.items.filter(starts_at=time(13, 45)).update(ends_at=time(13, 50))
        block = self.feed()['run_of_show']
        self.assertIsNone(block['now'],
                          'a finished cue must not sit there as though it were live')
        self.assertEqual(block['next']['activity'], 'Half time analysis')

    # ------------------------------------------------------------ not today

    def test_now_and_next_are_null_for_a_sheet_dated_tomorrow(self):
        self.a_sheet(self.today + timedelta(days=1))
        block = self.feed()['run_of_show']
        self.assertIsNone(block['now'])
        self.assertIsNone(block['next'])
        self.assertEqual(block['day_label'], '',
                         'no day matches today, so there is no day to name')

    def test_a_tournament_with_no_run_of_show_carries_an_empty_block(self):
        block = self.feed()['run_of_show']
        self.assertEqual(block, {'day_label': '', 'time_zone': '',
                                 'now': None, 'next': None})

    # ------------------------------------------------------------ visibility

    def test_a_private_sheet_is_withheld_from_the_public_feed(self):
        self.a_sheet(self.today, visibility=RunSheet.PRIVATE)
        block = self.feed()['run_of_show']
        self.assertIsNone(block['now'],
                          'a private run of show reached a public address')
        self.assertEqual(block['day_label'], '')

    def test_a_private_sheet_still_feeds_the_studio(self):
        self.a_sheet(self.today, visibility=RunSheet.PRIVATE)
        block = self.studio_feed()['run_of_show']
        self.assertEqual(block['now']['activity'], 'NGA1 v GHA1')
        self.assertEqual(block['next']['activity'], 'Half time analysis')

    def test_a_link_sheet_is_private_to_the_public_feed_too(self):
        """"Anybody with the address" is not "anybody who found the tournament"."""
        self.a_sheet(self.today, visibility=RunSheet.LINK)
        self.assertIsNone(self.feed()['run_of_show']['now'])
        self.assertIsNotNone(self.studio_feed()['run_of_show']['now'])

    # --------------------------------------------------- the event's own sheet

    def test_a_tournament_inside_an_event_reads_the_events_run_of_show(self):
        """One sheet for the day, not one per thing running inside it."""
        now = timezone.now()
        event = Event.objects.create(
            name='Rivalry Live', game=self.game, creator=self.organiser,
            event_type='physical', desc='A day of it', entry_fee=0,
            reg_start_date=now, reg_end_date=now + timedelta(days=1),
            event_date=self.today, start_time=time(10, 0),
            end_time=time(20, 0), is_active=True)
        EventTournamentLink.objects.create(event=event,
                                           tournament=self.tournament,
                                           linked_by=self.organiser)
        sheet = RunSheet.objects.create(
            event=event, name='Rivalry Live day one', time_zone='Africa/Lagos',
            visibility=RunSheet.PUBLIC, created_by=self.organiser)
        day = RunSheetDay.objects.create(sheet=sheet, label='Event day',
                                         date=self.today, position=0)
        RunSheetItem.objects.create(
            day=day, activity='Doors and warm up', owner='Front of house',
            starts_at=time(13, 30), ends_at=time(14, 30), position=0)

        block = self.feed()['run_of_show']
        self.assertEqual(block['day_label'], 'Event day')
        self.assertEqual(block['now']['activity'], 'Doors and warm up')

    def test_the_tournaments_own_sheet_wins_over_the_events(self):
        now = timezone.now()
        event = Event.objects.create(
            name='Rivalry Live Two', game=self.game, creator=self.organiser,
            event_type='physical', desc='A day of it', entry_fee=0,
            reg_start_date=now, reg_end_date=now + timedelta(days=1),
            event_date=self.today, start_time=time(10, 0),
            end_time=time(20, 0), is_active=True)
        EventTournamentLink.objects.create(event=event,
                                           tournament=self.tournament,
                                           linked_by=self.organiser)
        event_sheet = RunSheet.objects.create(
            event=event, time_zone='Africa/Lagos', visibility=RunSheet.PUBLIC)
        RunSheetDay.objects.create(sheet=event_sheet, label='Event day',
                                   date=self.today, position=0)
        self.a_sheet(self.today)

        self.assertEqual(self.feed()['run_of_show']['day_label'], 'Day 1')

    # ----------------------------------------------------------- the version

    def test_the_version_moves_when_the_run_sheet_changes(self):
        sheet, day = self.a_sheet(self.today)
        before = self.feed()['version']

        RunSheetItem.objects.filter(day=day, activity='Half time analysis').update(
            activity='Award: player of the day')

        self.assertNotEqual(self.feed()['version'], before,
                            'a corrected cue left the version standing')

    def test_the_run_of_show_stamp_is_the_thing_that_moves(self):
        """As above: the stamp is in the version, and the stamp is what moved."""
        from .views_overlay_feed import run_of_show_for

        sheet, day = self.a_sheet(self.today)
        with patch('django.utils.timezone.now', return_value=self.frozen):
            _block, before = run_of_show_for(self.tournament)
        self.assertTrue(before, 'a sheet dated today has no stamp')
        self.assertIn(before, self.feed()['version'])

        RunSheetItem.objects.filter(day=day).update(owner='Producer')

        with patch('django.utils.timezone.now', return_value=self.frozen):
            _block, after = run_of_show_for(self.tournament)
        self.assertNotEqual(after, before)
        self.assertIn(after, self.feed()['version'])

    def test_the_version_moves_when_a_cue_is_added(self):
        sheet, day = self.a_sheet(self.today)
        before = self.feed()['version']
        RunSheetItem.objects.create(
            day=day, activity='Day close', owner='GFX',
            starts_at=time(19, 0), ends_at=time(19, 15), position=3)
        self.assertNotEqual(self.feed()['version'], before)

    def test_the_studio_version_moves_when_a_private_sheet_changes(self):
        """The whole point of the studio's own stamp.

        The public feed cannot see a private sheet, so its version cannot move
        with it. Without a stamp of its own the studio would carry the correct
        cue under a version that never changed, and the now and next graphic
        would hold its first frame for the whole broadcast.
        """
        sheet, day = self.a_sheet(self.today, visibility=RunSheet.PRIVATE)
        before = self.studio_feed()['version']
        RunSheetItem.objects.filter(day=day).update(owner='Producer')
        self.assertNotEqual(self.studio_feed()['version'], before)

    def test_the_version_moves_when_the_clock_reaches_the_next_cue(self):
        """Nothing in the database moves at 14:30. The graphic still must."""
        self.a_sheet(self.today)
        before = self.feed()['version']

        self.frozen = self.frozen.replace(hour=13, minute=45)   # 14:45 in Lagos
        after = self.feed()

        self.assertNotEqual(after['version'], before)
        self.assertEqual(after['run_of_show']['now']['activity'],
                         'Half time analysis')
