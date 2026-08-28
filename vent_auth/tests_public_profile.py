"""A profile answers to its username, which is the address people share.

/u/3 was the address the platform handed out, and the endpoint behind it only
took a numeric id. That is against the slug rule, it lets anybody walk the user
table by counting, and it meant the profile page could not describe itself for a
link preview.
"""
from django.test import TestCase

from .models import Users


class PublicProfileTests(TestCase):
    def setUp(self):
        self.user = Users.objects.create(
            username='Temi', email='temi@vent.test', full_name='Temi A',
            is_active=True, country='NG')

    def test_a_username_resolves(self):
        res = self.client.get('/user/Temi/profile/')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['username'], 'Temi')

    def test_the_username_is_not_case_sensitive(self):
        """People type their own name however they remember it."""
        self.assertEqual(self.client.get('/user/temi/profile/').status_code, 200)
        self.assertEqual(self.client.get('/user/TEMI/profile/').status_code, 200)

    def test_the_old_numeric_address_still_works(self):
        """Links shared before this keep working."""
        res = self.client.get('/user/%s/profile/' % self.user.user_id)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['username'], 'Temi')

    def test_an_unknown_name_is_a_404(self):
        res = self.client.get('/user/nobody-at-all/profile/')
        self.assertEqual(res.status_code, 404, res.content)

    def test_a_deactivated_profile_is_not_public(self):
        self.user.is_deactivated = True
        self.user.save(update_fields=['is_deactivated'])
        self.assertEqual(self.client.get('/user/Temi/profile/').status_code, 404)

    def test_it_carries_what_a_link_preview_needs(self):
        """A shared profile has to be able to describe itself."""
        data = self.client.get('/user/Temi/profile/').json()['data']
        self.assertIn('username', data)
        self.assertIn('full_name', data)
        self.assertIn('profile_picture', data)
