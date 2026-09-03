# -*- coding: utf-8 -*-
"""Squads, entrants added directly, and invitations sent to an email address.

CEO, 3 September 2026: "each player for team nigeria in the rivalry series is
registered to a different team, but both nigerian players will be working
together as a team for nigeria".

The tests below are written around that exact case, because it is the one the
feature exists for and the one that will be run first.
"""

from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, TeamMembers, Teams, Users
from vent_tournament.models import (
    Tournament, TournamentInvitation, TournamentRegistration, SquadMember,
    TournamentSquad)


def a_user(name, email=None):
    user = Users.objects.create(
        username=name, email=email or ('%s@vent.test' % name), is_active=True,
        login_session_token=(name + 'x' * 16)[:16])
    return user, {'HTTP_AUTHORIZATION': 'Bearer ' + user.login_session_token}


class SquadTests(TestCase):
    """Team Nigeria: two players, two different clubs, one side."""

    def setUp(self):
        self.organiser, self.auth = a_user('sq_org')
        self.stranger, self.stranger_auth = a_user('sq_stranger')
        # Two Nigerian players who play for two different clubs.
        self.tolu, _ = a_user('tolu')
        self.zainab, _ = a_user('zainab')

        self.game = Games.objects.create(game_title='EA FC SQUAD')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Rivalry Series Test', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False, bracket_type='round_robin')

        self.lagos = Teams.objects.create(
            team_name='Lagos Lions', game=self.game, team_creator=self.tolu,
            team_owner=self.tolu, description='', penalty_points=0,
            number_of_members=1)
        self.abuja = Teams.objects.create(
            team_name='Abuja Aces', game=self.game, team_creator=self.zainab,
            team_owner=self.zainab, description='', penalty_points=0,
            number_of_members=1)
        TeamMembers.objects.create(team=self.lagos, user=self.tolu)
        TeamMembers.objects.create(team=self.abuja, user=self.zainab)

        self.ref = self.tournament.slug or self.tournament.tournament_id
        self.base = '/tournament/%s/squads/' % self.ref

    def make_nigeria(self):
        res = self.client.post(self.base, data={'name': 'Nigeria', 'tag': 'NGA'},
                               **self.auth)
        self.assertEqual(res.status_code, 201, res.content[:300])
        return res.json()['data']['squad']['id']

    def add(self, squad_id, username, **extra):
        return self.client.post(
            '/tournament/%s/squads/%s/members/' % (self.ref, squad_id),
            data=dict({'username': username}, **extra), **self.auth)

    # ------------------------------------------------------------- the case

    def test_two_players_from_two_clubs_make_one_side(self):
        squad = self.make_nigeria()
        self.assertEqual(self.add(squad, 'tolu').status_code, 200)
        res = self.add(squad, 'zainab')
        self.assertEqual(res.status_code, 200, res.content[:300])

        members = res.json()['data']['squad']['members']
        self.assertEqual(sorted(m['username'] for m in members),
                         ['tolu', 'zainab'])
        # The whole point: they are Nigeria here AND they still play for their
        # own clubs.
        represents = {m['username']: m['represents'] for m in members}
        self.assertEqual(represents['tolu'], 'Lagos Lions')
        self.assertEqual(represents['zainab'], 'Abuja Aces')

    def test_who_they_represent_is_a_snapshot(self):
        """A transfer next month must not rewrite September."""
        squad = self.make_nigeria()
        self.add(squad, 'tolu')

        self.lagos.team_name = 'Renamed Club'
        self.lagos.save()

        member = SquadMember.objects.get(squad_id=squad, user=self.tolu)
        self.assertEqual(member.represents_name, 'Lagos Lions')

    def test_a_squad_enters_the_tournament_like_a_club(self):
        squad = self.make_nigeria()
        self.add(squad, 'tolu')
        res = self.client.post(
            '/tournament/%s/squads/%s/enter/' % (self.ref, squad), **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])

        registration = TournamentRegistration.objects.get(squad_id=squad)
        self.assertEqual(registration.status, 'confirmed')
        self.assertEqual(registration.entrant_name, 'Nigeria')

    def test_an_empty_squad_cannot_enter(self):
        squad = self.make_nigeria()
        res = self.client.post(
            '/tournament/%s/squads/%s/enter/' % (self.ref, squad), **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'EMPTY_SQUAD')

    def test_a_player_cannot_be_two_sides_at_once(self):
        """Otherwise the bracket has somebody playing themselves."""
        first = self.make_nigeria()
        self.add(first, 'tolu')
        second = self.client.post(self.base, data={'name': 'Ghana'},
                                  **self.auth).json()['data']['squad']['id']
        res = self.add(second, 'tolu')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'ALREADY_IN_A_SQUAD')
        self.assertIn('Nigeria', res.json()['message'])

    def test_adding_the_same_player_twice_is_not_an_error(self):
        squad = self.make_nigeria()
        self.add(squad, 'tolu')
        res = self.add(squad, 'tolu')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['data']['already'])
        self.assertEqual(SquadMember.objects.filter(squad_id=squad).count(), 1)

    def test_somebody_already_entered_alone_is_flagged_and_can_be_added_anyway(self):
        TournamentRegistration.objects.create(
            tournament=self.tournament, user=self.tolu, status='confirmed')
        squad = self.make_nigeria()

        res = self.add(squad, 'tolu')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'ALREADY_ENTERED_ALONE')

        res = self.add(squad, 'tolu', anyway='1')
        self.assertEqual(res.status_code, 200, res.content[:300])

    def test_a_player_can_be_removed(self):
        squad = self.make_nigeria()
        self.add(squad, 'tolu')
        res = self.client.delete(
            '/tournament/%s/squads/%s/members/tolu/' % (self.ref, squad),
            **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['squad']['members'], [])

    def test_two_squads_cannot_share_a_name(self):
        self.make_nigeria()
        res = self.client.post(self.base, data={'name': 'nigeria'}, **self.auth)
        self.assertEqual(res.status_code, 409)

    def test_a_stranger_can_do_none_of_it(self):
        squad = self.make_nigeria()
        for method, url, data in [
            ('post', self.base, {'name': 'Theirs'}),
            ('post', '/tournament/%s/squads/%s/members/' % (self.ref, squad),
             {'username': 'tolu'}),
            ('post', '/tournament/%s/squads/%s/enter/' % (self.ref, squad), {}),
            ('delete', '/tournament/%s/squads/%s/' % (self.ref, squad), {}),
        ]:
            res = getattr(self.client, method)(url, data=data,
                                               **self.stranger_auth)
            self.assertEqual(res.status_code, 403, '%s %s' % (method, url))

    def test_signed_out_can_do_none_of_it(self):
        res = self.client.post(self.base, data={'name': 'Theirs'})
        self.assertEqual(res.status_code, 401)

    # ------------------------------------------------------------- the feed

    def test_the_feed_carries_the_squad_as_a_side_and_the_clubs_as_badges(self):
        squad = self.make_nigeria()
        self.add(squad, 'tolu')
        self.add(squad, 'zainab')
        self.client.post('/tournament/%s/squads/%s/enter/' % (self.ref, squad),
                         **self.auth)

        res = self.client.get('/tournament/%s/overlay-feed/' % self.ref)
        self.assertEqual(res.status_code, 200)
        teams = res.json()['data']['teams']
        self.assertEqual([t['name'] for t in teams], ['Nigeria'])
        self.assertEqual(teams[0]['tag'], 'NGA')

        players = {p['ign']: p for p in teams[0]['players']}
        self.assertEqual(players['tolu']['represents'], 'Lagos Lions')
        self.assertEqual(players['zainab']['represents'], 'Abuja Aces')


