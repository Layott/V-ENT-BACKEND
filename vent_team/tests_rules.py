"""The two team rules from the PRD, and the settings that never saved.

One team per game, and a warning before somebody registers for two tournaments
running at the same time. Both are about the same thing: a player who cannot
actually be in both places, discovered before the fixture rather than during it.
"""
import json

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, TeamMembers, Teams, Users
from vent_tournament.models import Tournament, TournamentRegistration


def signed_in(username):
    user = Users.objects.create(
        username=username, email=f'{username}@vent.test',
        login_session_token=f'tok{username}'[:16],
        login_session_created_at=timezone.now(), is_active=True,
    )
    return user, {'HTTP_AUTHORIZATION': f'Bearer {user.login_session_token}'}


class OneTeamPerGameTests(TestCase):
    def setUp(self):
        self.free_fire = Games.objects.create(game_title='Free Fire')
        self.codm = Games.objects.create(game_title='Call of Duty: Mobile')
        self.owner, self.owner_auth = signed_in('teamowner')
        self.player, self.player_auth = signed_in('player')

        self.rangers = self._team('Lagos Rangers', self.free_fire)
        self.falcons = self._team('Kano Falcons', self.free_fire)
        self.codm_team = self._team('Abuja Snipers', self.codm)

        # The player is already in one Free Fire team.
        TeamMembers.objects.create(team=self.rangers, user=self.player)

    def _team(self, name, game):
        return Teams.objects.create(
            team_name=name, game=game, description='', team_creator=self.owner,
            team_owner=self.owner, penalty_points=0, number_of_members=1,
            allow_membership_requests=True,
        )

    def _request_join(self, team, auth):
        return self.client.post(
            f'/team/request-join/{team.team_id}/', data=json.dumps({}),
            content_type='application/json', **auth,
        )

    def test_a_second_team_for_the_same_game_is_refused(self):
        res = self._request_join(self.falcons, self.player_auth)
        self.assertEqual(res.status_code, 409)
        body = res.json()
        self.assertEqual(body['code'], 'ALREADY_IN_A_TEAM_FOR_THIS_GAME')
        self.assertIn('Lagos Rangers', body['message'])

    def test_a_team_for_a_different_game_is_fine(self):
        res = self._request_join(self.codm_team, self.player_auth)
        self.assertIn(res.status_code, (200, 201), res.content[:200])

    def test_the_rule_is_checked_again_when_the_request_is_accepted(self):
        # Ask to join a CODM team, which is allowed at the time of asking.
        asked = self._request_join(self.codm_team, self.player_auth)
        self.assertIn(asked.status_code, (200, 201))
        request_id = asked.json()['data']['request_id']

        # Then join another CODM team while the request sits in the queue.
        TeamMembers.objects.create(team=self._team('Jos Owls', self.codm), user=self.player)

        accepted = self.client.post(
            f'/team/accept-request/{request_id}/', data=json.dumps({}),
            content_type='application/json', **self.owner_auth,
        )
        self.assertEqual(accepted.status_code, 409)
        self.assertEqual(accepted.json()['code'], 'ALREADY_IN_A_TEAM_FOR_THIS_GAME')


class MembershipSettingsTests(TestCase):
    def setUp(self):
        self.game = Games.objects.create(game_title='Free Fire')
        self.owner, self.auth = signed_in('settingsowner')
        self.team = Teams.objects.create(
            team_name='V-ENT Esport', game=self.game, description='', team_creator=self.owner,
            team_owner=self.owner, penalty_points=0, number_of_members=1,
            allow_membership_requests=True,
        )
        TeamMembers.objects.create(team=self.team, user=self.owner)

    def edit(self, body):
        return self.client.patch(
            f'/team/edit-team/{self.team.team_id}/', data=json.dumps(body),
            content_type='application/json', **self.auth,
        )

    def test_max_members_saves(self):
        res = self.edit({'max_members': 6})
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.team.refresh_from_db()
        self.assertEqual(self.team.max_members, 6)

    def test_open_to_join_saves(self):
        self.edit({'open_to_join': False})
        self.team.refresh_from_db()
        self.assertFalse(self.team.allow_membership_requests)

    def test_a_password_saves_hashed_and_turns_protection_on(self):
        self.edit({'team_password': 'letmein'})
        self.team.refresh_from_db()
        self.assertTrue(self.team.password_protected)
        self.assertNotEqual(self.team.join_password, 'letmein')
        self.assertTrue(self.team.join_password.startswith('pbkdf2_'))

    def test_turning_protection_off_clears_the_password(self):
        self.edit({'team_password': 'letmein'})
        self.edit({'password_protected': False})
        self.team.refresh_from_db()
        self.assertFalse(self.team.password_protected)
        self.assertEqual(self.team.join_password, '')

    def test_a_cap_below_the_current_roster_is_refused(self):
        for i in range(3):
            member, _ = signed_in(f'member{i}')
            TeamMembers.objects.create(team=self.team, user=member)
        res = self.edit({'max_members': 2})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'MAX_BELOW_ROSTER')


class ScheduleClashTests(TestCase):
    def setUp(self):
        self.game = Games.objects.create(game_title='Free Fire')
        self.organiser, _ = signed_in('clashorganiser')
        self.player, self.auth = signed_in('clashplayer')

        start = timezone.now() + timezone.timedelta(days=3)
        self.first = self._tournament('Saturday Cup', start, start + timezone.timedelta(hours=4))
        self.overlapping = self._tournament(
            'Saturday Clash', start + timezone.timedelta(hours=1),
            start + timezone.timedelta(hours=5),
        )
        self.later = self._tournament(
            'Sunday Cup', start + timezone.timedelta(days=1),
            start + timezone.timedelta(days=1, hours=4),
        )
        TournamentRegistration.objects.create(
            tournament=self.first, user=self.player, status='confirmed',
        )

    def _tournament(self, title, starts, ends):
        return Tournament.objects.create(
            tournament_title=title, tournament_game=self.game, tournament_creator=self.organiser,
            start_date_and_time=starts, end_date_and_time=ends, is_draft=False,
            tournament_visibility='public', tournament_access='individual',
            entry_fee='Free', entry_fee_price=0, status='published',
        )

    def join(self, tournament, **extra):
        body = {'tournament_id': tournament.tournament_id}
        body.update(extra)
        return self.client.post(
            '/tournament/join-tournament/', data=json.dumps(body),
            content_type='application/json', **self.auth,
        )

    def test_an_overlapping_tournament_warns_and_names_the_clash(self):
        res = self.join(self.overlapping)
        self.assertEqual(res.status_code, 409)
        body = res.json()
        self.assertEqual(body['code'], 'SCHEDULE_CONFLICT')
        self.assertEqual(body['data']['conflict']['title'], 'Saturday Cup')

    def test_the_warning_can_be_acknowledged(self):
        res = self.join(self.overlapping, acknowledge_overlap=True)
        self.assertNotEqual(res.status_code, 409)

    def test_a_tournament_on_another_day_does_not_warn(self):
        res = self.join(self.later)
        self.assertNotEqual(res.status_code, 409)
