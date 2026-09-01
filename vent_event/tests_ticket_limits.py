"""A limit per ticket type, a limit per day, and a limit across the whole event.

CEO, 1 September: "if there is several different days or types of ticket, the
option to set this for each ticket type and day should be available. for all
tickets and days at once also."

The rule these tests exist to pin down is that the three scopes **stack**. An
organiser who sets "one VIP each" and then sets "four per day" has said both
things, and a buyer holding a VIP is still refused a second one however much
room the day has. The alternative reading - the narrower scope replacing the
wider - would mean setting a per-type rule quietly switched off the event-wide
one, which is a rule disappearing because somebody edited a different rule.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users, UserWallet

from .models import Event, EventDayLimit, Ticket, TicketTier


def a_user(name, balance=0):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('l-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('lw%s' % name)[:10], user=user, wallet_balance=balance,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ScopedLimitTests(TestCase):
    """A three day convention selling two types on each day."""

    def setUp(self):
        self.organiser, self.auth = a_user('tlimA')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Three Day Con', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=7),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))
        self.friday = (now + timedelta(days=5)).date()
        self.saturday = (now + timedelta(days=6)).date()

        self.fri_standard = TicketTier.objects.create(
            event=self.event, name='Friday Standard', price=Decimal('0'),
            quantity=100, day=self.friday, day_label='Day 1')
        self.fri_vip = TicketTier.objects.create(
            event=self.event, name='Friday VIP', price=Decimal('0'),
            quantity=100, day=self.friday, day_label='Day 1')
        self.sat_standard = TicketTier.objects.create(
            event=self.event, name='Saturday Standard', price=Decimal('0'),
            quantity=100, day=self.saturday, day_label='Day 2')

    def buy(self, tier, email='ada@example.test', quantity=1):
        return self.client.post(
            '/event/%s/guest-buy/' % self.event.event_id,
            data={'tier_id': tier.id, 'quantity': quantity, 'email': email},
            content_type='application/json')

    # ------------------------------------------------------------- per type

    def test_a_type_limit_refuses_a_second_of_that_type(self):
        self.fri_vip.max_tickets_per_email = 1
        self.fri_vip.save()

        self.assertEqual(self.buy(self.fri_vip).status_code, 201)
        res = self.buy(self.fri_vip)
        self.assertEqual(res.status_code, 409, res.json())
        body = res.json()
        self.assertEqual(body['code'], 'EMAIL_LIMIT_TIER')
        # The refusal names the type, so the buyer knows which rule caught them
        # and that the rest of the event is still open to them.
        self.assertEqual(body['data']['name'], 'Friday VIP')
        self.assertEqual(body['data']['limit'], 1)
        self.assertEqual(body['data']['already'], 1)

    def test_a_type_limit_leaves_the_other_types_alone(self):
        self.fri_vip.max_tickets_per_email = 1
        self.fri_vip.save()

        self.assertEqual(self.buy(self.fri_vip).status_code, 201)
        # Same address, different type: the VIP rule has nothing to say.
        self.assertEqual(self.buy(self.fri_standard).status_code, 201)
        self.assertEqual(Ticket.objects.filter(event=self.event).count(), 2)

    # -------------------------------------------------------------- per day

    def test_a_day_limit_counts_across_the_types_on_that_day(self):
        """The point of a day limit: two types, one day, one number."""
        EventDayLimit.objects.create(event=self.event, day=self.friday,
                                     max_tickets_per_email=2)

        self.assertEqual(self.buy(self.fri_standard).status_code, 201)
        self.assertEqual(self.buy(self.fri_vip).status_code, 201)

        res = self.buy(self.fri_standard)
        self.assertEqual(res.status_code, 409, res.json())
        self.assertEqual(res.json()['code'], 'EMAIL_LIMIT_DAY')
        self.assertEqual(res.json()['data']['name'], self.friday.isoformat())

    def test_a_day_limit_does_not_reach_another_day(self):
        EventDayLimit.objects.create(event=self.event, day=self.friday,
                                     max_tickets_per_email=1)

        self.assertEqual(self.buy(self.fri_standard).status_code, 201)
        self.assertEqual(self.buy(self.fri_standard).status_code, 409)
        # Saturday is a different day and has no rule of its own.
        self.assertEqual(self.buy(self.sat_standard).status_code, 201)

    # ---------------------------------------------------------- they stack

    def test_the_event_rule_still_applies_under_a_looser_type_rule(self):
        """A per-type rule does not switch the event-wide one off."""
        self.event.max_tickets_per_email = 1
        self.event.save()
        self.fri_standard.max_tickets_per_email = 5
        self.fri_standard.save()

        self.assertEqual(self.buy(self.fri_standard).status_code, 201)
        res = self.buy(self.fri_standard)
        self.assertEqual(res.status_code, 409, res.json())
        self.assertEqual(res.json()['code'], 'EMAIL_LIMIT_REACHED')

    def test_the_narrowest_rule_is_the_one_named(self):
        """Every rule is checked; the most specific one explains the refusal."""
        self.event.max_tickets_per_email = 10
        self.event.save()
        EventDayLimit.objects.create(event=self.event, day=self.friday,
                                     max_tickets_per_email=5)
        self.fri_vip.max_tickets_per_email = 1
        self.fri_vip.save()

        self.assertEqual(self.buy(self.fri_vip).status_code, 201)
        res = self.buy(self.fri_vip)
        self.assertEqual(res.json()['code'], 'EMAIL_LIMIT_TIER')

    def test_a_quantity_that_would_break_a_rule_is_refused_up_front(self):
        self.fri_standard.max_tickets_per_email = 2
        self.fri_standard.save()
        res = self.buy(self.fri_standard, quantity=3)
        self.assertEqual(res.status_code, 409, res.json())
        self.assertEqual(Ticket.objects.filter(event=self.event).count(), 0)
        # They hold none and are still refused, which is a different sentence
        # from "you already have two" and therefore a different code. A
        # translation reading "you already have 0" would be nonsense.
        self.assertEqual(res.json()['code'], 'EMAIL_LIMIT_TIER_MAX')
        self.assertEqual(res.json()['data']['already'], 0)

    def test_no_rule_anywhere_means_no_refusal(self):
        for _ in range(4):
            self.assertEqual(self.buy(self.fri_standard).status_code, 201)

    def test_a_refunded_ticket_stops_counting(self):
        self.fri_vip.max_tickets_per_email = 1
        self.fri_vip.save()
        self.assertEqual(self.buy(self.fri_vip).status_code, 201)
        Ticket.objects.filter(event=self.event).update(status='refunded')
        # They hold nothing, so they may buy again. A record that admits nobody
        # is not a reason to refuse somebody.
        self.assertEqual(self.buy(self.fri_vip).status_code, 201)


class LimitsEndpointTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('tlimB')
        self.other, self.other_auth = a_user('tlimC')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Limits API', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=7),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))
        self.friday = (now + timedelta(days=5)).date()
        self.saturday = (now + timedelta(days=6)).date()
        # The event runs to +7d, so there is a third day with no type on it.
        # That day still has to be offered, which is the whole point of reading
        # the days off the event rather than off the types.
        self.sunday = (now + timedelta(days=7)).date()
        self.standard = TicketTier.objects.create(
            event=self.event, name='Standard', price=Decimal('0'),
            quantity=50, day=self.friday, day_label='Day 1')
        self.vip = TicketTier.objects.create(
            event=self.event, name='VIP', price=Decimal('0'), quantity=10,
            day=self.saturday, day_label='Day 2')
        self.url = '/event/%s/email-limits/' % self.event.event_id

    def post(self, body, auth=None):
        return self.client.post(self.url, data=body,
                                content_type='application/json',
                                **(auth if auth is not None else self.auth))

    def test_it_reports_every_scope_and_the_days_that_exist(self):
        res = self.client.get(self.url, **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        data = res.json()['data']
        self.assertIsNone(data['event'])
        self.assertEqual(len(data['tiers']), 2)
        self.assertEqual(
            [d['day'] for d in data['days']],
            [self.friday.isoformat(), self.saturday.isoformat(),
             self.sunday.isoformat()])
        self.assertTrue(data['has_days'])
        # The day carries the label the organiser gave it, not a bare date.
        self.assertEqual(data['days'][0]['label'], 'Day 1')

    def test_one_type_at_a_time(self):
        res = self.post({'tiers': {str(self.vip.id): 1}})
        self.assertEqual(res.status_code, 200, res.json())
        self.vip.refresh_from_db()
        self.standard.refresh_from_db()
        self.assertEqual(self.vip.max_tickets_per_email, 1)
        self.assertIsNone(self.standard.max_tickets_per_email)

    def test_all_types_at_once(self):
        """The whole reason this exists: not typing the same number six times."""
        res = self.post({'all_tiers': 2})
        self.assertEqual(res.status_code, 200, res.json())
        self.vip.refresh_from_db()
        self.standard.refresh_from_db()
        self.assertEqual(self.vip.max_tickets_per_email, 2)
        self.assertEqual(self.standard.max_tickets_per_email, 2)

    def test_all_days_at_once(self):
        res = self.post({'all_days': 3})
        self.assertEqual(res.status_code, 200, res.json())
        self.assertEqual(
            sorted(EventDayLimit.objects.filter(event=self.event)
                   .values_list('day', flat=True)),
            [self.friday, self.saturday, self.sunday])
        self.assertEqual(
            set(EventDayLimit.objects.filter(event=self.event)
                .values_list('max_tickets_per_email', flat=True)), {3})

    def test_clearing_a_scope_removes_only_that_rule(self):
        self.post({'event': 4, 'all_tiers': 2, 'all_days': 3})
        self.post({'all_tiers': None})

        self.event.refresh_from_db()
        self.vip.refresh_from_db()
        self.assertEqual(self.event.max_tickets_per_email, 4)
        self.assertIsNone(self.vip.max_tickets_per_email)
        self.assertEqual(EventDayLimit.objects.filter(event=self.event).count(), 3)

    def test_clearing_all_days_removes_the_rows(self):
        self.post({'all_days': 3})
        self.post({'all_days': None})
        self.assertEqual(EventDayLimit.objects.filter(event=self.event).count(), 0)

    def test_a_day_with_no_ticket_type_is_still_offered(self):
        """The days come from the event, not from the types that exist yet.

        Reading them off `TicketTier.day` meant a day nothing was sold for was
        never offered, so an organiser could not assign a type to it. That is
        how a type called "General Admission Day 2" ended up carrying no date
        and showing the buyer nothing at all.
        """
        res = self.client.get(self.url, **self.auth)
        days = [d['day'] for d in res.json()['data']['days']]
        self.assertIn(self.sunday.isoformat(), days)
        self.assertFalse(
            self.event.ticket_tiers.filter(day=self.sunday).exists())

    def test_the_days_are_numbered_for_the_screen(self):
        res = self.client.get(self.url, **self.auth)
        self.assertEqual([d['n'] for d in res.json()['data']['days']], [1, 2, 3])

    def test_a_one_day_event_offers_no_per_day_section(self):
        """One day, and a per-day rule is the event-wide rule under a new name."""
        self.event.end_date = self.event.start_date
        self.event.save(update_fields=['end_date'])
        res = self.client.get(self.url, **self.auth)
        self.assertFalse(res.json()['data']['has_days'])

    def test_the_tier_list_carries_the_days_so_a_date_can_be_corrected(self):
        res = self.client.get(
            '/event/%s/tiers/' % self.event.event_id, **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        self.assertEqual([d['n'] for d in res.json()['data']['days']], [1, 2, 3])

    def test_zero_is_refused_rather_than_read_as_no_limit(self):
        """Somebody typing 0 thinks they are allowing none, not allowing all."""
        res = self.post({'event': 0})
        self.assertEqual(res.status_code, 400, res.json())
        self.assertEqual(res.json()['code'], 'INVALID_NUMBER')
        self.event.refresh_from_db()
        self.assertIsNone(self.event.max_tickets_per_email)

    def test_a_day_the_event_does_not_sell_for_is_refused(self):
        res = self.post({'days': {'2030-01-01': 2}})
        self.assertEqual(res.status_code, 404, res.json())
        self.assertEqual(EventDayLimit.objects.filter(event=self.event).count(), 0)

    def test_a_type_on_another_event_is_refused(self):
        res = self.post({'tiers': {'99999': 2}})
        self.assertEqual(res.status_code, 404, res.json())

    def test_somebody_else_cannot_set_them(self):
        res = self.post({'event': 1}, auth=self.other_auth)
        self.assertEqual(res.status_code, 403, res.json())
        self.event.refresh_from_db()
        self.assertIsNone(self.event.max_tickets_per_email)

    def test_the_saved_rule_is_the_one_the_checkout_enforces(self):
        """The endpoint and the till have to agree, or the screen is decoration."""
        self.post({'tiers': {str(self.vip.id): 1}})
        buy = lambda: self.client.post(
            '/event/%s/guest-buy/' % self.event.event_id,
            data={'tier_id': self.vip.id, 'quantity': 1,
                  'email': 'ada@example.test'},
            content_type='application/json')
        self.assertEqual(buy().status_code, 201)
        self.assertEqual(buy().status_code, 409)


class TierDayUpdateTests(TestCase):
    """Setting a ticket type's day must not 500 after it has already saved.

    Found live on RIVALRY SERIES SEASON 2. `update_tier` assigned the date as a
    string, Django coerced it on the way to the database, and the instance in
    memory kept the string. The very next line serialised that instance and
    called `.isoformat()` on a `str`.

    So the write landed and the caller was told "Failed". They retried, the
    retry landed on a different ticket type, and the event finished with two
    types pointed at the same day. A 500 after a successful write is worse than
    a 500 instead of one, because it invites the retry that does the damage.
    """

    def setUp(self):
        self.organiser, self.auth = a_user('tdayA')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Two Day Con', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=6),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))
        self.day_two = (now + timedelta(days=6)).date()
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=Decimal('0'), quantity=50)

    def patch(self, body):
        return self.client.patch(
            '/event/%s/tiers/%s/' % (self.event.event_id, self.tier.id),
            data=body, content_type='application/json', **self.auth)

    def test_setting_a_day_answers_200_and_saves(self):
        res = self.patch({'day': self.day_two.isoformat(), 'day_label': 'Day 2'})
        self.assertEqual(res.status_code, 200, res.content[:400])
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.day, self.day_two)
        self.assertEqual(self.tier.day_label, 'Day 2')

    def test_the_response_carries_the_saved_day_as_a_date(self):
        """The serialiser calls .isoformat(); it must be given a real date."""
        res = self.patch({'day': self.day_two.isoformat(), 'day_label': 'Day 2'})
        self.assertEqual(res.json()['data']['tier']['day'],
                         self.day_two.isoformat())

    def test_clearing_the_day_makes_it_a_full_pass_again(self):
        self.patch({'day': self.day_two.isoformat(), 'day_label': 'Day 2'})
        res = self.patch({'day': '', 'day_label': ''})
        self.assertEqual(res.status_code, 200, res.content[:400])
        self.tier.refresh_from_db()
        self.assertIsNone(self.tier.day)

    def test_a_date_that_is_not_a_date_is_refused_cleanly(self):
        res = self.patch({'day': 'the fourth'})
        self.assertEqual(res.status_code, 400, res.content[:300])
        self.assertEqual(res.json()['code'], 'INVALID_DATE')
        self.tier.refresh_from_db()
        self.assertIsNone(self.tier.day)
