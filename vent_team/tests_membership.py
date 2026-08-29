"""Getting into a team, and what a role actually lets you do.

CEO, 29 August 2026: "There is no way for me to add players to my teams or
invite people, or get a link players can use to join directly. no where to also
manage the roles of players in the team and the access they have and what they
can control."

Before this a player could ask to join and be accepted, and that was the only
door. Roles existed as seven words on a member card, and exactly two of them
meant anything, in one function, decided inline.
"""
import json
import uuid

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, TeamInvite, TeamMembers, Teams, Users

from . import permissions as perms


def a_user(name):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class TeamMembershipTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('owner')
        self.game, _ = Games.objects.get_or_create(game_title='Free Fire')
        self.team = Teams.objects.create(
            team_name='Rangers %s' % uuid.uuid4().hex[:5], game=self.game,
            description='x', team_creator=self.owner, team_owner=self.owner,
            penalty_points=0, number_of_members=1,
        )
        TeamMembers.objects.create(team=self.team, user=self.owner, role='owner',
                                   is_captain=True)
        self.ref = self.team.slug or self.team.team_id

    def _post(self, path, body=None, auth=None):
        return self.client.post(path, data=json.dumps(body or {}),
                                content_type='application/json',
                                **(auth or self.owner_auth))

    # ------------------------------------------------------ inviting a player
    def test_the_owner_invites_a_named_player(self):
        rival, _ = a_user('player')
        res = self._post('/team/%s/invites/' % self.ref,
                         {'username': rival.username, 'role': 'captain'})
        self.assertEqual(res.status_code, 201, res.content[:300])
        invite = TeamInvite.objects.get()
        self.assertEqual(invite.user_id, rival.user_id)
        self.assertEqual(invite.role, 'captain')

    def test_inviting_twice_reminds_rather_than_making_a_second_row(self):
        """Their list should never show the same team twice, and accepting
        cannot be allowed to happen twice."""
        rival, _ = a_user('player')
        for _ in range(2):
            self._post('/team/%s/invites/' % self.ref, {'username': rival.username})
        self.assertEqual(TeamInvite.objects.filter(user=rival).count(), 1)

    def test_somebody_already_in_the_team_cannot_be_invited(self):
        res = self._post('/team/%s/invites/' % self.ref,
                         {'username': self.owner.username})
        self.assertEqual(res.status_code, 409)

    def test_the_invited_player_sees_it_and_joins(self):
        rival, rival_auth = a_user('player')
        self._post('/team/%s/invites/' % self.ref, {'username': rival.username})

        mine = self.client.get('/team/my-invites/', **rival_auth).json()
        self.assertEqual(mine['data']['count'], 1)
        row = mine['data']['invites'][0]
        # The invited player sees this without the team page around it, so it
        # has to say which team is asking.
        self.assertEqual(row['team_name'], self.team.team_name)
        self.assertTrue(row['team_slug'])
        invite_id = row['id']

        res = self._post('/team/invite/%s/respond/' % invite_id, {'accept': True},
                         auth=rival_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertTrue(TeamMembers.objects.filter(team=self.team, user=rival).exists())

    def test_declining_does_not_join(self):
        rival, rival_auth = a_user('player')
        self._post('/team/%s/invites/' % self.ref, {'username': rival.username})
        invite = TeamInvite.objects.get()
        self._post('/team/invite/%s/respond/' % invite.id, {'accept': False},
                   auth=rival_auth)
        self.assertFalse(TeamMembers.objects.filter(team=self.team, user=rival).exists())

    def test_an_invitation_addressed_to_somebody_else_is_refused(self):
        rival, _ = a_user('player')
        stranger, stranger_auth = a_user('stranger')
        self._post('/team/%s/invites/' % self.ref, {'username': rival.username})
        invite = TeamInvite.objects.get()
        res = self._post('/team/invite/%s/respond/' % invite.id, {'accept': True},
                         auth=stranger_auth)
        self.assertEqual(res.status_code, 403)

    # --------------------------------------------------------- the join link
    def test_the_owner_gets_a_link_to_hand_out(self):
        res = self._post('/team/%s/invites/' % self.ref, {'kind': 'link'})
        self.assertEqual(res.status_code, 201, res.content[:300])
        row = res.json()['data']['invite']
        self.assertTrue(row['token'])
        self.assertTrue(row['url'].startswith('https://v-ent.co/teams/join/'))

    def test_anybody_can_read_the_link_without_signing_in(self):
        """Somebody following it from a group chat sees which team it is
        before being asked to sign in."""
        self._post('/team/%s/invites/' % self.ref, {'kind': 'link'})
        token = TeamInvite.objects.get().token
        res = self.client.get('/team/join/%s/' % token)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['team']['name'], self.team.team_name)

    def test_following_the_link_joins_the_team(self):
        self._post('/team/%s/invites/' % self.ref, {'kind': 'link', 'role': 'coach'})
        token = TeamInvite.objects.get().token
        newcomer, newcomer_auth = a_user('newcomer')

        res = self._post('/team/join/%s/' % token, {}, auth=newcomer_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        row = TeamMembers.objects.get(team=self.team, user=newcomer)
        self.assertEqual(row.role, 'coach')

    def test_a_link_can_be_capped(self):
        """One leaked link must not be a permanent open door."""
        self._post('/team/%s/invites/' % self.ref, {'kind': 'link', 'max_uses': 1})
        token = TeamInvite.objects.get().token

        _, first_auth = a_user('first')
        self._post('/team/join/%s/' % token, {}, auth=first_auth)

        _, second_auth = a_user('second')
        res = self._post('/team/join/%s/' % token, {}, auth=second_auth)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'LINK_SPENT')

    def test_an_expired_link_is_refused_when_it_is_used(self):
        """Checked at the moment of use, not of creation: a link that expired
        while somebody had the page open must not still work."""
        self._post('/team/%s/invites/' % self.ref, {'kind': 'link'})
        invite = TeamInvite.objects.get()
        invite.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        invite.save(update_fields=['expires_at'])

        _, auth = a_user('late')
        res = self._post('/team/join/%s/' % invite.token, {}, auth=auth)
        self.assertEqual(res.status_code, 409)

    def test_a_revoked_link_stops_working(self):
        self._post('/team/%s/invites/' % self.ref, {'kind': 'link'})
        invite = TeamInvite.objects.get()
        self._post('/team/%s/invites/%s/revoke/' % (self.ref, invite.id))

        _, auth = a_user('toolate')
        res = self._post('/team/join/%s/' % invite.token, {}, auth=auth)
        self.assertEqual(res.status_code, 409)

    # ------------------------------------------------------- roles and powers
    def test_the_role_catalogue_says_what_each_role_can_do(self):
        res = self.client.get('/team/roles/')
        self.assertEqual(res.status_code, 200)
        roles = {r['role']: r for r in res.json()['data']['roles']}
        self.assertIn(perms.INVITE, roles['captain']['permissions'])
        self.assertEqual(roles['coach']['permissions'], [])
        self.assertTrue(roles['manager']['blurb'])

    def test_the_roster_says_what_the_viewer_may_do(self):
        """So the page draws the controls they can use and no others."""
        res = self.client.get('/team/%s/roster/' % self.ref, **self.owner_auth)
        data = res.json()['data']
        self.assertEqual(data['my_role'], 'owner')
        self.assertIn(perms.SET_ROLE, data['my_permissions'])

    def test_the_owner_changes_somebody_s_role(self):
        member, _ = a_user('member')
        TeamMembers.objects.create(team=self.team, user=member, role='member')
        res = self._post('/team/%s/set-role/' % self.ref,
                         {'username': member.username, 'role': 'manager'})
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(TeamMembers.objects.get(team=self.team, user=member).role,
                         'manager')

    def test_a_coach_cannot_invite(self):
        """A coach is part of the team and runs none of it. This was the gap:
        every role except owner and captain had exactly the powers of a
        member, which is to say none, and nothing said so."""
        coach, coach_auth = a_user('coach')
        TeamMembers.objects.create(team=self.team, user=coach, role='coach')
        rival, _ = a_user('rival')
        res = self._post('/team/%s/invites/' % self.ref,
                         {'username': rival.username}, auth=coach_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'ROLE_NOT_ALLOWED')

    def test_a_captain_can_invite(self):
        captain, captain_auth = a_user('captain')
        TeamMembers.objects.create(team=self.team, user=captain, role='captain')
        rival, _ = a_user('rival')
        res = self._post('/team/%s/invites/' % self.ref,
                         {'username': rival.username}, auth=captain_auth)
        self.assertEqual(res.status_code, 201, res.content[:300])

    def test_a_captain_cannot_remove_another_captain(self):
        """So two captains cannot remove each other."""
        one, one_auth = a_user('captain_one')
        two, _ = a_user('captain_two')
        TeamMembers.objects.create(team=self.team, user=one, role='captain')
        TeamMembers.objects.create(team=self.team, user=two, role='captain')
        res = self._post('/team/%s/remove/' % self.ref, {'username': two.username},
                         auth=one_auth)
        self.assertEqual(res.status_code, 403)

    def test_a_captain_can_remove_an_ordinary_member(self):
        captain, captain_auth = a_user('captain')
        member, _ = a_user('member')
        TeamMembers.objects.create(team=self.team, user=captain, role='captain')
        TeamMembers.objects.create(team=self.team, user=member, role='member')
        res = self._post('/team/%s/remove/' % self.ref, {'username': member.username},
                         auth=captain_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])

    def test_the_owner_cannot_be_removed(self):
        res = self._post('/team/%s/remove/' % self.ref,
                         {'username': self.owner.username})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'CANNOT_REMOVE_OWNER')

    def test_owner_is_not_a_role_you_can_be_given(self):
        """It is transferred, deliberately, through an endpoint that says so."""
        member, _ = a_user('member')
        TeamMembers.objects.create(team=self.team, user=member, role='member')
        res = self._post('/team/%s/set-role/' % self.ref,
                         {'username': member.username, 'role': 'owner'})
        self.assertEqual(res.status_code, 400)

    def test_somebody_outside_the_team_cannot_read_its_invites(self):
        _, stranger_auth = a_user('stranger')
        res = self.client.get('/team/%s/invites/' % self.ref, **stranger_auth)
        self.assertEqual(res.status_code, 403)
