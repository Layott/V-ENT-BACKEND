"""A self check-in is not attendance, and the numbers must say so.

CEO, 7 September 2026:

    "someone that admiotted themslves doesnt mean they are checkedin by the
    organizer, just means that maybe they want to show and announce to their
    followers and frienfds on the platform that they are at this event. The
    ticket scanning or ticket code entering is still the baseline for a proper
    check in that the person actally came."

Both kinds write `status='checked_in'`, so every count that filtered on that
status alone reported somebody sitting at home as having walked through the
door. The door screen showed that total under the word "Checked in", which is
the number an organiser reads as the headcount.

These tests pin the three figures apart. They are cheap and they are the only
thing standing between the vocabulary and the next person who writes
`filter(status='checked_in').count()` because it reads like the obvious thing.
"""
import uuid

from django.test import TestCase
from django.utils import timezone

from . import attendance
from .models import Event, Ticket, TicketTier
from vent_auth.models import Users


def a_user(name):
    return Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        is_active=True)


class AttendanceCountsTests(TestCase):
    def setUp(self):
        self.owner = a_user('att_owner')
        self.event = Event.objects.create(
            name='Counting Night %s' % uuid.uuid4().hex[:4],
            creator=self.owner, event_type='physical', desc='x',
            entry_fee=0,
            reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            event_date=timezone.localdate(),
            start_time='10:00', end_time='18:00')
        # A ticket needs a tier: the column is NOT NULL. Copied from the
        # fixture tests_door_summary already uses rather than invented, so
        # these rows are the shape the real table holds.
        self.tier = TicketTier.objects.create(
            event=self.event, name='Standard', price=0, quantity=500,
            day=timezone.localdate())

    def _ticket(self, status='valid', gate=None):
        return Ticket.objects.create(
            event=self.event, tier=self.tier,
            code='VT-%s' % uuid.uuid4().hex[:8].upper(),
            status=status,
            checked_in_gate=gate or '',
            attendee_email='a@b.test')

    def test_a_self_check_in_is_not_verified(self):
        """The whole point. Both write checked_in; only one is attendance."""
        self._ticket('checked_in', 'main gate')
        self._ticket('checked_in', attendance.SELF_GATE)

        counts = attendance.counts(Ticket.objects.filter(event=self.event))
        self.assertEqual(counts['verified'], 1)
        self.assertEqual(counts['self_reported'], 1)
        self.assertEqual(counts['total'], 2)

    def test_a_check_in_with_no_gate_recorded_is_still_verified(self):
        """Staff typed a code without naming a gate. Somebody was still there.

        This is the case that makes `verified` a NOT-self test rather than an
        is-door test: an empty gate is the common shape from the code entry
        box, and treating it as unverified would have thrown away most of a
        real door's numbers.
        """
        self._ticket('checked_in', '')
        counts = attendance.counts(Ticket.objects.filter(event=self.event))
        self.assertEqual(counts['verified'], 1)
        self.assertEqual(counts['self_reported'], 0)

    def test_tickets_that_never_checked_in_are_counted_in_none_of_the_three(self):
        self._ticket('valid')
        self._ticket('refunded')
        counts = attendance.counts(Ticket.objects.filter(event=self.event))
        self.assertEqual(counts, {'verified': 0, 'self_reported': 0, 'total': 0})

    def test_the_three_always_add_up(self):
        for gate in ('gate a', attendance.SELF_GATE, '', attendance.SELF_GATE):
            self._ticket('checked_in', gate)
        self._ticket('valid')

        counts = attendance.counts(Ticket.objects.filter(event=self.event))
        self.assertEqual(counts['verified'] + counts['self_reported'],
                         counts['total'])
        self.assertEqual(counts['verified'], 2)
        self.assertEqual(counts['self_reported'], 2)


class OneDefinitionTests(TestCase):
    """SELF_GATE was written out in two files. A marker meaning "not evidence"
    is precisely the string that must not differ between what writes it and
    what reads it."""

    def test_every_module_uses_the_same_marker(self):
        from . import views_door, views_self_check_in
        self.assertIs(views_door.SELF_GATE, attendance.SELF_GATE)
        self.assertIs(views_self_check_in.SELF_GATE, attendance.SELF_GATE)
