"""Holds, and the money an event has taken.

A hold removes tickets from sale without selling them: guest list, press, the
venue's own allocation. Without them an organiser buys their own tickets, which
corrupts the sales figures they then show a sponsor.

The tests that matter are the ones proving held tickets are genuinely
unavailable, and that releasing gives back only what has not been given away.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users, UserWallet

from . import availability
from .models import Event, EventReferral, Ticket, TicketHold, TicketTier


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('h-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class HoldTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('hold_organiser')
        self.stranger, self.stranger_auth = a_user('hold_stranger')
        self.buyer, self.buyer_auth = a_user('hold_buyer')
        UserWallet.objects.create(
            user_wallet_id='holdw', user=self.buyer, wallet_balance=0)
        now = timezone.now()
        self.event = Event.objects.create(
            name='Hold Probe', creator=self.organiser, event_type='physical',
            desc='A room.', entry_fee=0, capacity=100,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4),
        )
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=50)

    def url(self, suffix=''):
        return '/event/%s/holds/%s' % (self.event.event_id, suffix)

    def hold(self, **body):
        body.setdefault('name', 'Guest list')
        body.setdefault('quantity', 10)
        return self.client.post(self.url(), data=body,
                                content_type='application/json', **self.auth)

    # ------------------------------------------------------------- creating

    def test_a_hold_takes_tickets_off_sale_without_selling_them(self):
        before = availability.available(self.tier)
        res = self.hold(tier=self.tier.id, quantity=10)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(availability.available(self.tier), before - 10)
        # Nothing was sold. That is the difference from an organiser buying
        # their own tickets.
        self.assertEqual(self.event.tickets.count(), 0)
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.sold, 0)

    def test_a_hold_against_the_event_reduces_the_room(self):
        self.hold(quantity=90)
        self.assertEqual(availability.event_room(self.event), 10)

    def test_a_hold_needs_a_name(self):
        """So anybody reading the list knows who it is for."""
        res = self.hold(name='  ')
        self.assertEqual(res.status_code, 400, res.content)

    def test_holding_more_than_exists_is_refused(self):
        res = self.hold(tier=self.tier.id, quantity=500)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'NOT_ENOUGH_TO_HOLD')

    def test_a_tier_from_another_event_is_refused(self):
        other = Event.objects.create(
            name='Elsewhere', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=timezone.now() + timedelta(days=1),
            end_date=timezone.now() + timedelta(days=2),
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timedelta(hours=12))
        theirs = TicketTier.objects.create(
            event=other, name='Theirs', price=0, quantity=10)
        res = self.hold(tier=theirs.id)
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_stranger_cannot_hold_tickets(self):
        res = self.client.post(self.url(), data={'name': 'Mine', 'quantity': 5},
                               content_type='application/json',
                               **self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)

    # ------------------------------------------------------ what it blocks

    def test_held_tickets_cannot_be_bought(self):
        """The whole point of a hold."""
        self.tier.quantity = 10
        self.tier.save(update_fields=['quantity'])
        self.hold(tier=self.tier.id, quantity=10)

        res = self.client.post(
            '/event/%s/buy-ticket/' % self.event.event_id,
            data={'tier_id': self.tier.id, 'quantity': 1},
            content_type='application/json', **self.buyer_auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'SOLD_OUT')

    # ------------------------------------------------------------ releasing

    def test_releasing_puts_them_back_on_sale(self):
        res = self.hold(tier=self.tier.id, quantity=10)
        hold_id = res.json()['data']['hold']['id']
        before = availability.available(self.tier)

        res = self.client.post(self.url('%s/release/' % hold_id), data={},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['returned'], 10)
        self.assertEqual(availability.available(self.tier), before + 10)

    def test_releasing_twice_is_refused(self):
        hold_id = self.hold(quantity=5).json()['data']['hold']['id']
        self.client.post(self.url('%s/release/' % hold_id), data={},
                         content_type='application/json', **self.auth)
        again = self.client.post(self.url('%s/release/' % hold_id), data={},
                                 content_type='application/json', **self.auth)
        self.assertEqual(again.status_code, 409, again.content)

    # ------------------------------------------------------------- issuing

    def test_issuing_turns_held_tickets_into_real_ones(self):
        """The guest list arriving."""
        hold_id = self.hold(tier=self.tier.id, quantity=5).json()['data']['hold']['id']
        res = self.client.post(
            self.url('%s/issue/' % hold_id),
            data={'names': ['Amara Obi', 'Chidi Okeke']},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 201, res.content)

        tickets = Ticket.objects.filter(event=self.event)
        self.assertEqual(tickets.count(), 2)
        self.assertEqual({t.attendee_name for t in tickets},
                         {'Amara Obi', 'Chidi Okeke'})
        # Free, and that is what makes it different from the organiser buying
        # their own and then having to explain the revenue.
        self.assertTrue(all(t.price_vc == 0 for t in tickets))

    def test_issuing_more_than_is_held_is_refused(self):
        hold_id = self.hold(quantity=2).json()['data']['hold']['id']
        res = self.client.post(
            self.url('%s/issue/' % hold_id),
            data={'names': ['A', 'B', 'C']},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'NOT_ENOUGH_HELD')

    def test_releasing_after_issuing_returns_only_what_is_left(self):
        """Tickets already given to somebody are theirs. A release that
        un-issued them would take a ticket off somebody holding it."""
        hold_id = self.hold(tier=self.tier.id, quantity=10).json()['data']['hold']['id']
        self.client.post(self.url('%s/issue/' % hold_id),
                         data={'names': ['A', 'B', 'C']},
                         content_type='application/json', **self.auth)
        res = self.client.post(self.url('%s/release/' % hold_id), data={},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.json()['data']['returned'], 7)
        self.assertEqual(Ticket.objects.filter(event=self.event).count(), 3)

    def test_issuing_needs_names(self):
        hold_id = self.hold(quantity=5).json()['data']['hold']['id']
        res = self.client.post(self.url('%s/issue/' % hold_id), data={'names': []},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)

    # ------------------------------------------- the same mechanism as before

    def test_an_influencer_allocation_is_counted_as_a_hold(self):
        """It is the same idea with a different name: tickets reserved for
        somebody to sell. Counted by the same function so the two cannot
        disagree."""
        EventReferral.objects.create(
            event=self.event, name='An influencer', code='INF1',
            allocation=20, sold=5, is_active=True)
        # 20 reserved, 5 already sold through them, so 15 still held.
        self.assertEqual(availability.event_room(self.event), 100 - 15)


class MoneyTests(TestCase):
    """What the event took, what came back, and what is owed.

    There was no per-event view of any of it, which makes settling with a venue
    or a sponsor a manual count of rows.
    """

    def setUp(self):
        self.organiser, self.auth = a_user('money_organiser')
        self.stranger, self.stranger_auth = a_user('money_stranger')
        self.buyer, _ = a_user('money_buyer')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Money Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=3),
            end_date=now + timedelta(days=3, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=2))
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=5000, quantity=100)

    def ticket(self, code, status='valid', vc=50, ngn=5000):
        return Ticket.objects.create(
            event=self.event, tier=self.tier, user=self.buyer,
            code=code, status=status, price_vc=vc, price_ngn=ngn)

    def money(self, auth=None):
        return self.client.get('/event/%s/money/' % self.event.event_id,
                               **(auth if auth is not None else self.auth))

    def test_it_reports_what_was_taken(self):
        self.ticket('VT-M1')
        self.ticket('VT-M2')
        data = self.money().json()['data']
        self.assertEqual(data['taken']['count'], 2)
        self.assertEqual(data['taken']['vc'], 100)

    def test_a_refund_moves_from_taken_to_returned(self):
        self.ticket('VT-M1')
        self.ticket('VT-M2', status='refunded')
        data = self.money().json()['data']
        self.assertEqual(data['taken']['count'], 1)
        self.assertEqual(data['returned']['count'], 1)

    def test_owed_reconciles_by_construction(self):
        """Taken minus returned. None of the three is stored anywhere that
        could drift from the others."""
        self.ticket('VT-M1')
        self.ticket('VT-M2')
        self.ticket('VT-M3', status='refunded')
        data = self.money().json()['data']
        self.assertEqual(
            data['owed']['vc'], data['taken']['vc'] - data['returned']['vc'])
        # Two sold at 50 is 100 taken; one refunded at 50 goes back; 50 owed.
        # A refund reduces what the organiser is owed, because the money went
        # back to the buyer.
        self.assertEqual(data['taken']['vc'], 100)
        self.assertEqual(data['returned']['vc'], 50)
        self.assertEqual(data['owed']['vc'], 50)

    def test_free_tickets_count_as_people_and_not_as_money(self):
        self.ticket('VT-M1')
        self.ticket('VT-FREE', vc=0, ngn=0)
        data = self.money().json()['data']
        self.assertEqual(data['taken']['count'], 2)
        self.assertEqual(data['taken']['vc'], 50)
        self.assertEqual(data['free_tickets'], 1)

    def test_it_breaks_down_by_ticket_type(self):
        vip = TicketTier.objects.create(
            event=self.event, name='VIP', price=20000, quantity=10)
        self.ticket('VT-M1')
        Ticket.objects.create(event=self.event, tier=vip, user=self.buyer,
                              code='VT-V1', price_vc=200, price_ngn=20000)
        rows = {r['name']: r for r in self.money().json()['data']['by_tier']}
        self.assertEqual(rows['General']['count'], 1)
        self.assertEqual(rows['VIP']['vc'], 200)

    def test_checked_in_is_counted_separately_from_sold(self):
        self.ticket('VT-M1', status='checked_in')
        self.ticket('VT-M2')
        data = self.money().json()['data']
        self.assertEqual(data['taken']['count'], 2)
        self.assertEqual(data['checked_in'], 1)

    def test_a_stranger_cannot_see_the_money(self):
        res = self.money(auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)
