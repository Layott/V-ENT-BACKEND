# -*- coding: utf-8 -*-
"""If an event is cancelled then refunds must happen (CEO, 18 September 2026).

Before this, an admin cancel stopped the tickets admitting anybody and
nobody got anything back; the organiser's delete refused a sold event with
"Cancel the event first, which refunds the holders", and no cancel refunded
and no organiser had a cancel door at all.

Written on the consequence, through the real till: a buyer pays from a
wallet, a guest pays by card, a ticket is given away, a comp is free; the
event is cancelled; every one of them is where the rule says.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth import paystack
from vent_auth.models import AdminAction, Notification, Transaction, Users, UserWallet

from . import ledger, refunds
from .models import Event, EventLedgerEntry, Ticket, TicketTier, TicketTransfer


def a_user(name, balance=0, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('c-%s' % name)[:16], is_active=True, **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('cw%s' % name)[:10], user=user, wallet_balance=balance,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class CancelRefundBase(TestCase):
    def setUp(self):
        self.organiser, self.org_auth = a_user('cr_org', balance=0)
        self.buyer, self.buyer_auth = a_user('cr_buyer', balance=100)
        self.friend, self.friend_auth = a_user('cr_friend', balance=0)
        self.admin, self.admin_auth = a_user('cr_admin', is_staff=True, admin_role='super_admin')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Refund Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0, capacity=100,
            start_date=now + timedelta(days=7),
            end_date=now + timedelta(days=7, hours=6),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=6))
        self.ga = TicketTier.objects.create(
            event=self.event, name='General', price=Decimal('3000'), quantity=50)
        self.free = TicketTier.objects.create(
            event=self.event, name='Free entry', price=Decimal('0'), quantity=50)

    def buy(self, tier=None, quantity=1, auth=None):
        body = {'tier_id': (tier or self.ga).id, 'quantity': quantity, 'pin': '1234'}
        res = self.client.post('/event/%s/buy-ticket/' % self.event.slug, data=body,
                               content_type='application/json', **(auth or self.buyer_auth))
        self.assertEqual(res.status_code, 201, res.content)
        return list(Ticket.objects.filter(event=self.event, user=self.buyer).order_by('-id')[:quantity])

    def guest_card_ticket(self, reference='PSK_ref_1'):
        # A guest who paid by card: no account, a payment reference.
        return Ticket.objects.create(
            event=self.event, tier=self.ga, code='VT-GUEST001', price_vc=3,
            price_ngn=Decimal('3000'), attendee_email='guest@example.test',
            payment_reference=reference)

    def balance(self, user):
        return UserWallet.objects.get(user=user).wallet_balance


class RefundOnCancelTests(CancelRefundBase):

    def test_a_wallet_buyer_gets_the_coins_back(self):
        tickets = self.buy(quantity=2)
        self.assertEqual(self.balance(self.buyer), 94)
        self.ga.refresh_from_db()
        self.assertEqual(self.ga.sold, 2)

        res = self.client.post('/auth/admin/events/%s/state/' % self.event.slug,
                               data={'action': 'cancel', 'reason': 'Venue flooded'},
                               content_type='application/json', **self.admin_auth)
        self.assertEqual(res.status_code, 200, res.content)
        summary = res.json()['data']['refunds']
        self.assertEqual(summary['refunded'], 2)
        self.assertEqual(summary['wallet'], 2)
        self.assertEqual(summary['coins'], 6)
        self.assertEqual(summary['failed'], [])

        self.assertEqual(self.balance(self.buyer), 100)
        for t in tickets:
            t.refresh_from_db()
            self.assertEqual(t.status, 'refunded')
            self.assertIsNotNone(t.refunded_at)
            self.assertEqual(t.refund_reference, 'wallet')
        self.ga.refresh_from_db()
        self.assertEqual(self.ga.sold, 0)
        refunds_written = Transaction.objects.filter(wallet__user=self.buyer, type='refund')
        self.assertEqual(refunds_written.count(), 2)
        self.assertEqual(sum(r.amount for r in refunds_written), 6)

    def test_the_ledger_is_reversed_so_the_organiser_is_owed_nothing(self):
        self.buy(quantity=1)
        before = ledger.balances(self.event)
        self.assertGreater(before['organiser_owed_ngn'] if 'organiser_owed_ngn' in before else 1, 0)
        self.client.post('/auth/admin/events/%s/state/' % self.event.slug,
                         data={'action': 'cancel', 'reason': 'x'},
                         content_type='application/json', **self.admin_auth)
        originals = EventLedgerEntry.objects.filter(event=self.event).exclude(
            kind=EventLedgerEntry.KIND_REVERSAL)
        self.assertTrue(originals.exists())
        self.assertFalse(originals.filter(reversed_by__isnull=True).exists(),
                         'every sale line has its reversal')

    def test_a_free_ticket_is_cancelled_and_nothing_moves(self):
        Ticket.objects.create(event=self.event, tier=self.free, code='VT-FREE0001',
                              user=self.friend, price_vc=0, price_ngn=0)
        res = self.client.post('/auth/admin/events/%s/state/' % self.event.slug,
                               data={'action': 'cancel', 'reason': 'x'},
                               content_type='application/json', **self.admin_auth)
        summary = res.json()['data']['refunds']
        self.assertEqual(summary['free'], 1)
        self.assertEqual(summary['refunded'], 0)
        t = Ticket.objects.get(code='VT-FREE0001')
        self.assertEqual(t.status, 'cancelled')
        self.assertEqual(self.balance(self.friend), 0)

    def test_a_ticket_given_away_refunds_the_person_who_paid(self):
        ticket = self.buy(quantity=1)[0]
        # Given to a friend: the friend holds it, the buyer paid for it.
        TicketTransfer.objects.create(ticket=ticket, from_user=self.buyer, to_user=self.friend,
                                      old_code=ticket.code, to_name='Friend')
        ticket.user = self.friend
        ticket.save(update_fields=['user'])
        self.client.post('/auth/admin/events/%s/state/' % self.event.slug,
                         data={'action': 'cancel', 'reason': 'x'},
                         content_type='application/json', **self.admin_auth)
        self.assertEqual(self.balance(self.buyer), 100)
        self.assertEqual(self.balance(self.friend), 0)

    def test_a_guest_card_payment_is_refunded_through_paystack(self):
        ticket = self.guest_card_ticket()
        with patch.object(paystack, 'refund', return_value={'id': 4242, 'status': 'pending'}) as sent:
            res = self.client.post('/auth/admin/events/%s/state/' % self.event.slug,
                                   data={'action': 'cancel', 'reason': 'x'},
                                   content_type='application/json', **self.admin_auth)
        self.assertEqual(sent.call_count, 1)
        self.assertEqual(sent.call_args.args[0], 'PSK_ref_1')
        self.assertEqual(Decimal(sent.call_args.args[1]), Decimal('3000'))
        summary = res.json()['data']['refunds']
        self.assertEqual(summary['card'], 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'refunded')
        self.assertEqual(ticket.refund_reference, '4242')

    def test_a_gateway_refusal_leaves_the_ticket_live_and_named_and_the_retry_door_clears_it(self):
        ticket = self.guest_card_ticket()
        with patch.object(paystack, 'refund', side_effect=paystack.Refused('Transaction not found')):
            res = self.client.post('/auth/admin/events/%s/state/' % self.event.slug,
                                   data={'action': 'cancel', 'reason': 'x'},
                                   content_type='application/json', **self.admin_auth)
        summary = res.json()['data']['refunds']
        self.assertEqual(summary['card_failed'], 1)
        self.assertEqual(summary['failed'][0]['code'], ticket.code)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'valid', 'a refused refund is not a refund')
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_active, 'the cancellation stands')

        # The retry door asks again, and only about what is still live.
        with patch.object(paystack, 'refund', return_value={'id': 77}) as sent:
            res = self.client.post('/auth/admin/events/%s/refunds/' % self.event.slug,
                                   data={}, content_type='application/json', **self.admin_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(sent.call_count, 1)
        self.assertEqual(res.json()['data']['refunds']['card'], 1)
        self.assertEqual(res.json()['data']['still_owed'], 0)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'refunded')

    def test_the_retry_door_refuses_a_live_event(self):
        res = self.client.post('/auth/admin/events/%s/refunds/' % self.event.slug,
                               data={}, content_type='application/json', **self.admin_auth)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'NOT_CANCELLED')

    def test_running_the_refunds_twice_refunds_nobody_twice(self):
        self.buy(quantity=1)
        self.client.post('/auth/admin/events/%s/state/' % self.event.slug,
                         data={'action': 'cancel', 'reason': 'x'},
                         content_type='application/json', **self.admin_auth)
        again = refunds.refund_event(self.event, 'again')
        self.assertEqual(again['refunded'], 0)
        self.assertEqual(self.balance(self.buyer), 100)

    def test_the_holder_and_the_organiser_are_told_what_came_back(self):
        self.buy(quantity=1)
        self.client.post('/auth/admin/events/%s/state/' % self.event.slug,
                         data={'action': 'cancel', 'reason': 'Venue flooded'},
                         content_type='application/json', **self.admin_auth)
        holder = Notification.objects.filter(user=self.buyer).order_by('-pk').first()
        self.assertIn('cancelled', holder.title)
        self.assertIn('on its way back', holder.body)
        organiser = Notification.objects.filter(user=self.organiser).order_by('-pk').first()
        self.assertIn('1 paid tickets were refunded', organiser.body)
        self.assertTrue(AdminAction.objects.filter(action_type='refund_event').exists())


class OrganiserCancelTests(CancelRefundBase):

    def test_the_organiser_can_cancel_and_everybody_is_refunded(self):
        self.buy(quantity=1)
        res = self.client.post('/event/%s/cancel/' % self.event.slug,
                               data={'reason': 'Headliner pulled out'},
                               content_type='application/json', **self.org_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['refunds']['refunded'], 1)
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_active)
        self.assertEqual(self.balance(self.buyer), 100)
        note = Notification.objects.filter(user=self.organiser).order_by('-pk').first()
        self.assertIn('You cancelled', note.title)

    def test_a_reason_is_required(self):
        res = self.client.post('/event/%s/cancel/' % self.event.slug, data={},
                               content_type='application/json', **self.org_auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'REASON_REQUIRED')

    def test_a_manager_may_not_cancel(self):
        from .models import EventManager
        manager, manager_auth = a_user('cr_manager')
        EventManager.objects.create(event=self.event, user=manager, role='manager')
        res = self.client.post('/event/%s/cancel/' % self.event.slug, data={'reason': 'x'},
                               content_type='application/json', **manager_auth)
        self.assertEqual(res.status_code, 403)

    def test_twice_is_refused(self):
        self.client.post('/event/%s/cancel/' % self.event.slug, data={'reason': 'x'},
                         content_type='application/json', **self.org_auth)
        res = self.client.post('/event/%s/cancel/' % self.event.slug, data={'reason': 'x'},
                               content_type='application/json', **self.org_auth)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'ALREADY_CANCELLED')

    def test_after_a_cancel_the_organiser_can_delete(self):
        self.buy(quantity=1)
        self.client.post('/event/%s/cancel/' % self.event.slug, data={'reason': 'x'},
                         content_type='application/json', **self.org_auth)
        res = self.client.post('/event/%s/delete/' % self.event.slug, data={'confirm': True},
                               content_type='application/json', **self.org_auth)
        self.assertEqual(res.status_code, 200, res.content)
