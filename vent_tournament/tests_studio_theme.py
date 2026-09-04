# -*- coding: utf-8 -*-
"""Which look a broadcast is drawn in, and the four graphics added with it.

CEO, 4 September 2026: "continue with the overlays. but please note that the
design youi were doing did nnot match the original design."

The original is a finished broadcast pack for the CADE Rivalry Series, approved
before the event, with its own typefaces and its own artwork behind two of the
cards. The studio had drawn its own instead. Rather than repaint every
organiser's graphics in one client's brand, a broadcast now chooses a LOOK, and
the default stays what V-ENT ships.

A look, not a fork, and that is what most of this file asserts: the same
components read the same feed and only the drawing changes, so a correction to
what a card SAYS reaches both looks and they cannot drift apart.

The version assertion is the one that matters on air. An element page skips its
redraw when the version has not moved, so a broadcast switched to the other
look would keep drawing the old one until something else happened to change.
That has already happened once on this platform, to squad depth.
"""
from datetime import date, timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_event.models import Event

from .models import (BracketMatch, BroadcastElement, BroadcastSession,
                     LeagueRules, SquadMember, TieFixture, Tournament,
                     TournamentRegistration, TournamentSquad)

#: The four graphics the CEO named on 4 September that the studio had no kind
#: for. Two are cards and two are frames.
ADDED_KINDS = ['desk_lower_third', 'matchday', 'analyst_desk', 'play_area']

#: Of those, the three an EVENT may also put on air. A desk and a stage belong
#: to whoever is broadcasting. `matchday` draws a day of aggregate fixtures,
#: which only a tournament has.
ADDED_EVENT_KINDS = ['desk_lower_third', 'analyst_desk', 'play_area']


def an_organiser(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        full_name=name.replace('_', ' ').title(),
        login_session_token=('%s_tok' % name)[:16])
    return user, {'HTTP_AUTHORIZATION': 'Bearer ' + user.login_session_token}


