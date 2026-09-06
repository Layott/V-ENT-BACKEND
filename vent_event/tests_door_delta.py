# -*- coding: utf-8 -*-
"""Asking for only what changed, so a door can afford to keep asking.

CEO, 6 September 2026: "it took too long for the listto load on the people
managing the event", and separately that every event page should update itself
while people are still registering.

Those two asks pull against each other. The attendee payload was 648KB and grew
through the day; polling it on a timer would starve the connection the door
needs to admit anybody. The delta is what makes refreshing legal, so these tests
are not a nicety, they are the load-bearing part of row 78.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .models import Event, EventManager, TicketTier, Ticket
from .tests_door_lookup import a_user, _auth


class DeltaFixture(TestCase):
    def setUp(self):
        self.organiser = a_user('dl_organiser')
        self.steward = a_user('dl_steward')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Delta Probe', creator=self.organiser, event_type='physical',
            desc='A list that refreshes.', entry_fee=0,
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(hours=5),
            reg_start_date=now - timedelta(days=2),
            reg_end_date=now + timedelta(hours=4),
        )
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=500)
        EventManager.objects.create(event=self.event, user=self.steward,
                                    role='door')
        self.first = Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-DELTA001', price_vc=0,
            attendee_name='Already Here', attendee_email='here@vent.test')

    def attendees(self, **params):
        query = '&'.join('%s=%s' % (k, v) for k, v in params.items())
        url = '/event/%d/attendees/' % self.event.event_id
        if query:
            url += '?' + query
        return self.client.get(url, **_auth(self.steward))


class DeltaReturnsOnlyWhatMoved(DeltaFixture):

    def test_a_full_load_returns_everything_and_a_stamp(self):
        res = self.attendees()
        data = res.json()['data']
        self.assertEqual(data['returned'], 1)
        self.assertFalse(data['delta'])
        self.assertTrue(data['asked_at'])

    def test_nothing_changed_means_nothing_comes_back(self):
        stamp = self.attendees().json()['data']['asked_at']
        data = self.attendees(since=stamp).json()['data']
        self.assertEqual(data['returned'], 0)
        self.assertTrue(data['delta'])

    def test_a_ticket_bought_after_the_stamp_arrives_in_the_delta(self):
        """The fault from 4 and 5 September, in the refresh rather than search.

        A steward's page loads at 06:53. Somebody buys a ticket at 10:15. Before
        this the page had no way to learn about them at all.
        """
        stamp = self.attendees().json()['data']['asked_at']
        Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-DELTA002', price_vc=0,
            attendee_name='Bought Later', attendee_email='later@vent.test')

        data = self.attendees(since=stamp).json()['data']
        self.assertEqual([r['code'] for r in data['attendees']],
                         ['VT-DELTA002'])

    def test_a_check_in_arrives_in_the_delta(self):
        """The one that `update_fields` would have silently swallowed.

        `updated_at` is `auto_now`, and a save carrying `update_fields` writes
        only the named columns. Every check-in path saves that way, so without
        `Ticket.save` adding the column back, a check-in would never move the
        stamp and a second steward's screen would never learn about it.
        """
        stamp = self.attendees().json()['data']['asked_at']
        self.first.status = 'checked_in'
        self.first.checked_in_at = timezone.now()
        self.first.checked_in_gate = 'Main'
        self.first.save(update_fields=['status', 'checked_in_at',
                                       'checked_in_gate'])

        data = self.attendees(since=stamp).json()['data']
        self.assertEqual([r['code'] for r in data['attendees']],
                         ['VT-DELTA001'])
        self.assertEqual(data['attendees'][0]['status'], 'checked_in')

    def test_a_real_check_in_through_the_endpoint_moves_the_stamp(self):
        """The same thing again, through the actual door rather than a save."""
        stamp = self.attendees().json()['data']['asked_at']
        res = self.client.post(
            '/event/ticket/VT-DELTA001/check-in/',
            data={'gate': 'Main'}, content_type='application/json',
            **_auth(self.steward))
        self.assertEqual(res.status_code, 200, res.content)

        data = self.attendees(since=stamp).json()['data']
        self.assertEqual(data['returned'], 1)

    def test_the_totals_are_the_whole_event_not_the_delta(self):
        """The number that must never be a by-product of what was sent.

        `count` and `checked_in` used to be `len(rows)` and a sum over them,
        which was right only while every ticket came down. A delta returning
        one changed row would otherwise tell an organiser that one ticket was
        sold, which is exactly the headcount they cannot get wrong.
        """
        Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-DELTA003', price_vc=0,
            attendee_name='Second', attendee_email='second@vent.test')
        stamp = self.attendees().json()['data']['asked_at']
        Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-DELTA004', price_vc=0,
            attendee_name='Third', attendee_email='third@vent.test')

        data = self.attendees(since=stamp).json()['data']
        self.assertEqual(data['returned'], 1)
        self.assertEqual(data['count'], 3)

    def test_an_unreadable_stamp_is_ignored_rather_than_refused(self):
        """A door that stops answering because a clock is odd is worse."""
        res = self.attendees(since='not-a-date')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['returned'], 1)


class LeanIsSmaller(DeltaFixture):
    """The door's version of the list.

    Describing each row's checkout answers turns field ids into the organiser's
    own labels, and across a thousand rows that is most of the cost of the
    payload. A door refreshing every few seconds does not read them.
    """

    def test_lean_drops_the_answers(self):
        full = self.attendees().json()['data']['attendees'][0]
        lean = self.attendees(lean=1).json()['data']['attendees'][0]
        self.assertIn('answers', full)
        self.assertEqual(lean['answers'], [])

    def test_lean_keeps_everything_the_door_reads(self):
        """Lean is smaller, not different. The door reads one shape."""
        lean = self.attendees(lean=1).json()['data']['attendees'][0]
        for key in ('code', 'attendee_name', 'attendee_email',
                    'attendee_phone', 'tier', 'status', 'checked_in_at',
                    'checked_in_gate', 'self_check_in'):
            self.assertIn(key, lean, key)

    def test_lean_is_actually_smaller_on_the_wire(self):
        import json
        full = len(json.dumps(self.attendees().json()))
        lean = len(json.dumps(self.attendees(lean=1).json()))
        self.assertLessEqual(lean, full)
