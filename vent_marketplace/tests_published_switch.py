"""What the site is told about whether the marketplace is open.

The nav, the page and the endpoints have to agree. If they do not, somebody
taps Marketplace, sees a live-looking screen and gets a 503 when they press
anything, which is the "control that renders live and fails on press" fault the
project rules ban by name.

Two switches decide it and they are not equals:

  * `MARKETPLACE_ENABLED` on the server, defaulting to OFF. Every endpoint
    refuses while it says so.
  * `feature_flags.marketplace_enabled` in the admin console, an admin's
    day-to-day decision.

The published answer is the AND. Neither can open it alone.
"""
from django.test import TestCase, override_settings

from vent_auth.models import AdminSetting


def set_console_flag(value):
    row = AdminSetting.load()
    data = dict(row.data or {})
    flags = dict(data.get('feature_flags') or {})
    flags['marketplace_enabled'] = value
    data['feature_flags'] = flags
    row.data = data
    row.save(update_fields=['data'])


class PublishedSwitchTests(TestCase):

    def _published(self):
        res = self.client.get('/auth/platform/modules/')
        self.assertEqual(res.status_code, 200)
        return res.json()['data']['feature_flags']

    @override_settings(MARKETPLACE_ENABLED=False)
    def test_the_console_alone_cannot_open_it(self):
        set_console_flag(True)
        self.assertFalse(self._published()['marketplace_enabled'])

    @override_settings(MARKETPLACE_ENABLED=True)
    def test_the_server_alone_cannot_open_it(self):
        set_console_flag(False)
        self.assertFalse(self._published()['marketplace_enabled'])

    @override_settings(MARKETPLACE_ENABLED=True)
    def test_both_together_open_it(self):
        set_console_flag(True)
        self.assertTrue(self._published()['marketplace_enabled'])

    @override_settings(MARKETPLACE_ENABLED=False)
    def test_the_default_answer_is_closed(self):
        self.assertFalse(self._published()['marketplace_enabled'])

    @override_settings(MARKETPLACE_ENABLED=True)
    def test_the_other_modules_are_untouched(self):
        """A flag nobody asked about must not change because of this."""
        row = AdminSetting.load()
        data = dict(row.data or {})
        flags = dict(data.get('feature_flags') or {})
        flags['shop_enabled'] = True
        data['feature_flags'] = flags
        row.data = data
        row.save(update_fields=['data'])
        self.assertTrue(self._published()['shop_enabled'])
