"""The settings endpoints store only what the settings screens offer (inbox 398).

Owner rule R69. Until 30 September 2026 `/setting/update/` and the section
endpoints stored every key a body carried, of any size, into the account's
settings. Reading them field by field also found three switches that had been
saved under names nothing read:

  allow_dm_from      saved by the Privacy panel; messaging read allow_direct_messages
  search_indexable   saved by the Privacy panel; nothing read it at all
  currency           saved at the top by the Language panel; money.js read
                     payments.default_currency

Each is written here and read back the way its reader reads it.
"""
import uuid

from django.test import TestCase
from django.utils import timezone as tz
from rest_framework.test import APIClient

from .models import Users, UserSetting
from .views_profile import privacy_of


def a_user(name='ss'):
    u = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16], is_active=True)
    u.login_session_created_at = tz.now()
    u.save()
    return u, {'HTTP_AUTHORIZATION': 'Bearer %s' % u.login_session_token}


class SettingsSchemaTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user, self.auth = a_user()

    def stored(self):
        return UserSetting.objects.get(user=self.user).data

    def post(self, path, body):
        return self.client.post(path, body, format='json', **self.auth)

    def test_a_key_no_screen_offers_is_dropped_and_named(self):
        res = self.post('/setting/update/', {'language': 'fr', 'is_admin': True,
                                              'junk': 'x' * 5000})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['ignored'], ['is_admin', 'junk'])
        self.assertEqual(self.stored(), {'language': 'fr'})

    def test_a_value_outside_its_choices_is_refused(self):
        res = self.post('/setting/privacy/update/', {'profile_visibility': 'everyone-ish'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'INVALID_INPUT')
        self.assertEqual(res.data['field'], 'profile_visibility')

    def test_two_factor_cannot_be_switched_on_by_a_request(self):
        res = self.post('/setting/security/update/', {'two_factor_enabled': True,
                                                      'login_alerts': False})
        self.assertEqual(res.data['data']['ignored'], ['two_factor_enabled'])
        self.assertEqual(self.stored()['security'], {'login_alerts': False})
        self.assertFalse(res.data['data']['security']['two_factor_enabled'])

    def test_the_old_message_switch_is_what_messaging_obeys(self):
        self.post('/setting/privacy/update/', {'allow_dm_from': 'nobody'})
        self.assertEqual(privacy_of(self.user)['allow_direct_messages'], 'nobody')
        got = self.client.get('/setting/', **self.auth).data['data']['settings']
        self.assertEqual(got['privacy']['allow_direct_messages'], 'nobody')

    def test_a_row_saved_before_the_fix_reads_under_the_new_name(self):
        UserSetting.objects.create(user=self.user, data={
            'privacy': {'allow_dm_from': 'followers', 'search_indexable': False}})
        self.assertEqual(privacy_of(self.user)['allow_direct_messages'], 'followers')
        self.assertFalse(privacy_of(self.user)['indexable'])

    def test_the_currency_chosen_on_the_language_panel_is_the_one_read(self):
        self.post('/setting/update/', {'currency': 'ghs'})
        got = self.client.get('/setting/', **self.auth).data['data']['settings']
        self.assertEqual(got['payments']['default_currency'], 'GHS')

    def test_a_zone_that_does_not_exist_is_refused(self):
        res = self.post('/setting/update/', {'timezone': 'Mars/Olympus'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['field'], 'timezone')
        self.assertEqual(self.post('/setting/update/', {'timezone': 'Africa/Accra'}).status_code, 200)

    def test_the_walkthrough_is_kept_small(self):
        ok = self.post('/setting/update/', {'walkthrough': {
            'completed_at': '2026-09-30T10:00:00Z', 'skipped': False, 'version': 3,
            'chapters_seen': ['wallet', 'tournaments']}})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.stored()['walkthrough']['version'], 3)
        big = self.post('/setting/update/', {'walkthrough': {
            'chapters_seen': ['c%d' % i for i in range(500)]}})
        self.assertEqual(big.status_code, 400)

    def test_saved_cards_in_the_body_are_not_stored_as_settings(self):
        """Cards live on SavedCard rows; the panel resends its list with each save."""
        res = self.post('/setting/payments/update/', {
            'default_method': 'card', 'saved_cards': [{'id': 1}], 'saved_banks': []})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.stored()['payments'], {'default_method': 'card'})
