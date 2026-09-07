"""Where somebody is, on the leaderboard.

CEO, 7 September 2026: "the wrong locations is showing for users, i am seeing
united states everywhere for region, even under the filter for all countries, i
am seeing lagos, abuja and then other countries not there."

Three faults in one screen, and each has a test here:

1. The region column was the user's STATE (`u.state or u.country`), so somebody
   in Lagos had the region "Lagos".
2. The region filter was read off the query string and never used, so picking
   West Africa returned the whole world.
3. The country list was seven entries typed into the frontend, two of which
   were Nigerian cities.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from . import regions
from .models import Users


class RegionForACountryTests(TestCase):
    def test_a_country_maps_to_its_region(self):
        self.assertEqual(regions.region_for('Nigeria'), 'West Africa')
        self.assertEqual(regions.region_for('Kenya'), 'East Africa')
        self.assertEqual(regions.region_for('South Africa'), 'Southern Africa')
        self.assertEqual(regions.region_for('United States'), 'North America')

    def test_case_and_spacing_do_not_matter(self):
        self.assertEqual(regions.region_for('  nigeria '), 'West Africa')

    def test_a_city_is_not_a_country_and_gets_no_region(self):
        """The whole reported fault. Lagos and Abuja were offered AS countries,
        and a city must not resolve to a region or the filter silently includes
        the wrong people."""
        self.assertEqual(regions.region_for('Lagos'), '')
        self.assertEqual(regions.region_for('Abuja'), '')
        self.assertFalse(regions.is_country('Lagos'))
        self.assertFalse(regions.is_country('Abuja'))

    def test_something_unknown_is_blank_rather_than_guessed(self):
        """Putting somebody in the wrong region is worse than leaving it
        blank: a filter that quietly includes the wrong people is not
        discoverable, and a blank is."""
        self.assertEqual(regions.region_for('Atlantis'), '')
        self.assertEqual(regions.region_for(None), '')
        self.assertEqual(regions.region_for(''), '')

    def test_west_africa_is_complete_enough_to_be_useful(self):
        """Africa-first is not a slogan here: an organiser filtering to West
        Africa expects Benin and Togo to be in it."""
        west = regions.countries_in('West Africa')
        for country in ('Nigeria', 'Ghana', 'Senegal', 'Benin', 'Togo', 'Mali'):
            self.assertIn(country, west)

    def test_an_unknown_region_asks_for_nothing_rather_than_everything(self):
        self.assertEqual(regions.countries_in('Narnia'), [])
        self.assertEqual(regions.countries_in(''), [])


class TheLeaderboardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        for name, country, state in (
                ('rk_ng1', 'Nigeria', 'Lagos'),
                ('rk_ng2', 'Nigeria', 'Abuja'),
                ('rk_ke1', 'Kenya', 'Nairobi'),
                ('rk_us1', 'United States', 'Texas'),
                ('rk_none', '', '')):
            u = Users.objects.create(username=name, email='%s@vent.test' % name,
                                     country=country, state=state, is_active=True)
            u.save()

    def rows(self, query=''):
        res = self.client.get('/ranking/%s' % query)
        self.assertEqual(res.status_code, 200, res.content[:200])
        return res.json()['data']

    def test_the_region_is_the_region_not_the_state(self):
        by_name = {r['name']: r for r in self.rows()['players']}
        self.assertEqual(by_name['rk_ng1']['region'], 'West Africa')
        # NOT "Lagos", which is what it said before.
        self.assertNotEqual(by_name['rk_ng1']['region'], 'Lagos')
        self.assertEqual(by_name['rk_ke1']['region'], 'East Africa')
        self.assertEqual(by_name['rk_us1']['region'], 'North America')

    def test_somebody_with_no_country_has_no_region_rather_than_a_wrong_one(self):
        by_name = {r['name']: r for r in self.rows()['players']}
        self.assertEqual(by_name['rk_none']['region'], '')

    def test_the_region_filter_actually_filters(self):
        """It was read off the query string and never used in a single query,
        so picking West Africa returned the whole world."""
        west = [r['name'] for r in self.rows('?region=West%20Africa')['players']]
        self.assertIn('rk_ng1', west)
        self.assertIn('rk_ng2', west)
        self.assertNotIn('rk_ke1', west)
        self.assertNotIn('rk_us1', west)

    def test_a_region_nobody_is_in_returns_nobody(self):
        self.assertEqual(self.rows('?region=Oceania')['players'], [])

    def test_global_returns_everybody(self):
        self.assertEqual(len(self.rows('?region=global')['players']), 5)

    def test_a_country_beats_a_region_when_both_are_named(self):
        """Naming both means the country wins, which is what somebody who
        picked one expects."""
        rows = self.rows('?region=East%20Africa&country=Nigeria')['players']
        self.assertEqual({r['name'] for r in rows}, {'rk_ng1', 'rk_ng2'})

    def test_the_filter_list_comes_from_the_data_and_holds_no_cities(self):
        filters = self.rows()['filters']
        self.assertIn('Nigeria', filters['countries'])
        self.assertIn('Kenya', filters['countries'])
        self.assertNotIn('Lagos', filters['countries'])
        self.assertNotIn('Abuja', filters['countries'])

    def test_only_regions_that_have_somebody_are_offered(self):
        """An empty filter result reads as a broken page."""
        offered = self.rows()['filters']['regions']
        self.assertIn('West Africa', offered)
        self.assertIn('East Africa', offered)
        self.assertNotIn('Oceania', offered)

    def test_the_regions_are_offered_africa_first(self):
        offered = self.rows()['filters']['regions']
        self.assertLess(offered.index('West Africa'),
                        offered.index('North America'))
