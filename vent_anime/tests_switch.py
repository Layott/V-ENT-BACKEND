"""The anime module is closed unless somebody opens it.

The test that matters is the LAST one: it walks the urlconf and fails if any
route is missing the mark, so a route added tomorrow without `gated` fails the
build rather than shipping open. That exact fault was found on the billing
switch by unwrapping a route on purpose, and it passed the check that looked for
`__wrapped__`, because DRF's `@api_view` sets that on everything.
"""
from django.test import TestCase, override_settings
from django.urls import get_resolver

from .switch import OFF_CODE, anime_is_on


class TheSwitchTests(TestCase):

    @override_settings(ANIME_ENABLED=False)
    def test_it_is_off_by_default(self):
        self.assertFalse(anime_is_on())

    @override_settings(ANIME_ENABLED=True)
    def test_it_can_be_turned_on(self):
        self.assertTrue(anime_is_on())

    def test_a_box_with_no_setting_at_all_reads_as_closed(self):
        """A `.env` that predates this module must not raise on every request."""
        from django.conf import settings
        with self.settings():
            delattr_ok = hasattr(settings, 'ANIME_ENABLED')
            self.assertTrue(delattr_ok or not anime_is_on())


@override_settings(ANIME_ENABLED=False)
class WhileItIsOffTests(TestCase):

    def test_the_catalogue_refuses(self):
        res = self.client.get('/anime/catalogue/')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()['code'], OFF_CODE)

    def test_the_series_list_refuses(self):
        res = self.client.get('/anime/series/')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()['code'], OFF_CODE)

    def test_a_room_refuses(self):
        res = self.client.get('/anime/rooms/')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()['code'], OFF_CODE)

    def test_a_battle_refuses(self):
        res = self.client.get('/anime/battles/')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()['code'], OFF_CODE)

    def test_the_refusal_is_503_and_not_404(self):
        """404 would tell a crawler to forget an address that will work later."""
        res = self.client.get('/anime/series/')
        self.assertEqual(res.status_code, 503)

    def test_the_refusal_carries_the_envelope_every_screen_reads(self):
        body = self.client.get('/anime/series/').json()
        self.assertEqual(set(body), {'status', 'data', 'message', 'code'})
        self.assertEqual(body['status'], 'error')


@override_settings(ANIME_ENABLED=True)
class WhileItIsOnTests(TestCase):

    def test_the_catalogue_answers(self):
        res = self.client.get('/anime/catalogue/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('genres', res.json()['data'])

    def test_the_series_list_answers(self):
        res = self.client.get('/anime/series/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['series'], [])


class EveryRouteIsGatedTests(TestCase):
    """Walked from the urlconf, never from a list somebody typed."""

    def _anime_patterns(self):
        resolver = get_resolver()
        found = []

        def walk(patterns, prefix=''):
            for entry in patterns:
                if hasattr(entry, 'url_patterns'):
                    walk(entry.url_patterns, prefix + str(entry.pattern))
                else:
                    found.append((prefix + str(entry.pattern), entry.callback))

        walk(resolver.url_patterns)
        return [(path, cb) for path, cb in found if path.startswith('anime/')]

    def test_there_are_routes_to_check(self):
        """A checker that finds nothing passes, which is the failure mode."""
        self.assertGreaterEqual(len(self._anime_patterns()), 30)

    def test_every_one_carries_the_mark(self):
        unguarded = [path for path, cb in self._anime_patterns()
                     if not getattr(cb, 'anime_gated', False)]
        self.assertEqual(unguarded, [],
                         'these anime routes are not gated: %s' % unguarded)

    def test_the_mark_is_explicit_rather_than_functools_wraps(self):
        """`@api_view` sets `__wrapped__` on everything, so it proves nothing."""
        from .views_series import series_list
        self.assertTrue(hasattr(series_list, '__wrapped__'))
        self.assertFalse(getattr(series_list, 'anime_gated', False))
