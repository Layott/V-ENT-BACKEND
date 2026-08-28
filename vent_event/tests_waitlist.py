"""The queue for a sold-out event.

Built in the DICE shape: the return valve that makes a face-value-only policy
workable, not a way to capture demand. Somebody whose plans change has a way out
that is not a resale site.

The tests are mostly about the clock. An offer without an expiry means one
person who stops reading their email freezes the queue behind them for ever, and
that is the failure that only shows up weeks later.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users, UserWallet

from .models import Event, Ticket, TicketTier, WaitlistEntry
from .views_waitlist import OFFER_HOURS, offer_next


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('w-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('ww%s' % name)[:10], user=user, wallet_balance=0)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class WaitlistTests(TestCase):
    def setUp(self):
        self.organiser, self.organiser_auth = a_user('wl_org')
        self.first, self.first_auth = a_user('wl_first')
        self.second, self.second_auth = a_user('wl_second')
        self.third, self.third_auth = a_user('wl_third')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Waitlist Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0, capacity=2,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=2)

    def sell_out(self):
        for i in range(2):
            Ticket.objects.create(
                event=self.event, tier=self.tier, user=self.organiser,
                code='VT-WL%s' % i, price_vc=0)
        self.tier.sold = 2
        self.tier.save(update_fields=['sold'])

    def url(self, suffix=''):
        return '/event/%s/waitlist/%s' % (self.event.event_id, suffix)

    def join(self, auth):
        return self.client.post(self.url(), data={},
                                content_type='application/json', **auth)

    # ------------------------------------------------------------- joining

    def test_joining_a_sold_out_event(self):
        self.sell_out()
        res = self.join(self.first_auth)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['data']['entry']['position'], 1)

    def test_joining_an_event_with_tickets_left_is_refused(self):
        """A queue for something you can simply buy is a confusing thing to
        offer."""
        res = self.join(self.first_auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'NOT_SOLD_OUT')

    def test_joining_twice_does_not_take_two_places(self):
        """The first thing anybody would try."""
        self.sell_out()
        self.join(self.first_auth)
        self.join(self.first_auth)
        self.assertEqual(
            WaitlistEntry.objects.filter(event=self.event).count(), 1)

    def test_the_queue_is_in_the_order_people_joined(self):
        self.sell_out()
        self.join(self.first_auth)
        self.join(self.second_auth)
        third = self.join(self.third_auth)
        self.assertEqual(third.json()['data']['entry']['position'], 3)

    def test_leaving_takes_you_off(self):
        self.sell_out()
        self.join(self.first_auth)
        res = self.client.delete(self.url(), **self.first_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(
            WaitlistEntry.objects.get(event=self.event).status, 'left')

    def test_coming_back_puts_you_at_the_back(self):
        """Not at the place you gave up."""
        self.sell_out()
        self.join(self.first_auth)
        self.join(self.second_auth)
        self.client.delete(self.url(), **self.first_auth)
        again = self.join(self.first_auth)
        self.assertEqual(again.json()['data']['entry']['position'], 2)

    # ----------------------------------------------------------- the offer

    def test_a_returned_ticket_is_offered_to_the_first_in_the_queue(self):
        """The whole mechanism. A ticket comes back and goes to the queue at
        face value, rather than onto a resale site."""
        self.sell_out()
        self.join(self.first_auth)
        self.join(self.second_auth)

        # Somebody returns a ticket.
        Ticket.objects.filter(code='VT-WL0').update(status='refunded')
        self.tier.sold = 1
        self.tier.save(update_fields=['sold'])

        offered = offer_next(self.event)
        self.assertEqual(len(offered), 1)
        self.assertEqual(offered[0].user_id, self.first.user_id)

        entry = WaitlistEntry.objects.get(user=self.first)
        self.assertEqual(entry.status, 'offered')
        self.assertIsNotNone(entry.offer_expires_at)

    def test_two_returns_do_not_produce_three_offers(self):
        self.sell_out()
        for auth in (self.first_auth, self.second_auth, self.third_auth):
            self.join(auth)

        Ticket.objects.filter(code='VT-WL0').update(status='refunded')
        self.tier.sold = 1
        self.tier.save(update_fields=['sold'])

        offer_next(self.event, how_many=5)
        self.assertEqual(
            WaitlistEntry.objects.filter(status='offered').count(), 1)

    def test_an_offer_that_expires_passes_to_the_next_person(self):
        """Without the clock, one person who stops reading their email freezes
        the queue behind them for ever."""
        self.sell_out()
        self.join(self.first_auth)
        self.join(self.second_auth)

        Ticket.objects.filter(code='VT-WL0').update(status='refunded')
        self.tier.sold = 1
        self.tier.save(update_fields=['sold'])
        offer_next(self.event)

        # Wind the clock past the window.
        WaitlistEntry.objects.filter(user=self.first).update(
            offer_expires_at=timezone.now() - timedelta(minutes=1))

        res = self.client.get(self.url('all/'), **self.organiser_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['expired_just_now'], 1)
        self.assertEqual(
            WaitlistEntry.objects.get(user=self.first).status, 'missed')

    def test_the_offer_window_is_hours_not_minutes(self):
        """Long enough to see a notification and act."""
        self.assertGreaterEqual(OFFER_HOURS, 1)

    def test_somebody_holding_an_offer_can_buy_into_a_sold_out_event(self):
        """Otherwise the offer is unusable: the event is sold out by
        definition, which is why they are in the queue."""
        self.sell_out()
        self.join(self.first_auth)

        Ticket.objects.filter(code='VT-WL0').update(status='refunded')
        self.tier.sold = 1
        self.tier.save(update_fields=['sold'])
        offer_next(self.event)

        res = self.client.post(
            '/event/%s/buy-ticket/' % self.event.event_id,
            data={'tier_id': self.tier.id, 'quantity': 1},
            content_type='application/json', **self.first_auth)
        self.assertIn(res.status_code, (200, 201), res.content)
        self.assertEqual(
            WaitlistEntry.objects.get(user=self.first).status, 'taken')

    def test_somebody_without_an_offer_still_cannot(self):
        self.sell_out()
        res = self.client.post(
            '/event/%s/buy-ticket/' % self.event.event_id,
            data={'tier_id': self.tier.id, 'quantity': 1},
            content_type='application/json', **self.second_auth)
        self.assertEqual(res.status_code, 409, res.content)

    # ------------------------------------------------------------ reading

    def test_i_can_see_where_i_am(self):
        self.sell_out()
        self.join(self.first_auth)
        res = self.client.get(self.url('mine/'), **self.first_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['data']['on_the_list'])
        self.assertEqual(res.json()['data']['entry']['position'], 1)

    def test_somebody_not_on_the_list_is_told_so_rather_than_erroring(self):
        res = self.client.get(self.url('mine/'), **self.second_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.json()['data']['on_the_list'])

    def test_the_organiser_sees_the_queue(self):
        self.sell_out()
        self.join(self.first_auth)
        self.join(self.second_auth)
        data = self.client.get(self.url('all/'), **self.organiser_auth).json()['data']
        self.assertEqual(data['counts']['waiting'], 2)
        self.assertEqual([r['user'] for r in data['waitlist']],
                         ['wl_first', 'wl_second'])

    def test_a_stranger_cannot_see_the_queue(self):
        res = self.client.get(self.url('all/'), **self.first_auth)
        self.assertEqual(res.status_code, 403, res.content)
