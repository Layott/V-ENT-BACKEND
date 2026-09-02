"""What the buy page shows and what the checkout allows must be the same number.

CEO, 2 September 2026, on a live event: "i says ticket sold out, why???" - the
page showed "4814 remaining" on one type and "This event is sold out" above the
button, and then: "these are the kind of bugs that will cost us greatly".

The live figures on RIVALRY SERIES SEASON 2:

    capacity   400
    sold       300
    held       100     (two 30-ticket holds, plus influencer allocations)
    event_room 0
    tiers      two of 5000, showing 4814 and 4886 "remaining"

Neither number was wrong about its own question. `serialize_tier` answered
"how many of this type are unsold" and the checkout answered "how many more
people will the venue take". The buyer was shown the first and judged by the
second.

`availability.available()` already existed and already returned the lower of
the two. The listing simply was not calling it. So these tests are not really
about arithmetic; they are about the two paths agreeing, which is the only
property that matters to somebody trying to pay.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_event import availability
from vent_event.models import Event, Ticket, TicketTier
from vent_event.views_tickets import serialize_tier


class ListingAgreesWithCheckoutTests(TestCase):
    def setUp(self):
        self.organiser = Users.objects.create(
            username='availOrg', email='ao@vent.test', is_active=True)
        self.event = Event.objects.create(
            name='Capacity Test Event', creator=self.organiser,
            event_type='physical', desc='x', entry_fee=Decimal('0'),
            reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            event_date=timezone.now().date(),
            start_time=timezone.now().time(), end_time=timezone.now().time(),
            capacity=400,
        )
        self.tier = TicketTier.objects.create(
            event=self.event, name='General Admission', price=Decimal('0'),
            quantity=5000)

    def sell(self, n):
        for i in range(n):
            Ticket.objects.create(
                event=self.event, tier=self.tier,
                attendee_email='buyer%d@vent.test' % i,
                code='CAP%05d' % i, status='valid')
        self.tier.sold = n
        self.tier.save(update_fields=['sold'])

    # ------------------------------------------------------------ the bug

    def test_the_listing_never_advertises_more_than_can_be_bought(self):
        """The exact shape of the live fault: a big tier behind a small venue."""
        self.sell(300)
        shown = serialize_tier(self.tier)['remaining']
        buyable = availability.available(self.tier)
        self.assertEqual(shown, buyable)
        self.assertEqual(shown, 100)   # 400 capacity - 300 sold, not 4700

    def test_a_full_venue_reads_as_sold_out_on_the_card_too(self):
        """So the card and the button cannot contradict each other."""
        self.sell(400)
        row = serialize_tier(self.tier)
        self.assertEqual(row['remaining'], 0)
        self.assertTrue(row['sold_out'])
        self.assertEqual(availability.event_room(self.event), 0)

    def test_the_card_says_which_ceiling_was_hit(self):
        """"Sold out" beside a type with 4600 unsold reads as a broken site.
        The reason lets the screen say the venue is full instead."""
        self.sell(400)
        self.assertEqual(serialize_tier(self.tier)['unavailable_reason'],
                         'venue_full')

    def test_a_genuinely_exhausted_type_says_so_instead(self):
        small = TicketTier.objects.create(
            event=self.event, name='VIP', price=Decimal('0'), quantity=2)
        small.sold = 2
        small.save(update_fields=['sold'])
        self.assertEqual(serialize_tier(small)['unavailable_reason'],
                         'tier_sold_out')

    # ------------------------------------------------------- what is kept

    def test_the_organiser_can_still_see_the_types_own_numbers(self):
        """The console shows "186 of 5000 sold". Flattening that to the venue
        ceiling would hide how the type itself is doing."""
        self.sell(300)
        row = serialize_tier(self.tier)
        self.assertEqual(row['quantity'], 5000)
        self.assertEqual(row['sold'], 300)
        self.assertEqual(row['tier_remaining'], 4700)

    def test_an_uncapped_event_is_bounded_only_by_its_types(self):
        """capacity None means no ceiling, and must not read as a ceiling of
        zero - that would report every type on an uncapped event as sold out."""
        self.event.capacity = None
        self.event.save(update_fields=['capacity'])
        self.sell(300)
        self.assertIsNone(availability.event_room(self.event))
        self.assertEqual(serialize_tier(self.tier)['remaining'], 4700)

    def test_holds_come_off_the_number_a_buyer_is_shown(self):
        """A held ticket is not for sale, so advertising it is the same
        overselling in a quieter form."""
        from vent_event.models import TicketHold
        self.sell(100)
        TicketHold.objects.create(event=self.event, quantity=50,
                                  name='Sponsor block')
        shown = serialize_tier(self.tier)['remaining']
        self.assertEqual(shown, availability.available(self.tier))
        self.assertEqual(shown, 250)   # 400 - 100 sold - 50 held

    def test_the_two_paths_agree_across_a_range_of_states(self):
        """The property, stated once: whatever the numbers, the listing and
        the checkout answer the same question."""
        for sold in (0, 1, 199, 399, 400):
            with self.subTest(sold=sold):
                Ticket.objects.filter(event=self.event).delete()
                self.sell(sold)
                self.assertEqual(serialize_tier(self.tier)['remaining'],
                                 availability.available(self.tier))


class CapacityIsPerDayTests(TestCase):
    """A venue that holds 400 holds 400 on each day, not 200 each.

    CEO, 2 September 2026: "And if the venue capacity was 400 even, it was set
    to two days."

    This is what actually made RIVALRY SERIES SEASON 2 report itself sold out.
    Day one had sold 186 and day two 114, and every one of those 300 was being
    counted against a single 400 meant for one room on one afternoon. Add the
    100 held and the event closed itself with 214 places free on day one and
    286 on day two.
    """

    def setUp(self):
        self.organiser = Users.objects.create(
            username='perDayOrg', email='pdo@vent.test', is_active=True)
        self.event = Event.objects.create(
            name='Two Day Event', creator=self.organiser,
            event_type='physical', desc='x', entry_fee=Decimal('0'),
            reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            event_date=timezone.now().date(),
            start_time=timezone.now().time(), end_time=timezone.now().time(),
            capacity=400,
        )
        self.day1 = timezone.now().date()
        self.day2 = self.day1 + timezone.timedelta(days=1)
        self.t1 = TicketTier.objects.create(
            event=self.event, name='General Admission Day 1',
            price=Decimal('0'), quantity=5000, day=self.day1, day_label='Day 1')
        self.t2 = TicketTier.objects.create(
            event=self.event, name='General Admission Day 2',
            price=Decimal('0'), quantity=5000, day=self.day2, day_label='Day 2')

    def sell(self, tier, n, tag):
        for i in range(n):
            Ticket.objects.create(
                event=self.event, tier=tier,
                attendee_email='%s%d@vent.test' % (tag, i),
                code='%s%05d' % (tag.upper()[:3], i), status='valid')
        tier.sold = n
        tier.save(update_fields=['sold'])

    def test_the_live_numbers_are_not_sold_out(self):
        """186 on day one and 114 on day two, against 400 a day."""
        self.sell(self.t1, 186, 'd1')
        self.sell(self.t2, 114, 'd2')
        self.assertEqual(availability.event_room(self.event, self.day1), 214)
        self.assertEqual(availability.event_room(self.event, self.day2), 286)
        self.assertGreater(availability.available(self.t1), 0)
        self.assertGreater(availability.available(self.t2), 0)
        self.assertFalse(serialize_tier(self.t1)['sold_out'])

    def test_one_full_day_does_not_close_the_other(self):
        self.sell(self.t1, 400, 'f1')
        self.assertEqual(availability.available(self.t1), 0)
        self.assertGreater(availability.available(self.t2), 0)

    def test_a_full_pass_takes_a_place_on_every_day(self):
        """Somebody holding a ticket with no day is in the room on all of
        them, so counting them against only one would oversell the others."""
        both = TicketTier.objects.create(
            event=self.event, name='Weekend pass', price=Decimal('0'),
            quantity=100)
        self.sell(both, 50, 'wk')
        self.assertEqual(availability.event_room(self.event, self.day1), 350)
        self.assertEqual(availability.event_room(self.event, self.day2), 350)

    def test_a_single_day_event_is_unchanged(self):
        """The common case must not move. With no day on the type, every
        ticket counts, which is the same answer as before."""
        plain = Event.objects.create(
            name='One Day', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=Decimal('0'),
            reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            event_date=timezone.now().date(),
            start_time=timezone.now().time(), end_time=timezone.now().time(),
            capacity=100)
        tier = TicketTier.objects.create(
            event=plain, name='GA', price=Decimal('0'), quantity=500)
        for i in range(100):
            Ticket.objects.create(event=plain, tier=tier,
                                  attendee_email='o%d@vent.test' % i,
                                  code='ONE%05d' % i, status='valid')
        tier.sold = 100
        tier.save(update_fields=['sold'])
        self.assertEqual(availability.event_room(plain), 0)
        self.assertEqual(availability.available(tier), 0)


class EverythingSetAtCreationCanBeChangedTests(TestCase):
    """Nothing an organiser chooses may be settable once and then frozen.

    CEO, 2 September 2026, on the venue capacity: "only shown when creating the
    event never shown when managing, what other options like that are there,
    when changing ticket quantity it should have come up."

    The honest answer to "what other options like that are there" is not a
    list I remember on the day; it is this test. It reads the model and fails
    on any column that is neither reachable through the edit endpoint nor
    listed below with a reason. Add a column and forget the edit path, and it
    fails here rather than in a support message six weeks later.

    Capacity itself was the worst of them, because it silently overrules every
    ticket type: an organiser set 5000 and could not see, anywhere on the
    console, the 400 that was actually deciding.
    """

    def test_no_organiser_setting_is_write_once(self):
        from vent_event.models import Event as EventModel

        editable = {
            # plain text and choices
            'name', 'desc', 'event_type', 'category', 'location', 'event_link',
            'banner_url', 'venue_name', 'map_link', 'directions',
            # when it happens, and when people may sign up
            'start_date', 'end_date', 'reg_start_date', 'reg_end_date',
            # the room, the money, the game
            'capacity', 'entry_fee', 'currency', 'game',
            'max_tickets_per_email',
            # the pin, and self check-in
            'latitude', 'longitude',
            'self_check_in', 'self_check_in_opens_minutes',
            # artwork, multipart
            'logo', 'banner',
            # published or not
            'is_active',
        }

        deliberately_fixed = {
            # identity and bookkeeping
            'event_id', 'slug', 'creator', 'created_at', 'last_updated',
            'interaction_count',
            # set through their own endpoints, with their own permission rules
            'organization', 'series',
            # admin only, never the organiser's to set
            'is_featured',
            # Legacy columns kept for old rows. start_date and end_date are
            # what the wizard and the console both write; editing these as
            # well would give one event two answers about when it happens.
            'event_date', 'start_time', 'end_time',
        }

        columns = {f.name for f in EventModel._meta.get_fields()
                   if getattr(f, 'concrete', False)}
        stranded = columns - editable - deliberately_fixed
        self.assertEqual(
            stranded, set(),
            'Settable at creation and never afterwards, and not listed as '
            'deliberately fixed: %s' % sorted(stranded))
