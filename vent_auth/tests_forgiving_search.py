"""Search bars forgive a wrong or partial name (inbox 383), through the real views."""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Organization, Teams, Users


class ForgivingSearchTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.me = Users.objects.create(username='fz_viewer', email='v@fz.test', is_active=True)
        self.winlola = Users.objects.create(username='Winlola', email='w@fz.test', full_name='Winlola Ade', is_active=True)
        Users.objects.create(username='someone_else', email='s@fz.test', is_active=True)
        game = Games.objects.create(game_title='Free Fire FZ')
        Teams.objects.create(team_name='Port Harcourt Kings', game=game, description='x',
                             team_creator=self.me, team_owner=self.me, penalty_points=0,
                             number_of_members=1, creation_date=timezone.now().date())
        Organization.objects.create(org_name='Vermillion Encore', tag='VE',
                                    org_creator=self.me, org_owner=self.me)

    def names(self, url, key, field):
        body = self.client.get(url).json()
        return [row[field] for row in body['data'][key]]

    def test_people_by_part_and_by_typo(self):
        for q in ('Winlo', 'winlila', 'wniloal', 'ade'):
            with self.subTest(q=q):
                self.assertIn('Winlola', self.names('/user/search/?q=%s' % q, 'users', 'username'))

    def test_teams_by_typo_and_missing_words(self):
        for q in ('port harcort', 'harcourt', 'kigns'):
            with self.subTest(q=q):
                self.assertIn('Port Harcourt Kings', self.names('/team/list-teams/?search=%s' % q, 'teams', 'name'))

    def test_organisations_by_typo(self):
        for q in ('vermilion', 'encor', 'VE'):
            with self.subTest(q=q):
                orgs = self.names('/organization/list/?search=%s' % q, 'organizations', 'name')
                self.assertIn('Vermillion Encore', orgs)

    def test_nothing_alike_finds_nothing(self):
        self.assertEqual(self.names('/team/list-teams/?search=zzzz', 'teams', 'name'), [])
