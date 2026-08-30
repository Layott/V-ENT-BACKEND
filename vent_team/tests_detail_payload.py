"""What the team detail payload has to carry for the pages built on it.

Both of these were reported by the CEO on 30 August 2026, from one screenshot:
somebody created a team, pressed Manage, and was told "Access denied - Only the
owner of AVALANCHE GAMING can edit this team" on their own team.

Two separate faults met there. The gate itself was a frontend race, fixed in
`edit-team-profile/page.js`. These are the two halves the server owes that page:
the slug it should have been addressed by, and the viewer flag it decides
ownership from.
"""
import uuid

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, TeamJoinRequest, TeamMembers, Teams, Users


def signed_in(username):
    user = Users.objects.create(
        username=username, email='%s@vent.test' % username,
        login_session_token=('tok%s' % uuid.uuid4().hex)[:16],
        login_session_created_at=timezone.now(), is_active=True,
    )
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}

class SignedInStrangerTests(TestCase):
    """The role that is neither signed out nor the owner.

    `_viewer_state` returns early for a signed-out visitor and short-circuits
    for anybody already in the team, so the join-request lookup only ever runs
    for a signed-in non-member. It queried a field that does not exist, which
    made a team page a 500 for exactly that person and nobody else.
    """

    def setUp(self):
        self.game = Games.objects.get_or_create(game_title='Free Fire')[0]
        self.owner, _ = signed_in('avalanche_boss')
        self.stranger, self.stranger_auth = signed_in('passerby')
        self.member, self.member_auth = signed_in('squadmate')
        self.team = Teams.objects.create(
            team_name='Avalanche Gaming', game=self.game, description='',
            team_creator=self.owner, team_owner=self.owner,
            penalty_points=0, number_of_members=1,
            allow_membership_requests=True,
        )
        TeamMembers.objects.create(team=self.team, user=self.member)

    def _get(self, auth=None):
        return self.client.get('/team/view-team/%s/' % self.team.team_id, **(auth or {}))

    def test_a_signed_in_stranger_can_open_a_team_page(self):
        res = self._get(self.stranger_auth)
        self.assertEqual(res.status_code, 200)
        body = res.json()['data']['team']
        self.assertFalse(body['viewer_is_owner'])
        self.assertFalse(body['viewer_is_member'])
        self.assertEqual(body['viewer_request_status'], 'none')

    def test_a_stranger_who_has_asked_to_join_is_told_so(self):
        TeamJoinRequest.objects.create(
            team=self.team, applicant=self.stranger, status='pending')
        body = self._get(self.stranger_auth).json()['data']['team']
        self.assertEqual(body['viewer_request_status'], 'pending')

    def test_a_member_is_a_member(self):
        body = self._get(self.member_auth).json()['data']['team']
        self.assertTrue(body['viewer_is_member'])
        self.assertFalse(body['viewer_is_owner'])
