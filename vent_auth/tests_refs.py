"""A team named by a picker's slug is found at every door (inbox 416).

Putting a team straight into a tournament, inviting one, and challenging one
to a scrim looked a team up by its exact name only; a slug picked from the
list answered "No team by that name".
"""
from django.test import TestCase

from vent_auth.models import Games, Organization, Teams, Users
from vent_auth.refs import org_by_ref, team_by_ref


class RefsTests(TestCase):
    def setUp(self):
        owner = Users.objects.create(username='refowner', email='refowner@vent.test')
        game = Games.objects.create(game_title='Ref Game')
        self.team = Teams.objects.create(team_name='Lagos Night Owls', game=game,
                                         description='', team_creator=owner,
                                         team_owner=owner, penalty_points=0,
                                         number_of_members=1)
        self.org = Organization.objects.create(org_name='Owl Collective',
                                               org_creator=owner, org_owner=owner)

    def test_a_team_by_slug_name_any_case_or_old_id(self):
        self.assertEqual(team_by_ref(self.team.slug), self.team)
        self.assertEqual(team_by_ref('lagos night owls'), self.team)
        self.assertEqual(team_by_ref(str(self.team.team_id)), self.team)
        self.assertIsNone(team_by_ref('nobody-here'))
        self.assertIsNone(team_by_ref(''))

    def test_an_organisation_by_slug_or_name(self):
        self.assertEqual(org_by_ref(self.org.slug), self.org)
        self.assertEqual(org_by_ref('OWL COLLECTIVE'), self.org)
        self.assertIsNone(org_by_ref('   '))
