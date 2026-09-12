# -*- coding: utf-8 -*-
"""How many people actually came.

CEO, 6 September 2026: "because we could not check in poeople we cannot count
how many people actually showed up for the event."

The count has to come from the database rather than from whatever the door page
happens to be holding, because the page holds a delta and the answer must not
depend on how much of the list was downloaded.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .models import Event, EventManager, TicketTier, Ticket
from .tests_door_lookup import a_user, _auth


class SummaryFixture(TestCase):
    def setUp(self):
        self.organiser = a_user('ds_organiser')
        self.steward = a_user('ds_steward')
        self.stranger = a_user('ds_stranger')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Headcount Probe', creator=self.organiser,
            event_type='physical', desc='Who came.', entry_fee=0,
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(hours=5),
            reg_start_date=now - timedelta(days=2),
            reg_end_date=now + timedelta(hours=4),
        )
        EventManager.objects.create(event=self.event, user=self.steward,
                                    role='door')
        self.day_one = TicketTier.objects.create(
            event=self.event, name='Day 1', price=0, quantity=500,
            day=now.date())
        self.day_two = TicketTier.objects.create(
            event=self.event, name='Day 2', price=0, quantity=500,
            day=(now + timedelta(days=1)).date())

    def a_ticket(self, code, tier=None, gate=None, status='valid'):
        ticket = Ticket.objects.create(
            event=self.event, tier=tier or self.day_one, code=code,
            price_vc=0, attendee_name=code, status=status)
        if gate is not None:
            ticket.status = 'checked_in'
            ticket.checked_in_at = timezone.now()
            ticket.checked_in_gate = gate
            ticket.save(update_fields=['status', 'checked_in_at',
                                       'checked_in_gate'])
        return ticket

    def summary(self, user=None):
        return self.client.get('/event/%d/door-summary/' % self.event.event_id,
                               **_auth(user or self.organiser))


class TheHeadcount(SummaryFixture):

    def test_sold_and_admitted_are_counted_apart(self):
        self.a_ticket('VT-SUM00001', gate='Main')
        self.a_ticket('VT-SUM00002')
        self.a_ticket('VT-SUM00003')

        data = self.summary().json()['data']
        self.assertEqual(data['sold'], 3)
        self.assertEqual(data['admitted'], 1)
        self.assertEqual(data['not_admitted'], 2)

    def test_the_gates_are_broken_out(self):
        self.a_ticket('VT-SUM00010', gate='Main')
        self.a_ticket('VT-SUM00011', gate='Main')
        self.a_ticket('VT-SUM00012', gate='Side')

        data = self.summary().json()['data']
        self.assertEqual(data['by_gate'], {'Main': 2, 'Side': 1})

    def test_self_check_in_is_separated_from_the_door(self):
        """The CEO was explicit that the two must be distinguishable."""
        self.a_ticket('VT-SUM00020', gate='Main')
        self.a_ticket('VT-SUM00021', gate='self')

        data = self.summary().json()['data']
        self.assertEqual(data['admitted'], 2)
        self.assertEqual(data['at_the_door'], 1)
        self.assertEqual(data['self_admitted'], 1)

    def test_the_days_are_broken_out(self):
        self.a_ticket('VT-SUM00030', tier=self.day_one, gate='Main')
        self.a_ticket('VT-SUM00031', tier=self.day_two, gate='Main')
        self.a_ticket('VT-SUM00032', tier=self.day_two, gate='Main')

        by_day = self.summary().json()['data']['by_day']
        self.assertEqual(by_day[self.day_one.day.isoformat()], 1)
        self.assertEqual(by_day[self.day_two.day.isoformat()], 2)

    def test_the_tiers_are_broken_out(self):
        self.a_ticket('VT-SUM00040', tier=self.day_one, gate='Main')
        self.a_ticket('VT-SUM00041', tier=self.day_two, gate='Main')

        by_tier = self.summary().json()['data']['by_tier']
        self.assertEqual(by_tier, {'Day 1': 1, 'Day 2': 1})

    def test_refunded_and_cancelled_are_not_sold(self):
        """A refunded ticket is not somebody who failed to turn up."""
        self.a_ticket('VT-SUM00050', gate='Main')
        self.a_ticket('VT-SUM00051', status='refunded')
        self.a_ticket('VT-SUM00052', status='cancelled')

        data = self.summary().json()['data']
        self.assertEqual(data['sold'], 1)
        self.assertEqual(data['not_admitted'], 0)
        self.assertEqual(data['refunded'], 1)
        self.assertEqual(data['cancelled'], 1)

    def test_an_empty_door_answers_zero_rather_than_failing(self):
        data = self.summary().json()['data']
        self.assertEqual(data['sold'], 0)
        self.assertEqual(data['admitted'], 0)
        self.assertEqual(data['by_gate'], {})

    def test_a_gate_nobody_named_still_counts(self):
        """The scanner sends no gate when a steward did not type one.

        Dropping those would under-count the door, which is the one number this
        endpoint exists to get right.
        """
        self.a_ticket('VT-SUM00060', gate='')
        data = self.summary().json()['data']
        self.assertEqual(data['admitted'], 1)
        self.assertEqual(data['by_gate'], {'unnamed': 1})
        self.assertEqual(data['at_the_door'], 1)

    def test_door_staff_may_read_the_numbers(self):
        self.assertEqual(self.summary(self.steward).status_code, 200)

    def test_a_stranger_may_not(self):
        self.assertEqual(self.summary(self.stranger).status_code, 403)

    def test_signed_out_is_401(self):
        res = self.client.get('/event/%d/door-summary/' % self.event.event_id)
        self.assertEqual(res.status_code, 401)

    def test_a_slug_addresses_it_too(self):
        res = self.client.get('/event/%s/door-summary/' % self.event.slug,
                              **_auth(self.organiser))
        self.assertEqual(res.status_code, 200)


class TheNumberThatWasMissing(SummaryFixture):
    """The event that prompted all of this, in miniature.

    1422 tickets, one check-in. The point of the test is that the summary tells
    the truth about that rather than hiding it: an organiser reading it sees
    1 admitted and 1421 not, which is the fact they needed on the night.
    """

    def test_a_door_that_barely_ran_reports_honestly(self):
        self.a_ticket('VT-REAL0001', gate='Main')
        for n in range(2, 12):
            self.a_ticket('VT-REAL%04d' % n)

        data = self.summary().json()['data']
        self.assertEqual(data['sold'], 11)
        self.assertEqual(data['admitted'], 1)
        self.assertEqual(data['not_admitted'], 10)
