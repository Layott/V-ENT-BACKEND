"""GET /users/indexable/ - the profiles the sitemap lists (inbox 460).

Only accounts a visitor may see and whose owner left "show in search" on.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Users, UserSetting


class IndexableProfilesTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        def person(name, **extra):
            return Users.objects.create(username=name, email='%s@example.com' % name,
                                        is_active=True, **extra)

        self.open = person('open_player')
        self.hidden = person('hidden_player')
        UserSetting.objects.create(user=self.hidden, data={'privacy': {'indexable': False}})
        self.legacy = person('legacy_player')
        UserSetting.objects.create(user=self.legacy, data={'privacy': {'search_indexable': False}})
        self.private = person('private_player')
        UserSetting.objects.create(user=self.private, data={'privacy': {'profile_visibility': 'private'}})
        self.followers = person('followers_player')
        UserSetting.objects.create(user=self.followers, data={'privacy': {'profile_visibility': 'followers'}})
        person('gone_player', is_deactivated=True)
        Users.objects.create(username='unverified_player', email='u@example.com', is_active=False)

    def names(self):
        res = self.client.get('/users/indexable/')
        self.assertEqual(res.status_code, 200, res.content[:300])
        body = res.json()
        self.assertEqual(body['status'], 'success')
        return [row['username'] for row in body['data']['profiles']], body

    def test_a_public_profile_is_listed_without_signing_in(self):
        names, body = self.names()
        self.assertIn('open_player', names)
        self.assertEqual(body['data']['count'], len(names))

    def test_indexing_off_in_either_spelling_is_left_out(self):
        names, _ = self.names()
        self.assertNotIn('hidden_player', names)
        self.assertNotIn('legacy_player', names)

    def test_a_private_or_followers_profile_is_left_out(self):
        names, _ = self.names()
        self.assertNotIn('private_player', names)
        self.assertNotIn('followers_player', names)

    def test_deactivated_and_unconfirmed_accounts_are_left_out(self):
        names, _ = self.names()
        self.assertNotIn('gone_player', names)
        self.assertNotIn('unverified_player', names)

    def test_nothing_but_the_username_and_a_date_leaves(self):
        _, body = self.names()
        row = next(r for r in body['data']['profiles'] if r['username'] == 'open_player')
        self.assertEqual(set(row), {'username', 'updated_at'})
        self.assertNotIn('@example.com', str(body))