@override_settings(FRONTEND_URL='https://v-ent.co')
class BroadcastLookTests(TestCase):

    def setUp(self):
        self.organiser, self.auth = an_organiser('studio_theme_org')
        self.game = Games.objects.create(game_title='EA FC 26 THEME')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Look Test', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=3),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team', entry_fee='Free',
            is_draft=False, bracket_type='aggregate_2v2')
        self.ref = self.tournament.slug or self.tournament.tournament_id

    # ------------------------------------------------------------- the look

    def a_session(self):
        res = self.client.post('/tournament/%s/studio/sessions/' % self.ref,
                               data={'name': 'Look'}, **self.auth)
        self.assertIn(res.status_code, (200, 201), res.content[:300])
        return res.json()['data']['session']

    def set_look(self, session, value):
        return self.client.post(
            '/tournament/%s/studio/sessions/%s/' % (self.ref, session['id']),
            data={'theme': value}, content_type='application/json', **self.auth)

    def test_a_new_broadcast_is_drawn_in_the_v_ent_look(self):
        """The default is what V-ENT ships, for an organiser with no design."""
        session = self.a_session()
        self.assertEqual(session['theme'], 'vent')

    def test_the_console_is_told_which_looks_exist(self):
        """A list, so a look added to the model appears without a second change."""
        session = self.a_session()
        values = [row['value'] for row in session['themes']]
        self.assertIn('vent', values)
        self.assertIn('rivalry', values)
        self.assertEqual(len(values), len(set(values)))

    def test_an_operator_can_switch_the_look(self):
        session = self.a_session()
        res = self.set_look(session, 'rivalry')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['session']['theme'], 'rivalry')
        self.assertEqual(
            BroadcastSession.objects.get(pk=session['id']).theme, 'rivalry')

    def test_a_look_that_does_not_exist_is_refused_and_not_ignored(self):
        """An operator who set a look and saw nothing change would set it again."""
        session = self.a_session()
        res = self.set_look(session, 'neon')
        self.assertEqual(res.status_code, 400, res.content[:300])
        body = res.json()
        self.assertEqual(body['code'], 'INVALID_THEME')
        self.assertEqual(body['field'], 'theme')
        self.assertEqual(
            BroadcastSession.objects.get(pk=session['id']).theme, 'vent')

    def test_the_element_page_is_told_which_look_to_draw(self):
        session = self.a_session()
        self.set_look(session, 'rivalry')
        res = self.client.get('/studio/%s/feed/' % session['token'])
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['session']['theme'], 'rivalry')

    def test_switching_the_look_moves_the_version(self):
        """Or every source on air keeps drawing the old one until something else moves."""
        session = self.a_session()
        before = self.client.get(
            '/studio/%s/feed/' % session['token']).json()['data']['version']
        self.set_look(session, 'rivalry')
        after = self.client.get(
            '/studio/%s/feed/' % session['token']).json()['data']['version']
        self.assertNotEqual(before, after)

    # ---------------------------------------------------------- the graphics

    def test_the_four_added_graphics_are_offered_to_a_tournament(self):
        res = self.client.get('/tournament/%s/studio/sessions/' % self.ref,
                              **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        offered = [row['kind'] for row in res.json()['data']['kinds']]
        for kind in ADDED_KINDS:
            self.assertIn(kind, offered, '%s is not offered' % kind)

    def test_each_added_graphic_can_actually_go_on_air(self):
        """Offered in a list is not the same as accepted by the endpoint.

        The column's choices and `kinds_for()` are two lists, and a kind in one
        and not the other is a graphic the console shows and refuses to switch
        on. Pressing it is the only way to know.
        """
        session = self.a_session()
        for kind in ADDED_KINDS:
            res = self.client.post(
                '/tournament/%s/studio/sessions/%s/element/%s/'
                % (self.ref, session['id'], kind),
                data={'active': True, 'payload': {'title': 'On air'}},
                content_type='application/json', **self.auth)
            self.assertEqual(res.status_code, 200,
                             '%s could not go on air: %s' % (kind, res.content[:200]))
        data = self.client.get(
            '/studio/%s/feed/' % session['token']).json()['data']
        for kind in ADDED_KINDS:
            self.assertTrue(data['elements'][kind]['active'],
                            '%s went on air and the feed says otherwise' % kind)

    def test_the_desk_and_the_frames_reach_an_event_too(self):
        """Built for one side and forgotten on the other is the fault to avoid."""
        offered = [k for k, _ in BroadcastElement.EVENT_KINDS]
        for kind in ADDED_EVENT_KINDS:
            self.assertIn(kind, offered, '%s is missing from the event side' % kind)
        self.assertNotIn('matchday', offered,
                         'matchday draws aggregate fixtures, which an event has none of')

    def test_every_kind_is_listed_once_in_the_column_choices(self):
        values = [k for k, _ in BroadcastElement.KINDS]
        self.assertEqual(len(values), len(set(values)))
        for kind in ADDED_KINDS:
            self.assertIn(kind, values)


@override_settings(FRONTEND_URL='https://v-ent.co')
class MatchdayFeedTests(TestCase):
    """What the matchday card needs: which day a fixture is on, in order."""

    def setUp(self):
        self.organiser, self.auth = an_organiser('matchday_org')
        self.game = Games.objects.create(game_title='EA FC 26 MATCHDAY')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Matchday Feed', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=3),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team', entry_fee='Free',
            is_draft=False, bracket_type='aggregate_2v2')
        LeagueRules.objects.create(
            tournament=self.tournament, points_win=3, points_draw=1,
            points_loss=0, players_per_team=2,
            tiebreakers=['goal_difference', 'goals_for'])
        self.ref = self.tournament.slug or self.tournament.tournament_id

        self.regs = {}
        self.squads = {}
        for nation, tag, roster in (
            ('Nigeria', 'NGA', ['md_tolu', 'md_zainab']),
            ('Ghana', 'GHA', ['md_kwame', 'md_ama']),
            ('Kenya', 'KEN', ['md_otieno', 'md_wanjiru']),
        ):
            squad = TournamentSquad.objects.create(
                tournament=self.tournament, name=nation, tag=tag,
                created_by=self.organiser)
            for seat, username in enumerate(roster, start=1):
                player = Users.objects.create(
                    username=username, email='%s@vent.test' % username,
                    full_name=username.title(), is_active=True)
                SquadMember.objects.create(squad=squad, user=player,
                                           is_captain=(seat == 1))
            self.squads[nation] = squad
            self.regs[nation] = TournamentRegistration.objects.create(
                tournament=self.tournament, squad=squad, status='confirmed')

    def a_tie(self, home, away, number, day=None, order=0):
        tie = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=number,
            participant_1=self.regs[home], participant_2=self.regs[away],
            status='scheduled', day=day, running_order=order)
        home_players = list(self.squads[home].members.order_by(
            '-is_captain', 'added_at', 'pk'))
        away_players = list(self.squads[away].members.order_by(
            '-is_captain', 'added_at', 'pk'))
        for slot in (1, 2):
            TieFixture.objects.create(
                tie=tie, slot=slot, player_1=home_players[slot - 1].user,
                player_2=away_players[slot - 1].user, status='scheduled')
        return tie

    def feed(self):
        res = self.client.get('/tournament/%s/overlay-feed/' % self.ref)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']['rivalry']

    def test_a_fixture_carries_the_day_it_is_on(self):
        friday = date(2026, 9, 4)
        self.a_tie('Nigeria', 'Ghana', 1, day=friday, order=1)
        fixture = self.feed()['fixtures'][0]
        self.assertEqual(fixture['day'], '2026-09-04')
        self.assertEqual(fixture['running_order'], 1)

    def test_an_unscheduled_fixture_says_so_rather_than_guessing(self):
        """A Saturday draw shown on Friday is worse on air than no card."""
        self.a_tie('Nigeria', 'Ghana', 1)
        fixture = self.feed()['fixtures'][0]
        self.assertEqual(fixture['day'], '')

    def test_the_days_are_numbered_the_way_the_venue_numbers_them(self):
        self.a_tie('Nigeria', 'Ghana', 1, day=date(2026, 9, 5), order=1)
        self.a_tie('Ghana', 'Kenya', 2, day=date(2026, 9, 4), order=1)
        days = self.feed()['days']
        self.assertEqual([d['date'] for d in days],
                         ['2026-09-04', '2026-09-05'])
        self.assertEqual([d['number'] for d in days], [1, 2])
        self.assertEqual(len(days[0]['fixtures']), 1)

    def test_a_tournament_that_is_not_an_aggregate_league_still_has_the_key(self):
        """A page reading days must get an empty list, never undefined."""
        other = Tournament.objects.create(
            tournament_title='Plain Knockout', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team', entry_fee='Free',
            is_draft=False, bracket_type='single_elimination')
        res = self.client.get(
            '/tournament/%s/overlay-feed/'
            % (other.slug or other.tournament_id))
        block = res.json()['data']['rivalry']
        self.assertFalse(block['enabled'])
        self.assertEqual(block['days'], [])

    def test_moving_a_fixture_to_another_day_moves_the_version(self):
        """No score and no status changes, so without this the card freezes."""
        tie = self.a_tie('Nigeria', 'Ghana', 1, day=date(2026, 9, 4), order=1)
        res = self.client.get('/tournament/%s/overlay-feed/' % self.ref)
        before = res.json()['data']['version']
        tie.day = date(2026, 9, 5)
        tie.save(update_fields=['day'])
        res = self.client.get('/tournament/%s/overlay-feed/' % self.ref)
        self.assertNotEqual(before, res.json()['data']['version'])
