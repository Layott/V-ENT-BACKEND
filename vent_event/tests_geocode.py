"""An address typed by an organiser becomes a pin on a map.

CEO, 8 September 2026: "what I want is that when an event organizer puts an
address it should be located on the map and shown. that flow of it showing on
map to all users should be fixed."

Until now the map appeared only when somebody pasted a Google Maps LINK, so an
organiser who typed "Landmark Centre, Victoria Island, Lagos" - which is what
most people do - got "There is no map of this venue."

Nothing here touches the network. The geocoder is stubbed, because a test that
depends on OpenStreetMap being awake fails for reasons that have nothing to do
with this code.

The two decisions worth pinning are both about being WRONG rather than absent:

- A result must corroborate the distinctive words asked for. "Eko Convention
  Centre" came back as "Calabar International Convention Centre" - 600km away,
  matching only the generic words - and would have put a confident wrong pin on
  a real event.
- The search is bounded to the countries V-ENT runs in, because corroboration
  alone let a bare "Landmark" match a feature in Nunavut at 70.9, -111.2.
"""
from datetime import time, timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, Users

from . import geo
from .models import Event, GeocodedAddress


class LadderTests(TestCase):
    def test_the_forms_go_from_most_specific_to_least(self):
        ladder = geo.attempts_for('Landmark Centre', 'Victoria Island, Lagos')
        self.assertEqual(ladder[0], 'Landmark Centre, Victoria Island, Lagos')
        # The one that actually answers. Nominatim dislikes the commas.
        self.assertIn('Landmark Centre Lagos', ladder)
        self.assertEqual(ladder[-1], 'Lagos')

    def test_nothing_is_asked_twice(self):
        ladder = geo.attempts_for('Lagos', 'Lagos')
        self.assertEqual(len(ladder), len(set(x.lower() for x in ladder)))

    def test_a_venue_with_no_address_still_has_something_to_try(self):
        self.assertEqual(geo.attempts_for('The Dome', ''), ['The Dome'])


class CorroborationTests(TestCase):
    def test_a_generic_word_match_is_not_a_match(self):
        """The real one. "Eko Convention Centre" resolved to "Calabar
        International Convention Centre, Lagos-Calabar Coast", which is Cross
        River, and every word they share is a word half the venues in Nigeria
        share."""
        self.assertFalse(geo.corroborates(
            'Eko Convention Centre',
            'Calabar International Convention Centre, Lagos-Calabar Coast'))

    def test_a_distinctive_word_match_is(self):
        self.assertTrue(geo.corroborates(
            'Landmark Centre Lagos',
            'Landmark Centre, 3-4, Maroko, Lagos, Eti Osa, Lagos, Nigeria'))

    def test_a_query_with_nothing_distinctive_corroborates_nothing(self):
        self.assertFalse(geo.corroborates('The Hotel', 'Some Hotel, Anywhere'))


class BoundingTests(TestCase):
    def test_the_search_is_bounded_to_where_v_ent_runs_events(self):
        """Corroboration alone let a bare "Landmark" match a feature in
        Nunavut, at 70.9, -111.2. It even corroborated, because the word was
        genuinely in the answer."""
        self.assertIn('ng', geo.country_hint('Landmark Centre'))

    def test_naming_another_country_lifts_the_bound(self):
        self.assertEqual(geo.country_hint('Alexanderplatz, Berlin, Germany'), '')


