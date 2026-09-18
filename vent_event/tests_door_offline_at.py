# -*- coding: utf-8 -*-
"""A queued check-in carries WHEN they walked in.

CEO, 18 September 2026: "We also need to make Offline scanning of tickets very
possible and easy." The scanner already decided offline and queued the result;
what it sent when signal returned was `{gate, day}` and nothing else, so the
server stamped everybody from an outage as arriving at the moment the queue
flushed. A duplicate's "first used at 14:02" then named the outage's end, not
the person's arrival.

The check-in now takes an optional `at`. It is honoured only when it is a real
instant, in the past, and not older than a day: a phone with a wrong clock
cannot write the future, and a queue left over from last month cannot write
last month.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .models import Ticket
from .tests_door_lookup import DoorFixture, _auth


class OfflineScanKeepsItsOwnTime(DoorFixture):

    def _check_in(self, code, body):
        return self.client.post(
            '/event/ticket/%s/check-in/' % code, body,
            content_type='application/json', **_auth(self.steward))

    def test_a_past_at_is_stored_as_the_check_in_time(self):
        walked_in = timezone.now() - timedelta(hours=2, minutes=13)
        res = self._check_in(self.ginnie.code, {'gate': 'North', 'at': walked_in.isoformat()})
        self.assertEqual(res.status_code, 200, res.content)
        ticket = Ticket.objects.get(pk=self.ginnie.pk)
        self.assertEqual(ticket.status, 'checked_in')
        self.assertLess(abs((ticket.checked_in_at - walked_in).total_seconds()), 1)
        self.assertEqual(ticket.checked_in_gate, 'North')

    def test_a_future_at_is_ignored(self):
        """A phone whose clock runs fast must not write a check-in from the future."""
        ahead = timezone.now() + timedelta(minutes=30)
        before = timezone.now()
        res = self._check_in(self.ginnie.code, {'gate': 'North', 'at': ahead.isoformat()})
        self.assertEqual(res.status_code, 200, res.content)
        ticket = Ticket.objects.get(pk=self.ginnie.pk)
        self.assertGreaterEqual(ticket.checked_in_at, before)
        self.assertLessEqual(ticket.checked_in_at, timezone.now())

    def test_an_at_older_than_a_day_is_ignored(self):
        stale = timezone.now() - timedelta(days=3)
        before = timezone.now()
        res = self._check_in(self.riley.code, {'gate': 'North', 'at': stale.isoformat()})
        self.assertEqual(res.status_code, 200, res.content)
        ticket = Ticket.objects.get(pk=self.riley.pk)
        self.assertGreaterEqual(ticket.checked_in_at, before)

    def test_garbage_at_is_ignored_not_refused(self):
        """The queue must drain. A malformed stamp is not a reason to refuse
        the person who is already inside."""
        before = timezone.now()
        res = self._check_in(self.riley.code, {'gate': 'North', 'at': 'yesterday-ish'})
        self.assertEqual(res.status_code, 200, res.content)
        ticket = Ticket.objects.get(pk=self.riley.pk)
        self.assertEqual(ticket.status, 'checked_in')
        self.assertGreaterEqual(ticket.checked_in_at, before)

    def test_no_at_means_now(self):
        before = timezone.now()
        res = self._check_in(self.ginnie.code, {'gate': 'North'})
        self.assertEqual(res.status_code, 200, res.content)
        ticket = Ticket.objects.get(pk=self.ginnie.pk)
        self.assertGreaterEqual(ticket.checked_in_at, before)

    def test_the_duplicate_answer_names_the_offline_time(self):
        """The second scan says when the first was used. With the queue's own
        time honoured, that is when they walked in, not when signal came back."""
        walked_in = timezone.now() - timedelta(hours=1)
        self._check_in(self.ginnie.code, {'gate': 'North', 'at': walked_in.isoformat()})
        res = self._check_in(self.ginnie.code, {'gate': 'South'})
        self.assertEqual(res.status_code, 409, res.content)
        body = res.json()
        first = (body.get('data') or {}).get('ticket') or (body.get('data') or {})
        stamp = first.get('checked_in_at') or (first.get('first') or {}).get('at')
        self.assertTrue(stamp, body)
        self.assertTrue(str(stamp).startswith(walked_in.isoformat()[:16]), (stamp, walked_in))
