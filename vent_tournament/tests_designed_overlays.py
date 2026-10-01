"""Designed overlays: Starting soon and the transition (inbox 390).

CEO, 30 September 2026: generic overlays for the production studio, editable
(text, colours, the organiser's logo, what shows where), animated and still,
and the transition in any colour. The drawing is in the browser; the server
holds the kinds, the settings (`payload.design`) and the replay counter
(`payload.play`), and hands them to every browser source in the one feed.
"""
from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_event.models import Event

from .models import BroadcastElement
from .tests_studio import StudioTests, a_user

DESIGN = {
    'line1': 'MATCHDAY IS', 'line2': 'LOADING', 'note': '', 'countdown_to': '2026-10-01T17:00:00Z',
    'bg_from': '#0A2A6B', 'bg_to': '#1E6BFF', 'text_colour': '#FFFFFF', 'grid': False,
    'layout': 'center', 'logo': '17', 'font': 'pixel', 'animated': True,
}


class DesignedKinds(TestCase):
    def test_both_kinds_on_tournament_and_event_studios(self):
        for owner in ('tournament', 'event'):
            kinds = [k for k, _ in BroadcastElement.kinds_for(owner)]
            with self.subTest(owner=owner):
                self.assertIn('starting_soon', kinds)
                self.assertIn('transition', kinds)
        self.assertLessEqual(max(len(k) for k, _ in BroadcastElement.KINDS), 20)

    def test_every_designed_kind_is_on_both_studios(self):
        """Inbox 394 and 396: the whole GENERAL OVERLAYS set and the asset library, and a design carried into
        the next broadcast must be one either kind of broadcast can show."""
        self.assertEqual(len(BroadcastElement.DESIGNED_KINDS), 16)
        for owner in ('tournament', 'event'):
            kinds = [k for k, _ in BroadcastElement.kinds_for(owner)]
            for kind in BroadcastElement.DESIGNED_KINDS:
                with self.subTest(owner=owner, kind=kind):
                    self.assertIn(kind, kinds)


class TournamentDesigned(TestCase):
    """On a tournament broadcast, with the studio tests' own set-up and calls."""
    setUp = StudioTests.setUp
    start = StudioTests.start
    element = StudioTests.element

    def test_design_is_saved_whole_and_reaches_the_feed(self):
        s = self.start().json()['data']['session']
        self.assertIn('starting_soon', s['urls'])
        res = self.element(s['id'], 'starting_soon', {'active': True, 'payload': {'design': DESIGN}})
        self.assertEqual(res.status_code, 200, res.content[:300])
        feed = self.client.get('/studio/%s/feed/' % s['token']).json()['data']
        el = feed['elements']['starting_soon']
        self.assertTrue(el['active'])
        self.assertEqual(el['payload']['design'], DESIGN)

    def test_replaying_a_transition_moves_the_version(self):
        s = self.start().json()['data']['session']
        self.element(s['id'], 'transition', {'active': True, 'payload': {'design': {'colour_a': '#FFD602'}}})
        before = self.client.get('/studio/%s/feed/' % s['token']).json()['data']['version']
        self.element(s['id'], 'transition', {'payload': {'play': 1}})
        after = self.client.get('/studio/%s/feed/' % s['token']).json()['data']
        self.assertNotEqual(before, after['version'])
        # The design is kept when only the replay counter is sent.
        self.assertEqual(after['elements']['transition']['payload']['design'], {'colour_a': '#FFD602'})
        self.assertEqual(after['elements']['transition']['payload']['play'], 1)

    def test_a_stranger_cannot_change_a_design(self):
        s = self.start().json()['data']['session']
        res = self.client.post(
            '/tournament/%s/studio/sessions/%s/element/starting_soon/' % (self.ref, s['id']),
            data={'payload': {'design': DESIGN}}, content_type='application/json', **self.other_auth)
        self.assertEqual(res.status_code, 403)


class EventDesigned(TestCase):
    def test_an_event_broadcast_gets_both(self):
        organiser, auth = a_user('dovA')
        now = timezone.now()
        event = Event.objects.create(
            name='Lagos Anime Con', creator=organiser, event_type='physical', desc='x',
            entry_fee=Decimal('0'), reg_start_date=now, reg_end_date=now,
            event_date=now.date(), start_time=now.time(), end_time=now.time(),
            start_date=now - timedelta(hours=1), end_date=now + timedelta(hours=8),
            venue_name='Landmark Centre')
        ref = event.slug or event.event_id
        s = self.client.post('/event/%s/studio/sessions/' % ref, data={'name': 'Day 1'},
                             content_type='application/json', **auth).json()['data']['session']
        self.assertIn('starting_soon', s['urls'])
        self.assertIn('transition', s['urls'])
        res = self.client.post('/event/%s/studio/sessions/%s/element/transition/' % (ref, s['id']),
                               data={'payload': {'design': {'bands': '7'}}},
                               content_type='application/json', **auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
