"""What a sign-in alert may claim, and what a location guess may overwrite.

CEO, 30 August 2026, on an alert that placed a Lagos sign-in in Ilorin: "the
ilorik there is wrong". It was, and it always will be for that address: an IP on
a mobile network belongs to the carrier's gateway, not the handset.

Two faults came out of one screenshot. The email asserted a city as fact, and
the daily refresh behind it wrote that same guess over whatever the account had
already been told.
"""
import uuid
from unittest import mock

from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from .models import LoginEvent, Users


def a_user(**kwargs):
    fields = dict(
        username='alerts_%s' % uuid.uuid4().hex[:6],
        email='alerts_%s@vent.test' % uuid.uuid4().hex[:6],
        full_name='Layott',
    )
    fields.update(kwargs)
    return Users.objects.create(**fields)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class LoginAlertTests(TestCase):
    def setUp(self):
        mail.outbox = []
        self.user = a_user()
        LoginEvent.objects.create(
            user=self.user, ip='102.93.14.233', city='Ilorin', country='Nigeria',
            user_agent='Mozilla/5.0 (Linux; Android 14) Chrome/120', method='password',
        )

    def _send(self):
        from .emails import send_login_alert
        self.assertTrue(send_login_alert(self.user))
        return mail.outbox[0].message().as_string()

    def test_the_alert_names_the_country(self):
        body = self._send()
        self.assertIn('Nigeria', body)

    def test_the_alert_does_not_name_a_city(self):
        """A city read off a mobile IP is the carrier's exchange. Printing it as
        fact answers "was that you?" with "no" for a sign-in that was."""
        self.assertNotIn('Ilorin', self._send())

    def test_the_alert_says_where_the_country_came_from(self):
        self.assertIn('network address', self._send())

    def test_the_alert_still_carries_the_device_and_the_address(self):
        """The two things that actually identify a session."""
        body = self._send()
        self.assertIn('102.93.14.233', body)
        self.assertIn('Chrome on Android', body)

    def test_the_logo_is_inline_and_the_related_part_names_its_root(self):
        """RFC 2387 makes `type` required on multipart/related: it says which
        enclosed part is the document. Django leaves it off, and Gmail on
        Android drew the V-ENT mark as a broken image because of it."""
        self._send()
        message = mail.outbox[0].message()
        self.assertEqual(message.get_content_subtype(), 'related')
        self.assertEqual(message.get_param('type'), 'multipart/alternative')
        # The boundary is only written when the message is serialised, so it
        # is checked on the wire form rather than on the header object.
        self.assertIn('boundary=', mail.outbox[0].message().as_string()[:1200])

        images = [p for p in message.walk() if p.get_content_type() == 'image/png']
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]['Content-ID'], '<ventlogo>')


class LocationRefreshTests(TestCase):
    """A guess fills a blank. It never overwrites an answer."""

    def setUp(self):
        self.factory = RequestFactory()

    def _request(self):
        request = self.factory.get('/')
        request.META['REMOTE_ADDR'] = '102.93.14.233'
        return request

    def _refresh(self, user, located=('Nigeria', 'Ilorin')):
        from vent_auth import geo
        with mock.patch.object(geo, 'locate', return_value=located):
            return geo.refresh_daily_location(user, self._request())

    def test_it_fills_a_blank_country(self):
        user = a_user(country='', state='')
        self.assertTrue(self._refresh(user))
        user.refresh_from_db()
        self.assertEqual(user.country, 'Nigeria')
        self.assertEqual(user.state, 'Ilorin')

    def test_it_leaves_a_country_the_person_chose(self):
        """`country` gates who may accept a challenge. A wrong guess here locks
        somebody out of challenges in their own country."""
        user = a_user(country='Ghana', state='Accra')
        self._refresh(user)
        user.refresh_from_db()
        self.assertEqual(user.country, 'Ghana')
        self.assertEqual(user.state, 'Accra')

    def test_it_still_records_the_address_and_the_time(self):
        user = a_user(country='Ghana')
        self._refresh(user)
        user.refresh_from_db()
        self.assertEqual(user.last_login_ip, '102.93.14.233')
        self.assertIsNotNone(user.location_updated_at)

    def test_it_does_not_run_twice_in_one_day(self):
        user = a_user(country='', state='')
        self.assertTrue(self._refresh(user))
        self.assertFalse(self._refresh(user))

    def test_a_private_address_is_skipped(self):
        user = a_user(country='', state='')
        request = self.factory.get('/')
        request.META['REMOTE_ADDR'] = '127.0.0.1'
        from vent_auth import geo
        self.assertFalse(geo.refresh_daily_location(user, request))
        user.refresh_from_db()
        self.assertEqual(user.country or '', '')

    def test_a_state_is_only_filled_when_the_country_lookup_worked(self):
        user = a_user(country='', state='')
        self.assertFalse(self._refresh(user, located=(None, None)))
        user.refresh_from_db()
        self.assertEqual(user.state or '', '')
