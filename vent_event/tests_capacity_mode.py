"""What the venue capacity COUNTS is a decision, not something to infer.

CEO, 2 September 2026:

    "IF I SET 5000 FOR TWO DIFFERENT DAYS AND THE VENUE CAPACITY IS 5000, IT
    OBVIOUSLY MEANS AFTER DAY 1 PROGRAMME, PEOPLE WILL LEAVE AND THE NEW SET OF
    5000 WILL COME BACK ON THE 2ND DAY AND I CANT SELL ANOTHER 5000 TICKETS, SO
    I SHOULD HAVE THE OPTION SET IT HOW I WANT."

Right on both counts, and the second half is the important one. A 5000-seat
venue running a daily programme sells 10000 tickets across two days, because
those are different people in the same chairs. A residential weekend where the
same people stay is bounded by 5000 however long it runs.

Guessing is expensive in both directions: guess TOTAL and half the tickets
never go on sale, guess PER_DAY and the room is oversold. So it is set by the
organiser, and defaults to per_day because that is what a venue capacity
usually means.

The second class here is the warning. The console told an organiser "your
ticket types offer 10000 tickets but the venue is set to 5000" for a setup that
was exactly right. Telling somebody their correct configuration is broken is
worse than saying nothing, because the next real warning gets ignored.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_event import availability
from vent_event.models import Event, Ticket, TicketTier


def an_event(organiser, capacity=5000, mode='per_day'):
    return Event.objects.create(
        name='Capacity Mode Event %s' % timezone.now().timestamp(),
        creator=organiser, event_type='physical', desc='x',
        entry_fee=Decimal('0'),
        reg_start_date=timezone.now(), reg_end_date=timezone.now(),
        event_date=timezone.now().date(),
        start_time=timezone.now().time(), end_time=timezone.now().time(),
        capacity=capacity, capacity_mode=mode)


class CapacityModeTests(TestCase):
    def setUp(self):
        self.organiser = Users.objects.create(
            username='modeOrg', email='mo@vent.test', is_active=True)
        self.event = an_event(self.organiser)
        self.day1 = timezone.now().date()
        self.day2 = self.day1 + timezone.timedelta(days=1)
        self.t1 = TicketTier.objects.create(
            event=self.event, name='Day 1', price=Decimal('0'),
            quantity=5000, day=self.day1)
        self.t2 = TicketTier.objects.create(
            event=self.event, name='Day 2', price=Decimal('0'),
            quantity=5000, day=self.day2)

    def sell(self, tier, n, tag):
        for i in range(n):
            Ticket.objects.create(
                event=self.event, tier=tier,
                attendee_email='%s%d@vent.test' % (tag, i),
                code='%s%06d' % (tag.upper()[:2], i), status='valid')
        tier.sold = n
        tier.save(update_fields=['sold'])

    def test_per_day_is_the_default(self):
        """What a venue capacity usually means."""
        self.assertEqual(self.event.capacity_mode, 'per_day')

    def test_per_day_sells_the_full_allocation_on_each_day(self):
        """The CEO's example: 5000 on Saturday and 5000 more on Sunday."""
        self.sell(self.t1, 5000, 'p1')
        self.assertEqual(availability.available(self.t1), 0)
        self.assertEqual(availability.available(self.t2), 5000)

    def test_total_bounds_the_whole_event(self):
        """A residential weekend. The same people stay, so day two sells
        nothing once the 5000 are in."""
        self.event.capacity_mode = 'total'
        self.event.save(update_fields=['capacity_mode'])
        self.sell(self.t1, 5000, 'q1')
        self.assertEqual(availability.available(self.t1), 0)
        self.assertEqual(availability.available(self.t2), 0)

    def test_total_still_sells_up_to_the_ceiling(self):
        self.event.capacity_mode = 'total'
        self.event.save(update_fields=['capacity_mode'])
        self.sell(self.t1, 3000, 'r1')
        self.assertEqual(availability.available(self.t2), 2000)

    def test_only_the_two_modes_exist(self):
        allowed = [c[0] for c in Event._meta.get_field('capacity_mode').choices]
        self.assertEqual(sorted(allowed), ['per_day', 'total'])


class OverCapacityWarningTests(TestCase):
    """The warning has to be judged the way this event counts."""

    def setUp(self):
        self.organiser = Users.objects.create(
            username='warnOrg', email='wo@vent.test', is_active=True,
            login_session_token='warn-token-01')
        self.organiser.login_session_created_at = timezone.now()
        self.organiser.save()
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.organiser.login_session_token}
        self.event = an_event(self.organiser)
        self.day1 = timezone.now().date()
        self.day2 = self.day1 + timezone.timedelta(days=1)

    def tier(self, name, quantity, day=None):
        return TicketTier.objects.create(
            event=self.event, name=name, price=Decimal('0'),
            quantity=quantity, day=day)

    def capacity_block(self):
        """Read it through the view, so the test exercises what the console
        actually receives rather than a function nothing calls."""
        res = self.client.get('/event/%s/tiers/' % self.event.slug, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']['capacity']

    def test_two_days_of_five_thousand_is_not_a_warning(self):
        """Exactly the setup the console wrongly flagged."""
        self.tier('Day 1', 5000, self.day1)
        self.tier('Day 2', 5000, self.day2)
        cap = self.capacity_block()
        self.assertFalse(cap['over_capacity'])
        self.assertEqual(cap['offered_worst_day'], 5000)
        self.assertEqual(cap['offered_by_tiers'], 10000)
        self.assertEqual(cap['mode'], 'per_day')

    def test_one_day_promising_more_than_the_room_is_a_warning(self):
        self.tier('Day 1 GA', 4000, self.day1)
        self.tier('Day 1 VIP', 2000, self.day1)
        cap = self.capacity_block()
        self.assertTrue(cap['over_capacity'])
        self.assertEqual(cap['offered_worst_day'], 6000)

    def test_a_full_pass_counts_towards_every_day(self):
        """Somebody with a weekend pass occupies a chair on both days, so it
        is added to each day rather than treated as a day of its own."""
        self.tier('Day 1', 4500, self.day1)
        self.tier('Day 2', 4500, self.day2)
        self.tier('Weekend pass', 1000)
        cap = self.capacity_block()
        self.assertEqual(cap['offered_worst_day'], 5500)
        self.assertTrue(cap['over_capacity'])

    def test_under_total_the_sum_is_what_counts(self):
        self.event.capacity_mode = 'total'
        self.event.save(update_fields=['capacity_mode'])
        self.tier('Day 1', 5000, self.day1)
        self.tier('Day 2', 5000, self.day2)
        cap = self.capacity_block()
        self.assertTrue(cap['over_capacity'])
        self.assertEqual(cap['offered_worst_day'], 10000)

    def test_an_uncapped_event_never_warns(self):
        self.event.capacity = None
        self.event.save(update_fields=['capacity'])
        self.tier('Day 1', 50000, self.day1)
        cap = self.capacity_block()
        self.assertFalse(cap['over_capacity'])
        self.assertIsNone(cap['capacity'])