# Geocoding is OFF under the test runner, because `Event.save()` calls it and
# every test that creates an event with an address would otherwise reach
# OpenStreetMap. These are the tests that WANT it, and every one of them stubs
# the network, so they turn it back on explicitly.
@override_settings(GEOCODING_ENABLED=True)
class CacheTests(TestCase):
    def test_an_address_is_looked_up_once(self):
        calls = []

        def fake(address):
            calls.append(address)
            return Decimal('6.4231205'), Decimal('3.4453480')

        with mock.patch.object(geo, '_GEOCODERS', (fake,)):
            first = geo.geocode('Landmark Centre', 'Victoria Island, Lagos')
            second = geo.geocode('Landmark Centre', 'Victoria Island, Lagos')

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1, 'asked twice for the same address')

    def test_a_miss_is_cached_too(self):
        """An address nobody can find will not become findable on the next
        save, and re-asking every edit is how a free service withdraws."""
        calls = []

        def never(address):
            calls.append(address)
            return None

        with mock.patch.object(geo, '_GEOCODERS', (never,)):
            self.assertIsNone(geo.geocode('', 'Nowhere At All Really'))
            before = len(calls)
            self.assertIsNone(geo.geocode('', 'Nowhere At All Really'))

        self.assertEqual(len(calls), before, 'asked again after a miss')
        self.assertEqual(GeocodedAddress.objects.count(), 1)

    def test_which_form_answered_is_recorded(self):
        with mock.patch.object(geo, '_GEOCODERS',
                               (lambda a: (Decimal('6.4'), Decimal('3.4'))
                                if a == 'Landmark Centre Lagos' else None,)):
            geo.geocode('Landmark Centre', 'Victoria Island, Lagos')
        row = GeocodedAddress.objects.get()
        self.assertEqual(row.matched, 'Landmark Centre Lagos')

    def test_something_too_short_is_never_asked(self):
        with mock.patch.object(geo, '_GEOCODERS',
                               (lambda a: 1 / 0,)):   # would raise if called
            self.assertIsNone(geo.geocode('', 'Ah'))


@override_settings(GEOCODING_ENABLED=True)
class SavingAnEventTests(TestCase):
    def setUp(self):
        self.user = Users.objects.create(username='geo_org',
                                         email='geo@vent.test', is_active=True)
        self.game = Games.objects.create(game_title='EA FC GEO')

    def an_event(self, **kwargs):
        now = timezone.localtime(timezone.now())
        fields = dict(
            name='Geo Probe', game=self.game, creator=self.user,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timedelta(days=2),
            event_date=now.date(), start_time=time(18, 0), end_time=time(22, 0))
        fields.update(kwargs)
        return Event.objects.create(**fields)

    def test_an_address_becomes_a_pin(self):
        with mock.patch.object(geo, '_GEOCODERS',
                               (lambda a: (Decimal('6.4231205'),
                                           Decimal('3.4453480')),)):
            event = self.an_event(venue_name='Landmark Centre',
                                  location='Victoria Island, Lagos')
        # Reloaded, because the column stores six decimal places and the
        # in-memory object is still holding whatever the geocoder returned.
        # What a reader sees is what came back out of the database.
        event.refresh_from_db()
        self.assertEqual(event.latitude, Decimal('6.423120'))
        self.assertEqual(event.longitude, Decimal('3.445348'))

    def test_a_coordinate_somebody_set_is_never_overwritten(self):
        with mock.patch.object(geo, '_GEOCODERS',
                               (lambda a: (Decimal('1.0'), Decimal('2.0')),)):
            event = self.an_event(venue_name='Landmark Centre',
                                  location='Victoria Island, Lagos',
                                  latitude=Decimal('9.1'),
                                  longitude=Decimal('8.2'))
        event.refresh_from_db()
        self.assertEqual(event.latitude, Decimal('9.100000'))

    def test_a_geocoder_that_is_down_never_stops_an_event_saving(self):
        """An event must save whether or not a third party is up."""
        def explode(address):
            raise OSError('nominatim is down')

        with mock.patch.object(geo, '_GEOCODERS', (explode,)):
            event = self.an_event(venue_name='Landmark Centre',
                                  location='Victoria Island, Lagos')
        self.assertIsNotNone(event.pk)
        self.assertIsNone(event.latitude)

    def test_an_online_event_with_no_address_is_not_looked_up(self):
        with mock.patch.object(geo, '_GEOCODERS', (lambda a: 1 / 0,)):
            event = self.an_event(event_type='virtual', location='')
        self.assertIsNone(event.latitude)
