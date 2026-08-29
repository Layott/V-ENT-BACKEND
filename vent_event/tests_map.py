"""The map on an event, and the promise it makes about privacy.

CEO, 29 August 2026: markers showing that people nearby are going, "they dont
need to see specific people", and going public is the attendee's choice.

That is a feature whose failure mode is not a broken page. A map of who is
coming to a public event, drawn from real coordinates, is a map of where those
people live, and every one of these tests exists because one line of code could
turn it into that:

- the exact point must never be stored, so the test hands in a precise
  coordinate and then reads every field on the row;
- a cell must not be shown until enough people share it, so the test adds people
  one at a time and watches when the marker appears;
- nothing is shared without being asked for, so the default is checked
  explicitly rather than assumed.
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Users
from vent_event.geo import CELL, MIN_PER_CELL, point_from_map_link, to_cell
from vent_event.models import Event, EventAttendeeOrigin, Ticket, TicketTier


def is_cell_centre(value):
    """A cell centre is CELL/2 away from a whole number of cells.

    Not `value % CELL == CELL / 2`: Decimal's remainder takes the sign of the
    dividend, so that reads as false for everywhere south of the equator and
    west of Greenwich, which is most of the world and half of this platform's
    users.
    """
    return ((Decimal(value) - CELL / 2) / CELL) == (
        (Decimal(value) - CELL / 2) / CELL).to_integral_value()


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, full_name=name.title(),
        login_session_token=('tk-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user


class CellTests(TestCase):
    """The rounding itself, because everything else depends on it."""

    def test_a_point_becomes_the_centre_of_its_cell(self):
        lat, lng = to_cell('6.6018', '3.3515')
        # Lagos. The cell centre, not the point.
        self.assertNotEqual(lat, Decimal('6.6018'))
        self.assertNotEqual(lng, Decimal('3.3515'))
        self.assertTrue(is_cell_centre(lat), lat)
        self.assertTrue(is_cell_centre(lng), lng)

    def test_two_points_in_the_same_district_land_in_the_same_cell(self):
        a = to_cell('6.6018', '3.3515')
        b = to_cell('6.6100', '3.3600')
        self.assertEqual(a, b)

    def test_points_far_apart_do_not(self):
        self.assertNotEqual(to_cell('6.60', '3.35'), to_cell('9.05', '7.49'))

    def test_the_southern_and_western_hemispheres_round_the_same_way(self):
        lat, lng = to_cell('-33.9249', '18.4241')
        self.assertTrue(is_cell_centre(lat), lat)
        self.assertTrue(is_cell_centre(lng), lng)
        # Cape Town, and the cell it lands in contains it.
        self.assertLess(abs(lat - Decimal('-33.9249')), CELL)

    def test_nonsense_is_refused(self):
        for bad in [('abc', '3.3'), ('91', '0'), ('0', '181'), (None, None)]:
            with self.assertRaises(Exception):
                to_cell(*bad)


class MapLinkTests(TestCase):
    def test_a_google_maps_link_gives_up_its_coordinate(self):
        point = point_from_map_link(
            'https://www.google.com/maps/place/Celebr8/@6.6018,3.3515,17z')
        self.assertIsNotNone(point)
        self.assertEqual(point[0], Decimal('6.6018'))

    def test_a_query_link_works_too(self):
        point = point_from_map_link('https://maps.google.com/?q=6.6018,3.3515')
        self.assertIsNotNone(point)

    def test_a_link_with_no_coordinate_gives_nothing_rather_than_a_guess(self):
        self.assertIsNone(point_from_map_link('https://maps.app.goo.gl/abcd1234'))
        self.assertIsNone(point_from_map_link(''))
        self.assertIsNone(point_from_map_link(None))


class EventCoordinateTests(TestCase):
    def setUp(self):
        self.game = Games.objects.get_or_create(game_title='Map Probe')[0]
        self.owner = a_user('mapowner')

    def _event(self, **extra):
        return Event.objects.create(
            name=extra.pop('name', 'Map Probe Event'), game=self.game,
            creator=self.owner, event_type='physical', desc='probe',
            entry_fee=0, reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=5),
            event_date=(timezone.now() + timezone.timedelta(days=6)).date(),
            start_time='10:00', end_time='18:00',
            location='Celebr8 Centre, Lagos', **extra)

    def test_a_pasted_map_link_fills_the_coordinate(self):
        event = self._event(map_link='https://www.google.com/maps/@6.6018,3.3515,17z')
        event.refresh_from_db()
        self.assertEqual(event.latitude, Decimal('6.601800'))
        self.assertEqual(event.longitude, Decimal('3.351500'))

    def test_a_coordinate_somebody_set_is_never_overwritten_by_a_link(self):
        event = self._event(latitude=Decimal('9.050000'), longitude=Decimal('7.490000'))
        event.map_link = 'https://www.google.com/maps/@6.6018,3.3515,17z'
        event.save()
        event.refresh_from_db()
        self.assertEqual(event.latitude, Decimal('9.050000'))

    def test_an_event_with_no_link_has_no_coordinate_rather_than_a_default(self):
        event = self._event()
        self.assertIsNone(event.latitude)
        self.assertIsNone(event.longitude)


class OriginPrivacyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.game = Games.objects.get_or_create(game_title='Origin Probe')[0]
        self.owner = a_user('originowner')
        self.event = Event.objects.create(
            name='Origin Probe Event', game=self.game, creator=self.owner,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=5),
            event_date=(timezone.now() + timezone.timedelta(days=6)).date(),
            start_time='10:00', end_time='18:00', location='Lagos')
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=100)

    def _attendee(self, name):
        user = a_user(name)
        Ticket.objects.create(
            event=self.event, tier=self.tier, user=user,
            code='VT-%s' % name.upper()[:8], status='valid')
        return user

    def _auth(self, user):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)

    def _share(self, user, lat='6.6018', lng='3.3515'):
        self._auth(user)
        return self.client.post(
            '/event/%s/origins/' % self.event.slug,
            {'latitude': lat, 'longitude': lng}, format='json')

    # ---------------------------------------------------------------- consent

    def test_nothing_is_shared_until_somebody_asks(self):
        self._attendee('quiet')
        res = self.client.get('/event/%s/origins/' % self.event.slug)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['data']['cells'], [])
        self.assertFalse(res.data['data']['sharing'])

    def test_sharing_can_be_stopped_in_one_request(self):
        user = self._attendee('leaver')
        self._share(user)
        self.assertTrue(EventAttendeeOrigin.objects.filter(user=user).exists())
        res = self.client.delete('/event/%s/origins/' % self.event.slug)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(EventAttendeeOrigin.objects.filter(user=user).exists())
        self.assertFalse(res.data['data']['sharing'])

    def test_somebody_without_a_ticket_cannot_be_counted(self):
        stranger = a_user('nostranger')
        self._auth(stranger)
        res = self.client.post(
            '/event/%s/origins/' % self.event.slug,
            {'latitude': '6.6', 'longitude': '3.3'}, format='json')
        self.assertEqual(res.status_code, 403, res.data)
        self.assertFalse(EventAttendeeOrigin.objects.exists())

    def test_signed_out_cannot_add_anybody(self):
        self.client.credentials()
        res = self.client.post(
            '/event/%s/origins/' % self.event.slug,
            {'latitude': '6.6', 'longitude': '3.3'}, format='json')
        self.assertEqual(res.status_code, 401, res.data)

    # ---------------------------------------------------------------- storage

    def test_the_exact_point_is_never_stored(self):
        user = self._attendee('precise')
        self._share(user, lat='6.601837', lng='3.351592')
        row = EventAttendeeOrigin.objects.get(user=user)
        stored = {f.name: getattr(row, f.name) for f in row._meta.fields}
        for value in stored.values():
            self.assertNotIn('6.601837', str(value))
            self.assertNotIn('3.351592', str(value))
        # And what is stored is a cell centre.
        self.assertTrue(is_cell_centre(row.cell_latitude), row.cell_latitude)

    def test_sharing_twice_moves_the_cell_rather_than_making_a_second_row(self):
        user = self._attendee('mover')
        self._share(user, lat='6.6018', lng='3.3515')
        self._share(user, lat='9.0500', lng='7.4900')
        self.assertEqual(EventAttendeeOrigin.objects.filter(user=user).count(), 1)

    def test_a_bad_coordinate_is_refused_and_stores_nothing(self):
        user = self._attendee('badcoord')
        self._auth(user)
        res = self.client.post(
            '/event/%s/origins/' % self.event.slug,
            {'latitude': 'here', 'longitude': 'there'}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertFalse(EventAttendeeOrigin.objects.exists())

    # ------------------------------------------------------------- anonymity

    def test_one_person_is_not_a_marker(self):
        self._share(self._attendee('alone'))
        res = self.client.get('/event/%s/origins/' % self.event.slug)
        self.assertEqual(res.data['data']['cells'], [],
                         'a single person was drawn on the map')

    def test_a_cell_appears_only_once_enough_people_share_it(self):
        for n in range(MIN_PER_CELL - 1):
            self._share(self._attendee('near%d' % n))
        self.client.credentials()
        self.assertEqual(
            self.client.get('/event/%s/origins/' % self.event.slug).data['data']['cells'],
            [], 'the marker appeared below the threshold')

        self._share(self._attendee('nearlast'))
        self.client.credentials()
        cells = self.client.get(
            '/event/%s/origins/' % self.event.slug).data['data']['cells']
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]['people'], MIN_PER_CELL)

    def test_the_response_carries_no_person(self):
        for n in range(MIN_PER_CELL):
            self._share(self._attendee('p%d' % n))
        self.client.credentials()
        res = self.client.get('/event/%s/origins/' % self.event.slug)
        body = str(res.data)
        for n in range(MIN_PER_CELL):
            self.assertNotIn('p%d' % n, body)
        for word in ['username', 'email', 'user_id', 'full_name']:
            self.assertNotIn(word, body)

    def test_a_far_away_group_below_the_threshold_stays_hidden(self):
        for n in range(MIN_PER_CELL):
            self._share(self._attendee('city%d' % n))
        self._share(self._attendee('remote'), lat='9.0500', lng='7.4900')
        self.client.credentials()
        cells = self.client.get(
            '/event/%s/origins/' % self.event.slug).data['data']['cells']
        self.assertEqual(len(cells), 1, 'the lone remote attendee was drawn')

    def test_the_viewer_is_told_whether_they_are_sharing(self):
        user = self._attendee('selfcheck')
        self._share(user)
        self._auth(user)
        res = self.client.get('/event/%s/origins/' % self.event.slug)
        self.assertTrue(res.data['data']['sharing'])
