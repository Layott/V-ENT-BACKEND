"""Getting to the venue.

`location` is one line of text somebody typed. It is enough to print on a
ticket and not enough to travel to, and "where is it" is the question an
organiser answers most often in the week before an event.

The decision worth pinning: an organiser's own map link and a search built from
the address are two different things and are not collapsed into one field. A
search for "The Dome, Lagos" can land on the wrong Dome, and a page presenting
that as the venue would be lying.
"""
from datetime import time, timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users

from .models import Event
from .serializers import map_search_url


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=('d-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class DirectionsTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('dir_org')
        self.stranger, self.stranger_auth = a_user('dir_other')
        game = Games.objects.create(game_title='EA FC DIR')
        now = timezone.localtime(timezone.now())
        self.event = Event.objects.create(
            name='Directions Probe', game=game, creator=self.organiser,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timedelta(days=3),
            event_date=(now + timedelta(days=3)).date(),
            start_time=time(18, 0), end_time=time(22, 0),
            location='12 Adeola Odeku Street, Victoria Island, Lagos')

    def edit(self, payload, auth=None):
        return self.client.put(
            '/event/edit-event/%s/' % self.event.event_id, payload,
            content_type='application/json', **(auth or self.auth))

    def detail(self):
        return self.client.get('/event/view-event/%s/' % self.event.slug)

    def test_the_organiser_records_a_venue_a_map_and_notes(self):
        res = self.edit({
            'venue_name': 'Eko Convention Centre',
            'map_link': 'https://maps.app.goo.gl/probe',
            'directions': 'Enter by the Ozumba Mbadiwe gate. Parking is behind '
                          'the hotel and free after 6pm.',
        })
        self.assertEqual(res.status_code, 200, res.data)
        self.event.refresh_from_db()
        self.assertEqual(self.event.venue_name, 'Eko Convention Centre')
        self.assertIn('goo.gl', self.event.map_link)
        self.assertIn('Ozumba', self.event.directions)

    def test_they_reach_the_page(self):
        self.edit({'venue_name': 'Eko Convention Centre',
                   'directions': 'Gate 3.'})
        res = self.detail()
        self.assertEqual(res.status_code, 200)
        data = res.data['data']['event']
        self.assertEqual(data['venue_name'], 'Eko Convention Centre')
        self.assertEqual(data['directions'], 'Gate 3.')

    def test_a_search_is_offered_when_no_pin_was_dropped(self):
        # Everybody gets the address handed to a map rather than nothing.
        url = map_search_url(self.event)
        self.assertIn('google.com/maps/search', url)
        self.assertIn('Adeola', url)

    def test_the_search_and_the_pin_stay_separate(self):
        self.edit({'map_link': 'https://maps.app.goo.gl/exact'})
        data = self.detail().data['data']['event']
        self.assertEqual(data['map_link'], 'https://maps.app.goo.gl/exact')
        self.assertNotEqual(data['map_search_url'], data['map_link'])

    def test_the_venue_name_sharpens_the_search(self):
        # "The Dome" alone is ambiguous in a city with more than one of them.
        before = map_search_url(self.event)
        self.event.venue_name = 'Eko Convention Centre'
        after = map_search_url(self.event)
        self.assertNotEqual(before, after)
        self.assertIn('Eko', after)

    def test_a_virtual_event_gets_no_map(self):
        # There is nowhere to go.
        self.event.event_type = 'virtual'
        self.assertEqual(map_search_url(self.event), '')

    def test_an_event_with_no_address_gets_no_map(self):
        self.event.location = ''
        self.assertEqual(map_search_url(self.event), '')

    def test_the_address_is_escaped(self):
        self.event.location = 'Plot 5 & 6, Lekki'
        self.assertNotIn(' ', map_search_url(self.event))
        self.assertIn('%26', map_search_url(self.event))

    def test_a_stranger_cannot_move_the_venue(self):
        res = self.edit({'venue_name': 'Somewhere Else'},
                        auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.event.refresh_from_db()
        self.assertEqual(self.event.venue_name, '')

    def test_directions_are_empty_rather_than_null_on_a_new_event(self):
        # An empty state a page can render, not a None it has to guard.
        self.assertEqual(self.event.directions, '')
        self.assertEqual(self.event.venue_name, '')
        self.assertEqual(self.event.map_link, '')
