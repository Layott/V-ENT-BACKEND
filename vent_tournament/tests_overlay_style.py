"""The overlay style every designed overlay follows (inbox 393).

CEO, 30 September 2026: "an overlay design template that will apply to all
overlays, like primary fonts, secondary fonts, primary colors, secondary
colors". Stored on the broadcast, read by every browser source through the
feed, and carried into the next broadcast with each overlay's own settings.
"""
from django.test import SimpleTestCase, TestCase

from vent_auth import premium

from . import overlay_style
from .models import BroadcastElement
from .tests_studio import StudioTests

STYLE = {'primary': '#1e6bff', 'secondary': '#0A2A6B', 'text': '#FFFFFF',
         'font_primary': 'astronum', 'font_secondary': 'pixel', 'logo': '12', 'logo_secondary': 'none'}


class Clean(SimpleTestCase):
    def test_accepts_every_role(self):
        out = overlay_style.clean(STYLE)
        self.assertEqual(out['primary'], '#1E6BFF')
        self.assertEqual(out['font_primary'], 'astronum')
        self.assertEqual(set(out), set(overlay_style.ROLES))

    def test_empty_means_follow_the_design(self):
        self.assertEqual(overlay_style.clean({'primary': '', 'logo': None}), {})

    def test_refuses_what_is_not_a_style(self):
        for raw, field in [({'primary': 'red'}, 'primary'), ({'primary': '#12345'}, 'primary'),
                           ({'font_primary': 'url(x)'}, 'font_primary'), ({'logo': '../x.png'}, 'logo'),
                           ({'script': 'x'}, 'script'), ('style', 'style')]:
            with self.subTest(raw=raw):
                with self.assertRaises(overlay_style.StyleError) as caught:
                    overlay_style.clean(raw)
                self.assertEqual(caught.exception.field, field)


class Served(TestCase):
    setUp = StudioTests.setUp
    start = StudioTests.start
    element = StudioTests.element

    def detail(self, session_id, body, auth=None):
        return self.client.post('/tournament/%s/studio/sessions/%s/' % (self.ref, session_id),
                                data=body, content_type='application/json',
                                **(auth if auth is not None else self.auth))

    def test_saved_style_reaches_the_feed_and_moves_the_version(self):
        s = self.start().json()['data']['session']
        before = self.client.get('/studio/%s/feed/' % s['token']).json()['data']['version']
        res = self.detail(s['id'], {'style': STYLE})
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['session']['style']['primary'], '#1E6BFF')
        feed = self.client.get('/studio/%s/feed/' % s['token']).json()['data']
        self.assertEqual(feed['session']['style']['font_primary'], 'astronum')
        self.assertNotEqual(feed['version'], before)

    def test_a_bad_style_is_refused_whole(self):
        s = self.start().json()['data']['session']
        self.detail(s['id'], {'style': STYLE})
        res = self.detail(s['id'], {'style': {'primary': '#000000', 'secondary': 'blue'}})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_STYLE')
        self.assertEqual(res.json()['field'], 'secondary')
        feed = self.client.get('/studio/%s/feed/' % s['token']).json()['data']
        self.assertEqual(feed['session']['style']['primary'], '#1E6BFF')

    def test_a_stranger_cannot_set_the_style(self):
        s = self.start().json()['data']['session']
        self.assertEqual(self.detail(s['id'], {'style': STYLE}, auth=self.other_auth).status_code, 403)

    def test_the_next_broadcast_keeps_the_style_and_the_designs(self):
        first = self.start('Day 1').json()['data']['session']
        self.detail(first['id'], {'style': STYLE})
        self.element(first['id'], 'starting_soon', {'active': True, 'payload': {'design': {'line1': 'DAY 2 IS'}}})
        self.element(first['id'], 'scorebar', {'active': True, 'payload': {'home': 'Nigeria'}})
        second = self.start('Day 2').json()['data']['session']
        self.assertEqual(second['style']['primary'], '#1E6BFF')
        rows = {e.kind: e for e in BroadcastElement.objects.filter(session_id=second['id'])}
        self.assertEqual(rows['starting_soon'].payload, {'design': {'line1': 'DAY 2 IS'}})
        # Nothing comes across on air, and nothing that is not a design.
        self.assertFalse(rows['starting_soon'].is_active)
        self.assertNotIn('scorebar', rows)


class PremiumDownloads(TestCase):
    """Inbox 391 and 397: downloading overlays is a premium feature."""
    setUp = StudioTests.setUp
    start = StudioTests.start

    def test_the_session_says_whether_the_owner_may_download(self):
        self.assertIn('overlay_downloads', premium.FEATURES)
        s = self.start().json()['data']['session']
        self.assertFalse(s['may_download_overlays'])
        self.organiser.is_premium = True
        self.organiser.save(update_fields=['is_premium'])
        s = self.start('Day 2').json()['data']['session']
        self.assertTrue(s['may_download_overlays'])