class DirectEntrantTests(TestCase):
    """An organiser filling a bracket from a list they already have."""

    def setUp(self):
        self.organiser, self.auth = a_user('de_org')
        self.stranger, self.stranger_auth = a_user('de_stranger')
        self.player, _ = a_user('de_player')
        game = Games.objects.create(game_title='EA FC DIRECT')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Direct Cup', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False, bracket_type='round_robin')
        self.team = Teams.objects.create(
            team_name='Straight In FC', game=game, team_creator=self.player,
            team_owner=self.player, description='', penalty_points=0,
            number_of_members=1)
        self.ref = self.tournament.slug or self.tournament.tournament_id
        self.url = '/tournament/%s/entrants/' % self.ref

    def test_a_team_goes_straight_in_confirmed(self):
        res = self.client.post(self.url, data={'team': 'Straight In FC'},
                               **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertTrue(res.json()['data']['added'])
        registration = TournamentRegistration.objects.get(
            tournament=self.tournament, team=self.team)
        self.assertEqual(registration.status, 'confirmed')
        # Nobody was asked to pay, so nobody was charged.
        self.assertFalse(registration.entry_fee_paid)

    def test_a_player_goes_straight_in(self):
        res = self.client.post(self.url, data={'username': 'de_player'},
                               **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertTrue(TournamentRegistration.objects.filter(
            tournament=self.tournament, user=self.player).exists())

    def test_adding_twice_says_so_rather_than_erroring(self):
        self.client.post(self.url, data={'team': 'Straight In FC'}, **self.auth)
        res = self.client.post(self.url, data={'team': 'Straight In FC'},
                               **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['data']['added'])
        self.assertIn('already', res.json()['message'])

    def test_naming_both_or_neither_is_refused(self):
        for data in ({}, {'team': 'Straight In FC', 'username': 'de_player'}):
            res = self.client.post(self.url, data=data, **self.auth)
            self.assertEqual(res.status_code, 400)

    def test_a_stranger_cannot_add_anybody(self):
        res = self.client.post(self.url, data={'team': 'Straight In FC'},
                               **self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_signed_out_cannot_add_anybody(self):
        res = self.client.post(self.url, data={'team': 'Straight In FC'})
        self.assertEqual(res.status_code, 401)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   FRONTEND_URL='https://v-ent.co')
class InviteByEmailTests(TestCase):
    """CEO: "lets be able to invite through email also"."""

    def setUp(self):
        self.organiser, self.auth = a_user('em_org')
        game = Games.objects.create(game_title='EA FC EMAIL')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Email Cup', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False, bracket_type='round_robin')
        self.ref = self.tournament.slug or self.tournament.tournament_id
        self.url = '/tournament/%s/invitations/' % self.ref

    def test_somebody_with_no_account_is_invited_by_email(self):
        mail.outbox = []
        res = self.client.post(self.url, data={'email': 'newcomer@example.com'},
                               **self.auth)
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertEqual(res.json()['data']['invitation']['email'],
                         'newcomer@example.com')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Email Cup', mail.outbox[0].subject)
        self.assertIn('/tournaments/', mail.outbox[0].body
                      + ''.join(str(a) for a, _ in
                                getattr(mail.outbox[0], 'alternatives', [])))

    def test_an_address_that_already_has_an_account_becomes_an_invitation_to_them(self):
        somebody, _ = a_user('em_known', email='Known@Example.com')
        res = self.client.post(self.url, data={'email': 'known@example.com'},
                               **self.auth)
        self.assertEqual(res.status_code, 201, res.content[:300])
        invitation = TournamentInvitation.objects.get(tournament=self.tournament)
        self.assertEqual(invitation.user_id, somebody.user_id)
        self.assertEqual(invitation.email, '')

    def test_the_same_address_twice_is_a_reminder(self):
        self.client.post(self.url, data={'email': 'twice@example.com'}, **self.auth)
        res = self.client.post(self.url, data={'email': 'twice@example.com'},
                               **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['data']['reminded'])
        self.assertEqual(TournamentInvitation.objects.filter(
            tournament=self.tournament).count(), 1)

    def test_a_bad_address_is_refused_by_name(self):
        res = self.client.post(self.url, data={'email': 'not an address'},
                               **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['data'].get('field'), 'email')

    def test_naming_two_of_the_three_is_refused(self):
        res = self.client.post(
            self.url, data={'email': 'a@example.com', 'username': 'em_org'},
            **self.auth)
        self.assertEqual(res.status_code, 400)

    def test_the_invitation_binds_when_that_person_arrives(self):
        from vent_tournament.invitation_binding import bind_invitations_for

        self.client.post(self.url, data={'email': 'later@example.com'}, **self.auth)
        arrival, _ = a_user('em_later', email='later@example.com')

        self.assertEqual(bind_invitations_for(arrival), 1)
        invitation = TournamentInvitation.objects.get(tournament=self.tournament)
        self.assertEqual(invitation.user_id, arrival.user_id)
        self.assertEqual(invitation.email, '')

    def test_binding_twice_does_nothing_the_second_time(self):
        from vent_tournament.invitation_binding import bind_invitations_for

        self.client.post(self.url, data={'email': 'once@example.com'}, **self.auth)
        arrival, _ = a_user('em_once', email='once@example.com')
        self.assertEqual(bind_invitations_for(arrival), 1)
        self.assertEqual(bind_invitations_for(arrival), 0)

    def test_an_email_row_gives_way_to_one_addressed_by_name(self):
        """Otherwise binding would break one_invitation_per_player."""
        from vent_tournament.invitation_binding import bind_invitations_for

        both, _ = a_user('em_both', email='both@example.com')
        self.client.post(self.url, data={'username': 'em_both'}, **self.auth)
        TournamentInvitation.objects.create(
            tournament=self.tournament, email='both@example.com')

        bind_invitations_for(both)
        rows = TournamentInvitation.objects.filter(tournament=self.tournament)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().user_id, both.user_id)
