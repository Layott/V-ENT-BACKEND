"""Currency, timezone and date format: saved AND readable.

CEO, 7 September 2026, of the Currency and region panel: "do these work?"

They did not, and each failed differently:

  currency      saved to the account, read by nothing. The real preference
                lived in a separate localStorage key the panel never touched.
  timezone      saved and returned, but every date rendered in the BROWSER's
                zone, so the setting changed nothing on screen.
  date format   saved by the panel and then DROPPED on every read, because
                `_merged` builds its answer by walking DEFAULT_SETTINGS and
                the key was not in it.

The last one is the reason these tests exist rather than a comment. A value
that stores fine and vanishes on read is invisible to every test that only
checks the write, and invisible to anybody reading the view, because
`data.update(incoming)` looks like it works.

So each of the three is written and then READ BACK on a fresh request.
"""
import uuid

from django.test import TestCase
from django.utils import timezone as tz
from rest_framework.test import APIClient

from .models import Users
from .views_settings import DEFAULT_SETTINGS


def a_user(name='rg'):
    u = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16], is_active=True)
    u.login_session_created_at = tz.now()
    u.save()
    return u, {'HTTP_AUTHORIZATION': 'Bearer %s' % u.login_session_token}


class SurvivesTheRoundTripTests(TestCase):
    """Written, then read back on a separate request."""

    def setUp(self):
        self.client = APIClient()
        self.user, self.auth = a_user()

    def _get(self):
        res = self.client.get('/setting/', **self.auth)
        self.assertEqual(res.status_code, 200)
        return res.json()['data']['settings']

    def test_date_format_is_returned_and_not_silently_dropped(self):
        """The one that was actually broken.

        `_update_section` merges anything into the stored JSON, so the write
        always looked fine. `_merged` walks DEFAULT_SETTINGS, so a key missing
        from there is stored and then never returned.
        """
        self.client.post('/setting/update/', {'date_format': 'DD/MM/YYYY'},
                         format='json', **self.auth)
        self.assertEqual(self._get().get('date_format'), 'DD/MM/YYYY')

    def test_timezone_is_returned(self):
        self.client.post('/setting/update/', {'timezone': 'Africa/Accra'},
                         format='json', **self.auth)
        self.assertEqual(self._get().get('timezone'), 'Africa/Accra')

    def test_currency_is_returned(self):
        self.client.post('/setting/payments/update/', {'default_currency': 'GHS'},
                         format='json', **self.auth)
        self.assertEqual(self._get()['payments']['default_currency'], 'GHS')

    def test_an_unset_date_format_is_empty_rather_than_absent(self):
        """The frontend reads `settings.date_format` and falls back on ''.

        Absent and '' behave the same in JavaScript, but a KEY that is never
        present is the shape that made this invisible in the first place, so
        it is asserted explicitly.
        """
        self.assertIn('date_format', self._get())
        self.assertEqual(self._get()['date_format'], '')


class DefaultsAreReadableTests(TestCase):
    """Every key the settings screens read must be in DEFAULT_SETTINGS.

    Not a style rule: `_merged` uses that dict as its allow-list, so a key
    missing from it can be saved and can never be read. This is the catcher
    for the whole class rather than for the one field that happened to be
    reported.
    """

    READ_BY_A_SCREEN = ('language', 'region', 'timezone', 'date_format',
                        'notifications', 'privacy', 'security', 'payments',
                        'walkthrough')

    def test_every_setting_a_screen_reads_is_in_the_defaults(self):
        for key in self.READ_BY_A_SCREEN:
            self.assertIn(
                key, DEFAULT_SETTINGS,
                '%s is read by a settings screen but is not in '
                'DEFAULT_SETTINGS, so _merged will drop it on every read' % key)


class TwoFactorTruthTests(TestCase):
    """The 2FA row reports real enrolment, not a flag nobody writes."""

    def setUp(self):
        self.client = APIClient()
        self.user, self.auth = a_user('tf')

    def _security(self):
        res = self.client.get('/setting/', **self.auth)
        return res.json()['data']['settings']['security']

    def test_off_when_nothing_is_enrolled(self):
        self.assertIs(self._security()['two_factor_enabled'], False)

    def test_on_when_an_authenticator_is_confirmed(self):
        from .models import UserTOTP
        UserTOTP.objects.create(user=self.user, secret='x' * 32, confirmed=True)
        self.assertIs(self._security()['two_factor_enabled'], True)

    def test_a_started_but_unconfirmed_enrolment_does_not_count(self):
        """Half-finished setup is not protection, and must not read as it."""
        from .models import UserTOTP
        UserTOTP.objects.create(user=self.user, secret='x' * 32, confirmed=False)
        self.assertIs(self._security()['two_factor_enabled'], False)

    def test_the_stored_flag_cannot_lie_in_either_direction(self):
        """Somebody could have a stale True in their stored settings from
        before this was fixed. Real enrolment decides, not the leftover."""
        self.client.post('/setting/security/update/', {'two_factor_enabled': True},
                         format='json', **self.auth)
        self.assertIs(self._security()['two_factor_enabled'], False)
