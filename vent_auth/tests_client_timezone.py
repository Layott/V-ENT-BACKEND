"""A datetime typed with no zone is read in the browser's zone.

The server runs on UTC. A programme session typed as 10:30 in Lagos was stored
as 10:30 UTC and shown back as 11:30 (18 September 2026). With the browser
naming its zone on the request, every naive datetime lands where the person
meant it.
"""
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_event.models import Event, EventSession


def organiser():
    user = Users.objects.create(
        username='tz_org', email='tz@vent.test', is_active=True,
        login_session_token='tz-org-token'[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ClientTimezoneTests(TestCase):
    def setUp(self):
        self.user, self.auth = organiser()

    def create(self, **headers):
        start = (timezone.now() + timezone.timedelta(days=10)).strftime('%Y-%m-%dT10:00')
        end = (timezone.now() + timezone.timedelta(days=10)).strftime('%Y-%m-%dT18:00')
        return self.client.post('/event/create-event/', data={
            'name': 'Zone Con', 'event_type': 'physical', 'description': 'x',
            'start_date': start, 'end_date': end, 'location': 'Lagos',
        }, content_type='application/json', **self.auth, **headers)

    def test_a_naive_time_is_read_in_the_browsers_zone(self):
        res = self.create(HTTP_X_CLIENT_TIMEZONE='Africa/Lagos')
        self.assertEqual(res.status_code, 201, res.content)
        stored = Event.objects.get().start_date
        # 10:00 in Lagos (UTC+1) is 09:00 UTC.
        self.assertEqual((stored.hour, stored.minute), (9, 0))

    def test_a_zone_west_of_greenwich_goes_the_other_way(self):
        res = self.create(HTTP_X_CLIENT_TIMEZONE='America/New_York')
        self.assertEqual(res.status_code, 201, res.content)
        stored = Event.objects.get().start_date
        # 10:00 in New York (UTC-4 in September) is 14:00 UTC.
        self.assertEqual((stored.hour, stored.minute), (14, 0))

    def test_no_header_is_utc_as_before(self):
        res = self.create()
        self.assertEqual(res.status_code, 201, res.content)
        stored = Event.objects.get().start_date
        self.assertEqual((stored.hour, stored.minute), (10, 0))

    def test_a_zone_that_is_not_a_zone_is_ignored_not_a_crash(self):
        res = self.create(HTTP_X_CLIENT_TIMEZONE='Mars/Olympus')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Event.objects.get().start_date.hour, 10)

    def test_a_programme_session_lands_where_it_was_typed(self):
        """The row that showed the fault: 10:30 typed, 11:30 shown."""
        self.create(HTTP_X_CLIENT_TIMEZONE='Africa/Lagos')
        event = Event.objects.get()
        day = event.start_date.strftime('%Y-%m-%d')
        res = self.client.post('/event/%s/sessions/manage/' % event.slug, data={
            'title': 'Opening ceremony', 'starts_at': '%sT10:30' % day,
        }, content_type='application/json', **self.auth,
            HTTP_X_CLIENT_TIMEZONE='Africa/Lagos')
        self.assertEqual(res.status_code, 201, res.content)
        stored = EventSession.objects.get().starts_at
        self.assertEqual((stored.hour, stored.minute), (9, 30))

    def test_the_zone_does_not_leak_into_the_next_request(self):
        self.create(HTTP_X_CLIENT_TIMEZONE='Africa/Lagos')
        Event.objects.all().delete()
        self.create()
        self.assertEqual(Event.objects.get().start_date.hour, 10)
