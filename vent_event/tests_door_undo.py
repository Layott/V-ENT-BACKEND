# -*- coding: utf-8 -*-
"""Taking a check-in back.

CEO, 6 September 2026: "should also be able to undo check ins."

A gate is a place where mistakes are made: the wrong phone is scanned, somebody
is admitted on the wrong day, or Check in is pressed twice on a slow connection.
Without an undo the headcount is wrong afterwards and nobody can correct it,
which defeats the point of counting at all.
"""
from django.test import TestCase
from django.utils import timezone

from .models import Ticket, DoorLookup
from .tests_door_lookup import DoorFixture, _auth


class UndoingACheckIn(DoorFixture):

    def admit(self, code='VT-NWKL9Z2K', gate='Main'):
        res = self.client.post(
            '/event/ticket/%s/check-in/' % code,
            data={'gate': gate}, content_type='application/json',
            **_auth(self.steward))
        self.assertEqual(res.status_code, 200, res.content)
        return res

    def undo(self, code='VT-NWKL9Z2K', user=None):
        return self.client.post(
            '/event/ticket/%s/undo-check-in/' % code,
            data={}, content_type='application/json',
            **_auth(user or self.steward))

    def test_a_check_in_can_be_taken_back(self):
        self.admit()
        res = self.undo()
        self.assertEqual(res.status_code, 200, res.content)

        self.ginnie.refresh_from_db()
        self.assertEqual(self.ginnie.status, 'valid')
        self.assertIsNone(self.ginnie.checked_in_at)
        self.assertEqual(self.ginnie.checked_in_gate, '')
        self.assertIsNone(self.ginnie.checked_in_by)

    def test_the_headcount_goes_back_down(self):
        """The number is the whole point, so it has to follow."""
        self.admit()
        before = self.client.get('/event/%d/door-summary/' % self.event.event_id,
                                 **_auth(self.organiser)).json()['data']
        self.assertEqual(before['admitted'], 1)

        self.undo()
        after = self.client.get('/event/%d/door-summary/' % self.event.event_id,
                                **_auth(self.organiser)).json()['data']
        self.assertEqual(after['admitted'], 0)
        self.assertEqual(after['not_admitted'], 2)

    def test_they_can_be_admitted_again_afterwards(self):
        """An undo that leaves somebody unable to get in is not an undo."""
        self.admit()
        self.undo()
        again = self.admit()
        self.assertEqual(again.status_code, 200)
        self.ginnie.refresh_from_db()
        self.assertEqual(self.ginnie.status, 'checked_in')

    def test_undoing_is_recorded_with_who_did_it(self):
        """"I was already checked in" is the next thing somebody says."""
        self.admit()
        self.undo()
        row = DoorLookup.objects.get(kind='undo')
        self.assertEqual(row.term, 'VT-NWKL9Z2K')
        self.assertEqual(row.asked_by, self.steward)
        self.assertEqual(row.ticket, self.ginnie)
        # The gate they were admitted at, kept on the undo row, because it is
        # gone from the ticket the moment this succeeds.
        self.assertEqual(row.gate, 'Main')

    def test_a_ticket_that_is_not_checked_in_answers_by_code(self):
        res = self.undo()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'NOT_CHECKED_IN')

    def test_two_stewards_undoing_at_once_is_not_an_error_state(self):
        """The same situation as two pressing check in, and the same answer."""
        self.admit()
        first = self.undo()
        second = self.undo()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.ginnie.refresh_from_db()
        self.assertEqual(self.ginnie.status, 'valid')

    def test_an_unknown_code_is_404(self):
        res = self.undo(code='VT-ZZZZZZZZ')
        self.assertEqual(res.status_code, 404)

    def test_door_staff_may_undo_without_running_the_event(self):
        """An undo that waits for the organiser does not happen at a gate.

        The mistake being corrected was made ten seconds ago by the person
        standing there, with a queue behind them.
        """
        self.admit()
        self.assertEqual(self.undo(user=self.steward).status_code, 200)

    def test_a_stranger_cannot_undo(self):
        self.admit()
        res = self.undo(user=self.stranger)
        self.assertEqual(res.status_code, 403)
        self.ginnie.refresh_from_db()
        self.assertEqual(self.ginnie.status, 'checked_in')

    def test_signed_out_cannot_undo(self):
        self.admit()
        res = self.client.post('/event/ticket/VT-NWKL9Z2K/undo-check-in/',
                               data={}, content_type='application/json')
        self.assertEqual(res.status_code, 401)
        self.ginnie.refresh_from_db()
        self.assertEqual(self.ginnie.status, 'checked_in')

    def test_a_self_check_in_can_be_undone_too(self):
        """Somebody who admitted themselves by mistake, or too early."""
        self.ginnie.status = 'checked_in'
        self.ginnie.checked_in_at = timezone.now()
        self.ginnie.checked_in_gate = 'self'
        self.ginnie.save(update_fields=['status', 'checked_in_at',
                                        'checked_in_gate'])
        self.assertEqual(self.undo().status_code, 200)
        self.ginnie.refresh_from_db()
        self.assertEqual(self.ginnie.status, 'valid')

    def test_the_undo_shows_up_in_the_delta(self):
        """A second steward's screen has to learn the person is out again."""
        self.admit()
        stamp = self.client.get('/event/%d/attendees/' % self.event.event_id,
                                **_auth(self.steward)).json()['data']['asked_at']
        self.undo()
        data = self.client.get(
            '/event/%d/attendees/?since=%s' % (self.event.event_id, stamp),
            **_auth(self.steward)).json()['data']
        codes = [r['code'] for r in data['attendees']]
        self.assertIn('VT-NWKL9Z2K', codes)
        self.assertEqual(data['attendees'][codes.index('VT-NWKL9Z2K')]['status'],
                         'valid')


class TheDoorLogTellsTheKindsApart(DoorFixture):

    def test_searches_lookups_and_undos_are_separable(self):
        self.client.get('/event/%d/door-search/?q=Ginnie' % self.event.event_id,
                        **_auth(self.steward))
        self.client.get('/event/ticket/VT-NWKL9Z2K/lookup/', **_auth(self.steward))
        self.client.post('/event/ticket/VT-NWKL9Z2K/check-in/', data={},
                         content_type='application/json', **_auth(self.steward))
        self.client.post('/event/ticket/VT-NWKL9Z2K/undo-check-in/', data={},
                         content_type='application/json', **_auth(self.steward))

        self.assertEqual(DoorLookup.objects.filter(kind='search').count(), 1)
        self.assertEqual(DoorLookup.objects.filter(kind='lookup').count(), 1)
        self.assertEqual(DoorLookup.objects.filter(kind='undo').count(), 1)

    def test_an_undo_does_not_pollute_the_miss_count(self):
        """Misses are what tell an organiser a door is in trouble.

        Counting undos among them would make a busy, well-run gate look like a
        failing one.
        """
        self.client.get('/event/%d/door-search/?q=Nobody' % self.event.event_id,
                        **_auth(self.steward))
        self.client.post('/event/ticket/VT-NWKL9Z2K/check-in/', data={},
                         content_type='application/json', **_auth(self.steward))
        self.client.post('/event/ticket/VT-NWKL9Z2K/undo-check-in/', data={},
                         content_type='application/json', **_auth(self.steward))

        data = self.client.get('/event/%d/door-lookups/' % self.event.event_id,
                               **_auth(self.organiser)).json()['data']
        self.assertEqual(data['misses'], 1)
