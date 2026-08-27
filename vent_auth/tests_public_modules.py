"""The module switches in the console are published to the site.

/admin/settings has had a Modules panel since it was built. Nothing read it, so
an admin could switch the shop on, save, see a success toast, and change nothing
anywhere. The console reported success for an instruction it never carried out.
"""
from django.test import TestCase

from .models import AdminSetting


class PublicPlatformModulesTests(TestCase):
    url = '/auth/platform/modules/'

    def test_anybody_can_read_it(self):
        """A signed-out visitor's page depends on this too."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['status'], 'success')

    def test_it_publishes_the_switches(self):
        response = self.client.get(self.url)
        flags = response.json()['data']['feature_flags']
        self.assertIn('shop_enabled', flags)
        self.assertIn('events_enabled', flags)
        self.assertIsInstance(flags['shop_enabled'], bool)

    def test_a_change_in_the_console_is_visible_to_the_site(self):
        """The whole point: saving in the console changes what visitors get."""
        before = self.client.get(self.url).json()['data']['feature_flags']
        self.assertFalse(before['shop_enabled'])

        setting = AdminSetting.load()
        data = setting.data or {}
        data.setdefault('feature_flags', {})['shop_enabled'] = True
        setting.data = data
        setting.save()

        after = self.client.get(self.url).json()['data']['feature_flags']
        self.assertTrue(after['shop_enabled'])

    def test_it_does_not_publish_the_fees(self):
        """Only the switches and the notices. Nothing else on the record."""
        data = self.client.get(self.url).json()['data']
        self.assertEqual(set(data), {'feature_flags', 'banner', 'maintenance'})
        self.assertNotIn('platform_fees', data)

    def test_the_banner_and_maintenance_notice_travel_with_it(self):
        """Both are shown to visitors, so they do not need a second request."""
        setting = AdminSetting.load()
        data = setting.data or {}
        data['banner'] = {'enabled': True, 'title': 'Scheduled work',
                          'message': 'Tickets pause at 22:00.', 'type': 'warn'}
        setting.data = data
        setting.save()

        banner = self.client.get(self.url).json()['data']['banner']
        self.assertTrue(banner['enabled'])
        self.assertEqual(banner['title'], 'Scheduled work')
        self.assertEqual(banner['type'], 'warn')
